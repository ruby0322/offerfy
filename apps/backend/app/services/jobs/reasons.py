"""One reason and one optional gap per surviving posting, and the validator
that stops a sentence claiming experience the resume does not have.

This module produces and validates user-visible match prose and does nothing
else. Scoring is `score.py`; choosing the survivors and building the digest
are later tasks.

Why the validator is stricter than `profile.py`'s. Everything `profile.py`
writes is shown to an employer as a summary the person can read and correct.
This is the only free text shown to a person as a claim about THEIR OWN
history, written by a model, with no one checking it before it lands in their
inbox. An unverifiable sentence is worse than no sentence. So the model does
not just write a reason: it must name what it cites (`resume_cites`, from the
resume; `posting_cites`, from the posting), the cite must be findable in the
resume facts (`ResumeFacts.skills_text` / `search_text`, which are columns, not
functions) and the cite must actually appear in the sentence it vouches for. A
gap is a claim that the person LACKS something, so it must name what
(`gap_missing`) and what it names must not be on the resume.

The model is not asked to "write a reason". It is told which dimension is weak:
a low `requirements_met` beside three high components localises the gap, and
the prompt carries all four component probabilities for exactly that.

Two different things are called an "item" here, and the difference is the
point. `validate` DROPS A `Prose` RECORD: a reason citing experience the resume
does not have comes back in `dropped` as (job_id, "uncited") and no `Prose` is
returned for it. It does NOT return a `Prose` with an empty-string reason. The
digest item, built later from a `Scored` plus an OPTIONAL `Prose`, keeps its
tier and score and simply has no sentence. Do not "fix" this into blank
reasons: the caller would then have to filter them out again, and a blank
string is how a missing sentence gets rendered as an empty paragraph.

A rejected gap is gentler: the reason is a separate claim, so it survives with
`gap=None`.

Length is clamped, never rejected (the discipline of `resume_facts.clamp_payload`):
truncating loses a clause, rejecting loses the sentence. Cites are checked
against the clamped text, because that is what the person reads.

Fewer than five is allowed and so is none: the honest empty state is only
honest if the model is permitted to produce it.

One provider call per digest, over the survivors only. A provider fault sets
`error` and leaves `prose` empty; the caller keeps its tiers, scores and order
and renders the rows bare.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.services.jobs.score import Scored, _facts_text, _Failed, _requirements
from app.services.llm import _output_text, _responses_url
from app.services.metering import metered, system_guard
from app.services.resume_facts import PROFILE_FALLBACK_MODEL, PROFILE_TIMEOUT, cut, usage_of

logger = logging.getLogger(__name__)

# Fixed, not the person's choice: nobody is watching the call. The same cheap
# OpenAI model resume_facts falls back to.
REASON_MODEL = PROFILE_FALLBACK_MODEL

REASON_CHARS = 240
GAP_CHARS = 160
MAX_ITEMS = 5
MIN_CITE_CHARS = 2

PROMPT = (
    "You write the one-line reasons a person sees beside job postings that "
    "matched their resume. Each posting comes with probabilities for four "
    "dimensions (requirements_met, seniority_fit, location_fit, direction_fit); "
    "a low one beside high ones says where the gap is, so write about that "
    "dimension and not a generic one. "
    "You may return fewer than five items, and you may return none: return "
    "only the postings you can write an honest reason for. "
    "Every reason must cite something from the resume facts AND something from "
    "the posting, and you must list each cite exactly as written in "
    "resume_cites and posting_cites; each resume cite must also appear in the "
    "reason's own words. "
    "gap is the single most important thing the posting asks for that the "
    "resume does not show, and it is null when there is none; when it is not "
    "null, put the missing thing, as the posting words it, in gap_missing and "
    "repeat it in the gap. Never write a gap for something the resume lists. "
    "Never invent experience: do not claim a skill, employer, title, years or "
    "result that the resume facts do not state, and never guess what the "
    "person did. "
    "Write at most one sentence per reason, in the language of the posting."
)


class ProseItem(BaseModel):
    """What the model returns per posting, before the validator has seen it."""

    job_id: str
    reason: str
    gap: str | None = None
    gap_missing: str | None = None
    resume_cites: list[str] = []
    posting_cites: list[str] = []


class _ProseBatch(BaseModel):
    items: list[ProseItem]


@dataclass(frozen=True)
class Prose:
    job_id: str
    reason: str
    gap: str | None


@dataclass
class ProseRun:
    prose: list[Prose] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


def _schema() -> dict[str, Any]:
    """The response schema, written out rather than derived from pydantic.

    Strict structured output requires every property in `required` and
    additionalProperties false throughout. `_ProseBatch` still validates the
    result: this is the wire contract and that is the truth.
    """
    text = {"type": "string"}
    maybe = {"type": ["string", "null"]}
    cites = {"type": "array", "items": {"type": "string"}}
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["job_id", "reason", "gap", "gap_missing", "resume_cites", "posting_cites"],
        "properties": {
            "job_id": text,
            "reason": text,
            "gap": maybe,
            "gap_missing": maybe,
            "resume_cites": cites,
            "posting_cites": cites,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": item}},
    }


# --- the validator -----------------------------------------------------------


def _mentions(haystack: str, needle: str) -> bool:
    """Whole-word containment, case-folded.

    The boundary is ASCII-only on purpose: `\\w` would count a CJK character
    as a word character and refuse "Python" inside "Python開發". "java" is
    not vouched for by "javascript".
    """
    needle = needle.strip().casefold()
    if len(needle) < MIN_CITE_CHARS:
        return False
    return bool(
        re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack.casefold())
    )


def _on_resume(facts: Any, cite: str) -> bool:
    skills = getattr(facts, "skills_text", "") or ""
    search = getattr(facts, "search_text", "") or ""
    token = cite.strip().casefold()
    if len(token) < MIN_CITE_CHARS:
        return False
    return f"|{token}|" in skills.casefold() or _mentions(search, token)


def validate(
    items: list[ProseItem], *, facts: Any, scored_ids: set[str]
) -> tuple[list[Prose], list[tuple[str, str]]]:
    """Keep the prose that can be checked; list the rest with why.

    Pure. Returns (kept, dropped) where dropped is (job_id, why). A dropped
    record is dropped from the PROSE list only -- see the module docstring.
    """
    kept: list[Prose] = []
    dropped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        job_id = item.job_id
        if job_id not in scored_ids:
            dropped.append((job_id, "unknown_job"))
            continue
        if job_id in seen:
            dropped.append((job_id, "duplicate"))
            continue
        seen.add(job_id)
        reason = cut(item.reason, REASON_CHARS)
        if not reason:
            dropped.append((job_id, "empty"))
            continue
        resume_cites = [c for c in item.resume_cites if _on_resume(facts, c)]
        # A cite only counts if the sentence says it, else it is decoration.
        vouched = any(_mentions(reason, c) for c in resume_cites)
        has_posting_cite = any(len(c.strip()) >= MIN_CITE_CHARS for c in item.posting_cites)
        if not (vouched and has_posting_cite):
            dropped.append((job_id, "uncited"))
            continue
        kept.append(Prose(job_id=job_id, reason=reason, gap=_checked_gap(item, facts)))
    return kept, dropped


def _checked_gap(item: ProseItem, facts: Any) -> str | None:
    gap = cut(item.gap, GAP_CHARS)
    missing = (item.gap_missing or "").strip()
    if not gap or not missing:
        return None
    # The gap says the person lacks `missing`; if the resume has it, that is
    # a manufactured claim about their history.
    if _on_resume(facts, missing):
        return None
    if not _mentions(gap, missing):
        return None
    return gap


# --- the call ----------------------------------------------------------------


class _CallFailed(Exception):
    """A provider fault. Carries whatever usage the response reported: a
    response that bills and then fails must still reach the ledger."""

    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


def _posting_block(scored: Scored, job: Any) -> str:
    comps = ", ".join(f"{k}={v:.2f}" for k, v in scored.components.items())
    return "\n".join(
        [
            f"job_id: {scored.job_id}",
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location or 'not stated'}",
            f"Tier: {scored.tier}",
            f"Probabilities: {comps}",
            "Requirements:",
            _requirements(getattr(job, "description_text", "")),
        ]
    )


def _request_body(model: str, facts_text: str, blocks: list[str]) -> dict[str, Any]:
    parts = [PROMPT, "", "RESUME FACTS:", facts_text, "", f"POSTINGS (at most {MAX_ITEMS} items):"]
    parts += ["\n---\n".join(blocks)]
    return {
        "model": model,
        "input": [{"role": "user", "content": "\n".join(parts)}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "match_prose",
                "strict": True,
                "schema": _schema(),
            }
        },
    }


async def _write_call(*, url: str, headers: dict, body: dict) -> tuple[dict, dict]:
    """The one billable call. Returns (response body, usage).

    Usage is read off the body before anything is parsed, so every
    `_CallFailed` raised after a response arrived carries it.
    """
    try:
        async with httpx.AsyncClient(timeout=PROFILE_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
            try:
                data = resp.json()
            except ValueError:
                data = None
    except httpx.HTTPError as exc:
        logger.warning("digest prose transport failed: %s", exc.__class__.__name__)
        raise _CallFailed("provider_error") from None
    usage = usage_of(data) if isinstance(data, dict) else {}
    if resp.status_code >= 400:
        logger.warning("digest prose failed status=%s", resp.status_code)
        raise _CallFailed("provider_error", usage)
    if not isinstance(data, dict):
        raise _CallFailed("unparseable", usage)
    return data, usage


def _record(meter: Any, model: str, usage: dict) -> None:
    if not any(v is not None for v in usage.values()):
        return
    meter.record(
        {
            "kind": "usage",
            "purpose": "digest",
            "model": model,
            "round_index": 0,
            "input_tokens": usage.get("input_tokens"),
            "cached_tokens": usage.get("cached_tokens"),
            "cache_write_tokens": None,
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": None,
        }
    )


async def write_reasons(db, *, user, facts, scored: list[Scored], jobs: list[Any]) -> ProseRun:
    """One call for the whole digest. Never raises on a provider fault."""
    run = ProseRun(model=REASON_MODEL)
    by_id = {str(getattr(j, "id", "")): j for j in jobs}
    survivors = [s for s in scored if s.job_id in by_id]
    if not survivors:
        return run
    settings = get_settings()
    if not (settings.openai_api_key or "").strip():
        run.error = "llm_not_configured"
        return run
    try:
        facts_text = _facts_text(facts)
    except _Failed as exc:
        run.error = str(exc)
        return run

    body = _request_body(
        REASON_MODEL, facts_text, [_posting_block(s, by_id[s.job_id]) for s in survivors]
    )
    url = _responses_url(settings.openai_base_url or "https://api.openai.com/v1")
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    guard = system_guard(
        user=user,
        purpose="digest",
        reason="weekly digest prose; runs from the build job with no HTTP caller to refuse",
    )
    async with metered(guard) as meter:
        try:
            data, usage = await _write_call(url=url, headers=headers, body=body)
        except _CallFailed as exc:
            # A failed call may still have been billed.
            _record(meter, REASON_MODEL, exc.usage)
            run.usage, run.error = exc.usage, str(exc)
            return run
        # Recorded before the answer is read: a response that bills and then
        # fails to parse must not vanish from the ledger.
        _record(meter, REASON_MODEL, usage)
    run.usage = usage

    output = data.get("output") if isinstance(data.get("output"), list) else []
    text = _output_text(data, output)
    if not text.strip():
        run.error = "no_output"
        return run
    try:
        batch = _ProseBatch.model_validate_json(text)
    except ValidationError as exc:
        # Where it failed, never what it said: the content is personal data.
        logger.warning(
            "digest prose rejected: %s",
            "; ".join(".".join(str(p) for p in err["loc"]) for err in exc.errors()),
        )
        run.error = "invalid_payload"
        return run
    run.prose, run.dropped = validate(
        batch.items, facts=facts, scored_ids={s.job_id for s in survivors}
    )
    return run
