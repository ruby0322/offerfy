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

Two honest limits. The cite check trusts what the model declares, so a
sentence that names something it did not declare is only caught by a
conservative scan for name-like ASCII tokens (capitalised, inner capital,
digit, + or #); a LOWERCASE technology the model neither declares nor
capitalises ("kubernetes") is a known hole, as is any non-ASCII claim. Position
excuses nothing: a capitalised token is excused only by being one of a short
list of function words and sentence-openers (`_STOPWORDS`), because "Terraform
and Python work fits this role." puts the invented claim first. The scan
errs towards dropping a sentence. Second, `validate` can only check a posting
cite against the posting when `check` is given the posting's field values
(write_reasons does, values only -- a cite of "Requirements" must not match a
label); at most MAX_ITEMS prose records are kept, but WHICH postings are sent is
the caller's cap (Task 5), not this module's.

The scan is split by field. In a `reason`, name-like tokens from the posting's
company and location are exempt (a city or an employer is not a claim about
the person) but tokens from its TITLE and REQUIREMENTS are not (titles carry
technology names; a posting cite never exempts anything): a requirement
mirrored back as the person's experience is the likeliest invented claim, so it
must be declared and on the resume. That company/location exemption covers a
MENTION, not an ATTRIBUTION: Docker, Snowflake and Terraform are all
employers, so an exempt token loses its exemption when a possessive stands
immediately before it or "experience"/"work" immediately after. "...fits
Terraform." is the employer; "Your Terraform work..." is a history. In the
`gap`, the gap_missing token is allowed wherever it stands -- naming a
requirement the person lacks is what that field is for. It is held to
`gap_missing` (named, at most GAP_MISSING_CHARS long, absent from the resume,
in the gap); an unbounded gap_missing would exempt a whole phrase.

The gap IS also scanned for any other name-like token (exempting gap_missing,
and the company and location as mentions), and must contain an absence or
demand marker (`_ABSENCE`), since "You have shipped Kubernetes in prod." names
only the gap token. A marker alone proves nothing, though -- a demand verb
says nothing about the person -- so a gap asserting POSSESSION (`_POSSESSION`:
"you have", "you led", "your X experience", a subject-elided perfect) is
rejected whatever marker it also carries. What stays uncatchable: a gap that
frames the gap_missing token ITSELF as something the person has done in words
`_POSSESSION` does not cover ("Missing docker; you led docker swarm" where the
lowercase token is legitimately exempt). Token scanning cannot see that; the
prompt is the only defence.

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
from app.services.jobs.score import ScoreFailed, Scored, facts_text, requirements
from app.services.llm.providers.openai import output_text, responses_url
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
# gap_missing names ONE requirement, not a sentence. Unbounded, it exempts
# every name inside itself from the scan ("Kubernetes production deployments at
# Google Scale"), so it is capped and a longer one rejects the gap.
GAP_MISSING_CHARS = 40

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
    "Completeness is required: every skill, employer, institution or technology "
    "named in the reason must appear in resume_cites. Refer to the posting by its "
    "company or location, or just say \"this role\": do not name technology "
    "words from the title or the requirements unless the resume has them and "
    "you list them. A skill or requirement the person does not have never goes "
    "in the reason: say it in gap and gap_missing. A name you did not list is "
    "rejected. "
    "gap is the single most important thing the posting asks for that the "
    "resume does not show, and it is null when there is none; when it is not "
    "null, put the missing thing, as the posting words it, in gap_missing and "
    "repeat it in the gap. A gap says what is ABSENT or what the posting asks "
    "for (not, no, missing, without, lacks, new to, the posting wants); it never "
    "describes anything the person has done, and it names nothing else. "
    "Never write a gap for something the resume lists. "
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
    # Job ids whose gap was suppressed (manufactured or unverifiable). Not in
    # `dropped`, which means "whole record removed"; the reason survived.
    gaps_rejected: list[str] = field(default_factory=list)
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


@dataclass(frozen=True)
class PostingText:
    """The field VALUES of one posting, as shown to the model -- no labels.

    Kept apart because the scan treats them differently. Company and location
    are not claims about the person, so a name found there is exempt where it
    is MENTIONED -- but not where it is attributed to the person, since an
    employer may be called Terraform. The title
    is NOT: it routinely contains technology names ("Senior Terraform
    Engineer"), so a title word in a `reason` must be declared and on the
    resume. Requirements likewise: a requirement mirrored back as the person's
    experience is the most likely invented claim. In a `gap` the gap_missing
    token is exactly what the field is for.
    """

    company: str
    title: str
    location: str
    requirements: str

    @property
    def header(self) -> str:
        return "\n".join([self.company, self.title, self.location])

    @property
    def everything(self) -> str:
        return "\n".join([self.header, self.requirements])


@dataclass
class Checked:
    kept: list[Prose] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    gaps_rejected: list[str] = field(default_factory=list)


# An ASCII token of 3+ characters that looks like a name or a technology:
# capitalised, or carrying an inner capital, a digit, or + / #.  Lowercase
# technologies ("kubernetes") are NOT caught -- a known limitation, see the
# module docstring.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#]{2,}")

# Capitalisation is not excused by POSITION: "Terraform and Python work fits"
# and "Your Python work fits. Terraform shipped in prod." both put an invented
# claim where a sentence begins. Only these words are excused, and only by
# being themselves: the common sentence-openers and function words no employer
# or technology is called. The list is deliberately short -- an ambiguous token
# costs one dropped sentence, an exempted one ships an invented claim.
_STOPWORDS = frozenset(
    """
    you your yours the this that these those they them their there here his
    her hers its our ours and but for nor not with without from into onto over
    under after before while when where what which who whom whose why how
    because although though however also both each every all any some most
    many much few more less least other another such same than then still yet
    about across along around between during through upon within against among
    has have had was were are been being can could shall should will would
    may might must does did doing having given despite besides beyond
    nothing none neither either missing lacks lacking never new absent
    unfamiliar seems looks matches fits experience background work working
    role roles team teams years year posting resume
    only just enough plenty several nearly almost mostly largely likely
    clearly exactly fewer little
    one two three four five six seven eight nine ten eleven twelve
    strong good great direct close ready able senior junior
    remote onsite hybrid backend frontend
    """.split()
)

# A possessive immediately before, or "experience"/"work" immediately after,
# turns a MENTION into an ATTRIBUTION. See `_undeclared_names`.
_POSSESSIVES = frozenset({"your", "my", "his", "her", "their", "its", "our"})
_ATTRIBUTIVE_NOUNS = frozenset({"experience", "work"})
_WORD_BEFORE = re.compile(r"([A-Za-z']+)\s*$")
_WORD_AFTER = re.compile(r"^\s*([A-Za-z']+)")


def _attributed(text: str, start: int, end: int) -> bool:
    """Is the token at [start:end) framed as the PERSON's, not the posting's?

    A company or a city named after a technology (Docker, MongoDB, Snowflake,
    Terraform are all employers) would otherwise launder the claim: "Your
    Python and Terraform work fits Terraform." The mention at the end is the
    employer; "Your ... Terraform work" is a claim about a history.
    """
    before = _WORD_BEFORE.search(text[:start])
    if before and before.group(1).casefold() in _POSSESSIVES:
        return True
    after = _WORD_AFTER.match(text[end:])
    return bool(after and after.group(1).casefold() in _ATTRIBUTIVE_NOUNS)


def _undeclared_names(
    text: str, allowed: list[str], facts: Any, exempt: str = ""
) -> list[str]:
    """Name-like tokens in the sentence that the resume, the declared cites
    and the posting cites all fail to account for.

    `allowed` exempts a token wherever it stands (it is on the resume or
    declared). `exempt` -- the posting's company and location -- exempts it
    only as a mention: an attributed one is still a claim about the person.
    """
    found = []
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        looks_named = (
            (token[0].isupper() and token.casefold() not in _STOPWORDS)
            or any(c.isupper() for c in token[1:])
            or any(c.isdigit() or c in "+#" for c in token)
        )
        if not looks_named:
            continue
        if _on_resume(facts, token) or any(_mentions(c, token) for c in allowed):
            continue
        if (
            exempt
            and _mentions(exempt, token)
            and not _attributed(text, match.start(), match.end())
        ):
            continue
        found.append(token)
    return found


def check(
    items: list[ProseItem],
    *,
    facts: Any,
    scored_ids: set[str],
    postings: dict[str, PostingText] | None = None,
) -> Checked:
    """`validate` plus the ids whose gap was suppressed.

    `postings` maps job_id to the posting's field values. When given, a posting
    cite must be found in them (correspondence) and name-like tokens from the
    posting's company and location are exempt from the undeclared scan where
    they are mentioned rather than attributed to the person (title and
    requirements are never exempt). Posting cites prove the sentence is about THIS
    posting; they never exempt a token, since that is a different question from
    whether a word is a claim about the person. Without it the posting half only checks that
    something was declared (presence) and nothing is exempt.

    The cap (MAX_ITEMS) is enforced here: the prompt asks for at most five, but
    a prompt is not a bound. The caller still owns WHICH postings are sent.
    """
    out = Checked()
    seen: set[str] = set()
    for item in items:
        job_id = item.job_id
        if job_id not in scored_ids:
            out.dropped.append((job_id, "unknown_job"))
            continue
        if job_id in seen:
            out.dropped.append((job_id, "duplicate"))
            continue
        reason = cut(item.reason, REASON_CHARS)
        if not reason:
            out.dropped.append((job_id, "empty"))
            continue
        declared = [c for c in item.resume_cites if c.strip()]
        # EVERY declared cite must be on the resume. Filtering to the valid
        # ones and vouching with any of them would let "Python and Rust" ship
        # on Python's credit while Rust is silently discarded.
        if not declared or not all(_on_resume(facts, c) for c in declared):
            out.dropped.append((job_id, "uncited"))
            continue
        # A cite only counts if the sentence says it, else it is decoration.
        if not any(_mentions(reason, c) for c in declared):
            out.dropped.append((job_id, "uncited"))
            continue
        posting_cites = [c for c in item.posting_cites if len(c.strip()) >= MIN_CITE_CHARS]
        # Company and location only; see PostingText for why not the title.
        exempt = ""
        if postings is not None:
            posting = postings.get(job_id)
            posting_cites = [c for c in posting_cites if posting and _mentions(posting.everything, c)]
            exempt = "\n".join([posting.company, posting.location]) if posting else ""
        if not posting_cites:
            out.dropped.append((job_id, "uncited"))
            continue
        # Completeness is asked of the model in the prompt; this is the cheap
        # check that it kept the promise for name-like tokens.
        if _undeclared_names(reason, declared, facts, exempt=exempt):
            out.dropped.append((job_id, "undeclared"))
            continue
        seen.add(job_id)
        if len(out.kept) >= MAX_ITEMS:
            out.dropped.append((job_id, "over_cap"))
            continue
        gap = _checked_gap(item, facts, exempt)
        if gap is None and (item.gap or "").strip():
            out.gaps_rejected.append(job_id)
        out.kept.append(Prose(job_id=job_id, reason=reason, gap=gap))
    return out


def validate(
    items: list[ProseItem], *, facts: Any, scored_ids: set[str]
) -> tuple[list[Prose], list[tuple[str, str]]]:
    """Keep the prose that can be checked; list the rest with why.

    Pure. Returns (kept, dropped) where dropped is (job_id, why). A dropped
    record is dropped from the PROSE list only -- see the module docstring.
    """
    checked = check(items, facts=facts, scored_ids=scored_ids)
    return checked.kept, checked.dropped


# Words that frame the gap as an absence or as the posting's demand. A gap
# that has none is describing the person, which is the claim this module exists
# to stop. ASCII words are matched whole; the CJK ones as substrings.
_ABSENCE = re.compile(
    r"(?<![a-z])(?:not|no|never|missing|without|lack\w*|new|absent|unfamiliar|"
    r"wants?|requires?|needs?|expects?|seeks?|asks?)(?![a-z])|n't|沒有|缺|未|無|不",
    re.IGNORECASE,
)


# A gap names an ABSENCE. A marker is not enough to prove that, because a
# demand verb ("the posting wants X") says nothing about the person: "You need
# Kubernetes experience and have shipped it in prod." carries a marker and a
# history claim at once. So possession is rejected outright, whatever marker
# the sentence also contains. The cost is a gap phrased as a negated
# possession ("You have not shipped Kubernetes") -- rejected too, and the
# reason survives either way; the prompt asks for absence wording.
_HAS_VERBS = (
    r"led|lead|leads|shipped|ship|ships|built|build|builds|drove|drive|drives|"
    r"driven|ran|run|runs|managed|manage|manages|owned|own|owns|delivered|"
    r"deliver|delivers|designed|design|designs|developed|develop|develops|"
    r"wrote|write|writes|worked|work|works|used|use|uses|brought|bring|brings|"
    r"know|knew|knows|scaled|scale|scales|maintained|maintain|maintains"
)
_POSSESSION = re.compile(
    # "you've", "you have/had/has", "you led", "you already shipped", ...
    r"(?<![a-z])you\s*(?:'|’)\s*ve(?![a-z])"
    rf"|(?<![a-z])you\s+(?:already\s+|also\s+)?(?:have|had|has|{_HAS_VERBS})(?![a-z])"
    # a subject-elided perfect: "...and have shipped it in prod". A relative
    # clause about the posting's ideal candidate ("someone who has shipped
    # Kubernetes") is about nobody in particular, so it is left alone.
    rf"|(?<![a-z])(?<!who )(?<!they )(?<!that )(?:have|has|had|having)"
    rf"\s+(?:already\s+|also\s+)?(?:{_HAS_VERBS})(?![a-z])"
    # "your Kubernetes experience"
    r"|(?<![a-z])your\s+[A-Za-z0-9+#.-]+\s+(?:experience|background|history)(?![a-z])",
    re.IGNORECASE,
)


def _checked_gap(item: ProseItem, facts: Any, exempt: str = "") -> str | None:
    gap = cut(item.gap, GAP_CHARS)
    missing = (item.gap_missing or "").strip()
    if not gap or not missing:
        return None
    # One requirement, not a phrase: a long gap_missing exempts every name
    # inside it from the scan below.
    if len(missing) > GAP_MISSING_CHARS:
        return None
    # The gap says the person lacks `missing`; if the resume has it, that is
    # a manufactured claim about their history.
    if _on_resume(facts, missing):
        return None
    if not _mentions(gap, missing):
        return None
    if not _ABSENCE.search(gap):
        return None
    # A gap must never assert that the person HAS something.
    if _POSSESSION.search(gap):
        return None
    # Nothing else name-like may appear: "...you led Terraform migrations".
    # gap_missing is exempt wherever it stands -- naming the requirement the
    # person lacks is the field's purpose -- while the company and the location
    # are exempt only as mentions.
    if _undeclared_names(gap, [missing], facts, exempt=exempt):
        return None
    return gap


# --- the call ----------------------------------------------------------------


class _CallFailed(Exception):
    """A provider fault. Carries whatever usage the response reported: a
    response that bills and then fails must still reach the ledger."""

    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


def _posting_text(job: Any) -> PostingText:
    return PostingText(
        company=str(job.company),
        title=str(job.title),
        location=str(job.location or ""),
        requirements=requirements(getattr(job, "description_text", "")),
    )


def _posting_block(scored: Scored, posting: PostingText) -> str:
    comps = ", ".join(f"{k}={v:.2f}" for k, v in scored.components.items())
    return "\n".join(
        [
            f"job_id: {scored.job_id}",
            f"Title: {posting.title}",
            f"Company: {posting.company}",
            f"Location: {posting.location or 'not stated'}",
            f"Tier: {scored.tier}",
            f"Probabilities: {comps}",
            "Requirements:",
            posting.requirements,
        ]
    )


def _request_body(model: str, resume_text: str, blocks: list[str]) -> dict[str, Any]:
    parts = [PROMPT, "", "RESUME FACTS:", resume_text, "", f"POSTINGS (at most {MAX_ITEMS} items):"]
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
        resume_text = facts_text(facts)
    except ScoreFailed as exc:
        run.error = str(exc)
        return run

    posting_texts = {s.job_id: _posting_text(by_id[s.job_id]) for s in survivors}
    blocks = [_posting_block(s, posting_texts[s.job_id]) for s in survivors]
    body = _request_body(REASON_MODEL, resume_text, blocks)
    url = responses_url(settings.openai_base_url or "https://api.openai.com/v1")
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
    text = output_text(data, output)
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
    checked = check(
        batch.items,
        facts=facts,
        scored_ids={s.job_id for s in survivors},
        postings=posting_texts,
    )
    run.prose, run.dropped, run.gaps_rejected = (
        checked.kept, checked.dropped, checked.gaps_rejected,
    )
    return run
