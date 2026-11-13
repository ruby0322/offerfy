"""Score one resume against one posting on OpenAI's /v1/decisions endpoint.

The only module that talks to /v1/decisions for matching. The reason it is
that endpoint and not a chat model: a chat model writing 92 is inventing a
number, while a `predicate` returning 0.78 is answering a question it was
asked.

One call per (resume, posting) pair, never one call carrying thirty postings.
A request answers one question about its *whole* input -- `layout._chosen_pages`
already paid for that lesson: asked to score the density of a two-page resume
whose first page was 99% full and whose second held six lines, the model
returned one document-wide 0.07. Hand it thirty postings and every score is
about the pile. Calls run concurrently behind a semaphore instead.

A refusal is its own answer type, not a low probability (the same rule as
`layout._vision_answers`). Reading a number off one would invent a confident
"no", and a posting scored 0 would then sit in the digest's pool as if it had
been judged. A refused pair is therefore dropped and listed in `refused`; it
is never given a score. Likewise an answer with no readable probabilities is
a provider fault, not a zero.

How `fit_score` is made, and why the endpoint's own `score` is ignored. The
`fit` rubric returns a probability for each of its four levels. The tier is the
most probable level. `fit_score` is the expectation of TIER_WEIGHTS over those
probabilities, normalised by their sum:

    fit_score = sum(weight[level] * p[level]) / sum(p[level])

with weight 1 for Strong fit, 2/3 for Good fit, 1/3 for Worth a look and 0 for
Not a fit.

The `score` field the endpoint also returns for the rubric is DELIBERATELY NOT
READ. The layout rubrics list their low level first (Airy, Flat), so that field
is plausibly a position counted up from the first level, whereas `fit` lists
Strong fit first: reading it risks a silent inversion, a number that looks
reasonable and means the opposite. This score is displayed to users beside its
tier, so it must not rest on a field whose direction nobody has verified. Do
not "simplify" the expectation into `answer["score"]`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.db import get_session_factory
from app.services.chat_messages import persist_usage
from app.services.layout import (
    DEFAULT_DECISIONS_MODEL,
    VISION_TIMEOUT,
    _error_detail,
    _score_questions,
    decisions_url,
)
from app.services.metering import metered, system_guard

logger = logging.getLogger(__name__)


def record_round(db, *, meter: Any, user: Any, facts: Any, event: dict[str, Any]) -> None:
    """Both halves of one billed round: the counter AND the telemetry row.

    Two writes with two durabilities, the pair the chat router has always
    written. Moving the counter without writing the row is what makes
    `app.services.usage.reconcile` report a gap on the admin overview for that
    window forever -- and a gap that is always there is an alarm nobody can
    read. Both digest purposes went out missing the second half; this is it.

    On a session of its own, for the reason `metering.Meter.record` gives: the
    caller here is a scheduled build that keeps ONE transaction per user, and
    telemetry that committed the caller's session would settle a digest it has
    not finished writing. Telemetry may be lost; the work may not.

    `resume_id` is the resume these matches were judged against, which is the
    only resume the digest has an opinion about.
    """
    meter.record(event)
    session = get_session_factory()()
    try:
        persist_usage(
            session,
            getattr(facts, "source_resume_id", None),
            event,
            str(event.get("purpose") or ""),
            user=user,
            guest=None,
        )
    finally:
        session.close()

FIT_RUBRIC = "fit"
PREDICATES = ("requirements_met", "seniority_fit", "location_fit", "direction_fit")

_PREDICATE_INSTRUCTIONS = {
    "requirements_met": "Does this person meet the technical requirements the posting states?",
    "seniority_fit": "Does the seniority this posting asks for match this person's experience?",
    "location_fit": "Would this posting's location or remote arrangement work for this person?",
    "direction_fit": (
        "Would this role move this person toward the direction their resume points "
        "in, rather than sideways?"
    ),
}

# Display tier -> weight in the 0..1 fit score; see the module docstring for
# the formula and for why the endpoint's own `score` field is not used.
TIER_WEIGHTS = {
    "Strong fit": 1.0,
    "Good fit": 2 / 3,
    "Worth a look": 1 / 3,
    "Not a fit": 0.0,
}

REQUIREMENTS_CHARS = 1500
MAX_FACT_ROLES = 6
MAX_FACT_SKILLS = 40

_REQUIREMENTS_HEADING = re.compile(
    r"(?im)^\W*(?:minimum |basic |preferred )?(?:requirements|qualifications|"
    r"what you(?:'|’)ll need|what we(?:'|’)re looking for|who you are)\b"
)

PREAMBLE = (
    "Below are the facts of one person's resume and one job posting. Judge only "
    "what is written; do not assume experience the resume does not state."
)


@dataclass(frozen=True)
class Scored:
    job_id: str
    tier: str
    fit_score: float
    components: dict[str, float]


@dataclass
class ScoreRun:
    scored: list[Scored] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


class ScoreFailed(Exception):
    """A provider fault for one pair. Carries the message for `ScoreRun.error`
    and whatever usage the response reported: a call that failed to parse or
    came back 4xx/5xx may still have been billed, and must still be recorded."""

    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


def facts_text(facts: Any) -> str:
    try:
        data = json.loads(facts.facts_json)
    except (AttributeError, TypeError, ValueError):
        raise ScoreFailed("Resume facts could not be read") from None
    if not isinstance(data, dict):
        raise ScoreFailed("Resume facts could not be read")
    lines: list[str] = []
    if data.get("headline"):
        lines.append(f"Headline: {data['headline']}")
    if data.get("years_experience") is not None:
        lines.append(f"Years of experience: {data['years_experience']}")
    if data.get("locations"):
        lines.append("Locations: " + ", ".join(map(str, data["locations"])))
    if data.get("skills"):
        lines.append("Skills: " + ", ".join(map(str, data["skills"][:MAX_FACT_SKILLS])))
    for role in (data.get("roles") or [])[:MAX_FACT_ROLES]:
        if not isinstance(role, dict):
            continue
        span = f"{role.get('start') or '?'} to {role.get('end') or '?'}"
        lines.append(f"Role: {role.get('title') or '?'} at {role.get('employer') or '?'} ({span})")
        lines.extend(f"- {h}" for h in (role.get("highlights") or [])[:3])
    return "\n".join(lines)


def requirements(description: str | None) -> str:
    text = (description or "").strip()
    match = _REQUIREMENTS_HEADING.search(text)
    if match:
        text = text[match.start() :]
    return text[:REQUIREMENTS_CHARS].strip()


def _posting_text(job: Any) -> str:
    return "\n".join(
        [
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location or 'not stated'}",
            "Requirements:",
            requirements(getattr(job, "description_text", "")),
        ]
    )


def _body(model: str, facts_text: str, job: Any) -> dict[str, Any]:
    questions = _score_questions([FIT_RUBRIC]) + [
        {"type": "predicate", "name": name, "instructions": _PREDICATE_INSTRUCTIONS[name]}
        for name in PREDICATES
    ]
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": PREAMBLE},
                    {"type": "input_text", "text": "Resume:\n" + facts_text},
                    {"type": "input_text", "text": "Posting:\n" + _posting_text(job)},
                ],
            }
        ],
        "questions": questions,
    }


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def _parse(job_id: str, data: Any) -> Scored | str:
    """A Scored, or the string "refused". Raises ScoreFailed on a malformed answer."""
    answers = {
        str(item.get("name")): item
        for item in (data.get("answers") if isinstance(data, dict) else None) or []
        if isinstance(item, dict)
    }
    # Refusal first, on any question: a pair the model declined to judge is
    # dropped whole, not scored from the questions it did answer.
    if any(item.get("type") == "refusal" for item in answers.values()):
        return "refused"
    fit = answers.get(FIT_RUBRIC)
    levels = fit.get("probabilities") if fit else None
    if not isinstance(levels, list) or not levels:
        raise ScoreFailed("Decisions returned no probabilities for the fit rubric")
    by_label = {
        level.get("label"): _probability(level.get("probability"))
        for level in levels
        if isinstance(level, dict)
    }
    if set(by_label) != set(TIER_WEIGHTS) or None in by_label.values():
        raise ScoreFailed("Decisions returned unreadable probabilities for the fit rubric")
    components: dict[str, float] = {}
    for name in PREDICATES:
        value = _probability((answers.get(name) or {}).get("probability"))
        if value is None:
            raise ScoreFailed(f"Decisions returned no probability for {name}")
        components[name] = value
    total = sum(by_label.values())
    if total <= 0:
        raise ScoreFailed("Decisions returned empty probabilities for the fit rubric")
    tier = max(TIER_WEIGHTS, key=lambda label: by_label[label])
    fit_score = sum(TIER_WEIGHTS[label] * p for label, p in by_label.items()) / total
    return Scored(job_id=job_id, tier=tier, fit_score=round(fit_score, 4), components=components)


async def _score_pair(
    client: httpx.AsyncClient, *, url: str, headers: dict, model: str, facts_text: str, job: Any
) -> tuple[Scored | str, Any]:
    """One billable Decisions call. Returns (Scored | "refused", usage dict).

    Usage is read before the answer is, so every `ScoreFailed` raised after a
    response arrived carries it.
    """
    try:
        resp = await client.post(url, headers=headers, json=_body(model, facts_text, job))
        try:
            data = resp.json()
        except ValueError:
            data = None
    except httpx.HTTPError as exc:
        logger.warning("decisions match transport failed: %s", exc.__class__.__name__)
        raise ScoreFailed("Decisions request failed") from None
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    if resp.status_code >= 400:
        detail = _error_detail(resp)
        logger.warning("decisions match failed status=%s detail=%s", resp.status_code, detail)
        raise ScoreFailed(detail, usage)
    if data is None:
        raise ScoreFailed("Decisions returned no JSON", usage)
    try:
        return _parse(str(job.id), data), usage
    except ScoreFailed as exc:
        exc.usage = usage
        raise


async def score_candidates(db, *, user, facts, jobs, concurrency: int = 4) -> ScoreRun:
    """Score each job against `facts`. Never raises on a provider fault.

    A fault sets `error` and leaves `scored` empty: a half-scored pool would
    rank the survivors against postings that were never judged. Once one pair
    has failed, pairs not yet started are skipped rather than paid for.
    """
    settings = get_settings()
    model = (settings.openai_decisions_model or DEFAULT_DECISIONS_MODEL).strip()
    run = ScoreRun(model=model)
    if not (settings.openai_api_key or "").strip():
        run.error = "llm_not_configured"
        return run

    # A job without an id, or a repeat of one, cannot be attributed to a
    # result, so it is dropped before it costs anything.
    seen: set[str] = set()
    candidates = []
    for job in jobs:
        job_id = getattr(job, "id", None)
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        candidates.append(job)

    try:
        resume_text = facts_text(facts)
    except ScoreFailed as exc:
        run.error = str(exc)
        return run

    url = decisions_url(settings.openai_base_url or "https://api.openai.com/v1")
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    semaphore = asyncio.Semaphore(max(1, concurrency))
    failure: list[str] = []
    results: dict[str, Scored | str] = {}
    input_tokens = 0
    measured = False

    guard = system_guard(
        user=user,
        purpose="match",
        reason="weekly digest scoring; runs from the build job with no HTTP caller to refuse",
    )
    async with metered(guard) as meter:
        async with httpx.AsyncClient(timeout=VISION_TIMEOUT) as client:

            async def one(index: int, job: Any) -> None:
                nonlocal input_tokens, measured
                async with semaphore:
                    if failure:
                        return
                    try:
                        outcome, usage = await _score_pair(
                            client, url=url, headers=headers, model=model,
                            facts_text=resume_text, job=job,
                        )
                    except ScoreFailed as exc:
                        failure.append(str(exc))
                        outcome, usage = None, exc.usage
                        # A failed call may still have been billed.
                        billed = bool(usage)
                    else:
                        billed = True
                    tokens = usage.get("input_tokens")
                    tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None
                    if tokens is not None:
                        input_tokens += tokens
                        measured = True
                    if billed:
                        # Input tokens are the whole bill on this endpoint: no
                        # output charge and no cache line, the shape turn.py
                        # emits for purpose="vision".
                        record_round(
                            db,
                            meter=meter,
                            user=user,
                            facts=facts,
                            event={
                                "kind": "usage",
                                "purpose": "match",
                                "model": model,
                                "round_index": index,
                                "input_tokens": tokens,
                                "cached_tokens": None,
                                "cache_write_tokens": None,
                                "output_tokens": None,
                                "reasoning_tokens": None,
                            },
                        )
                    if outcome is not None:
                        results[str(job.id)] = outcome

            await asyncio.gather(*(one(i, job) for i, job in enumerate(candidates)))

    run.usage = {
        "input_tokens": input_tokens if measured else None,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
    }
    if failure:
        run.error = failure[0]
        return run
    for job in candidates:
        outcome = results.get(str(job.id))
        if outcome == "refused":
            run.refused.append(str(job.id))
        elif isinstance(outcome, Scored):
            run.scored.append(outcome)
    return run
