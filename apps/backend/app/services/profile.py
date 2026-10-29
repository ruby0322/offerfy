"""The talent profile: building one, and getting rid of one.

A profile is derived state. It exists only while its owner's newest answer for
talent_introduction is 'granted', and it holds no contact details -- see
scrub_contacts, and the absence of any field for one in
app.schemas.TalentProfilePayload.

Nothing here is triggered implicitly. Building is not hung off publishing,
because publishing commits in-request and must not start failing when the
provider is down; staleness is computed from the revision pointer instead, so
the publish path needs no code at all. And it is never done lazily inside a
read, because that would let an external reader spend our money and set our
latency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    TALENT_PROFILE_SCHEMA_VERSION,
    LlmCall,
    Resume,
    TalentProfile,
    User,
)
from app.schemas import (
    TALENT_EDUCATION_LIMIT,
    TALENT_HEADLINE_CHARS,
    TALENT_HIGHLIGHT_LIMIT,
    TALENT_LANGUAGE_LIMIT,
    TALENT_LOCATION_LIMIT,
    TALENT_NAME_CHARS,
    TALENT_ROLE_LIMIT,
    TALENT_SKILL_LIMIT,
    TALENT_SUMMARY_CHARS,
    TALENT_SUMMARY_SKILLS,
    TALENT_YEARS_MAX,
    TalentProfileDetail,
    TalentProfilePayload,
    TalentProfileSummary,
)
from app.services.account import display_name_of
from app.services.consent import has_consent, has_consent_many
from app.services.family import family_master
from app.services.llm import _output_text, _responses_url
from app.services.llm.providers import openai as openai_provider
from app.services.model_catalog import DEFAULT_MODEL, spec_or_passthrough
from app.services.memory import accepted_memory_texts
from app.services.revisions import published_source

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


PROFILE_TIMEOUT = httpx.Timeout(90.0, connect=10.0)

PROMPT = (
    "You read one resume and return a structured talent profile for it. "
    "Use only what the resume and the listed facts about the person say; "
    "never infer, guess or fill a gap. Leave a field null and a list empty "
    "when the resume does not answer it. "
    "Never include an email address, phone number, URL, social handle or "
    "street address in any field, including the summary and the role "
    "highlights -- employers are introduced through Offerfy, not directly. "
    "locations is city or region only. "
    "Use YYYY-MM for dates, or Present for an ongoing role. "
    "headline is the person's own current role in their words, not a pitch. "
    "summary is at most three sentences, in the resume's own language."
)


def _schema() -> dict[str, Any]:
    """The response schema, written out rather than derived from pydantic.

    Strict structured output requires every property in `required` and
    additionalProperties false throughout, which a pydantic schema with
    defaults does not produce. The payload is still validated against
    TalentProfilePayload afterwards, so this is the wire contract and that is
    the truth.
    """
    text = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "display_name",
            "headline",
            "summary",
            "years_experience",
            "locations",
            "languages",
            "skills",
            "roles",
            "education",
        ],
        "properties": {
            "display_name": text,
            "headline": text,
            "summary": text,
            "years_experience": {"type": ["integer", "null"]},
            "locations": {"type": "array", "items": {"type": "string"}},
            "languages": {"type": "array", "items": {"type": "string"}},
            "skills": {"type": "array", "items": {"type": "string"}},
            "roles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "employer",
                        "title",
                        "start",
                        "end",
                        "highlights",
                    ],
                    "properties": {
                        "employer": text,
                        "title": text,
                        "start": text,
                        "end": text,
                        "highlights": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "education": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["institution", "credential", "field", "end"],
                    "properties": {
                        "institution": text,
                        "credential": text,
                        "field": text,
                        "end": text,
                    },
                },
            },
        },
    }


# --- contact scrubbing -------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(
    r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|net|org|io|dev|cc|tw|me|ai|co)\b",
    re.IGNORECASE,
)
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_HANDLE = re.compile(r"(?<!\w)@[\w.]{2,}")
# Four or more digits in a row: postcodes and street numbers. Stripped from
# locations, where a city is the most precise thing we want.
_LONG_DIGITS = re.compile(r"\d{4,}")


def _scrub_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value
    for pattern in (_EMAIL, _URL, _PHONE, _HANDLE):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;·|-")
    return cleaned or None


def scrub_contacts(payload: TalentProfilePayload) -> TalentProfilePayload:
    """Strip anything that could be used to reach the person directly.

    The prompt already says not to include it. This runs anyway: a model that
    puts an email in `summary` once in a hundred builds is a model that leaks
    a hundredth of the pool, and the whole promise of this surface is that the
    introduction goes through us.

    A field that is only a contact detail becomes None rather than an empty
    string, so the payload never carries a field that looks answered.
    """
    payload.display_name = _scrub_text(payload.display_name)
    payload.headline = _scrub_text(payload.headline)
    payload.summary = _scrub_text(payload.summary)
    payload.locations = [
        cleaned
        for raw in payload.locations
        if (cleaned := _scrub_text(_LONG_DIGITS.sub(" ", raw)))
    ]
    payload.languages = [c for raw in payload.languages if (c := _scrub_text(raw))]
    payload.skills = [c for raw in payload.skills if (c := _scrub_text(raw))]
    for role in payload.roles:
        role.employer = _scrub_text(role.employer)
        role.title = _scrub_text(role.title)
        role.start = _scrub_text(role.start)
        role.end = _scrub_text(role.end)
        role.highlights = [c for raw in role.highlights if (c := _scrub_text(raw))]
    for school in payload.education:
        school.institution = _scrub_text(school.institution)
        school.credential = _scrub_text(school.credential)
        school.field = _scrub_text(school.field)
        school.end = _scrub_text(school.end)
    return payload


# --- reading what we hold ----------------------------------------------------


def profile_for_user(db: Session, user: User) -> TalentProfile | None:
    return (
        db.query(TalentProfile).filter(TalentProfile.user_id == user.id).one_or_none()
    )


def record_build_spend(db: Session, user: User, result, meter) -> None:
    """Record what one profile build billed.

    Lives here rather than in a router because both callers need it and one of
    them is a cron job -- app/jobs/refresh_profiles.py -- which has no business
    importing from app/routers.

    Profile builds are a billable path and belong in LlmCall like the rest.

    Commits on its own, so callers must settle the build's own transaction
    first -- commit a profile they are keeping, roll back one they are not --
    or this commit would persist a write they had decided to discard.

    try/except for the same reason the chat router wraps its own insert:
    telemetry may be lost, the work may not.
    """
    if not result.model or result.resume_id is None:
        return
    try:
        db.add(
            LlmCall(
                resume_id=result.resume_id,
                user_id=user.id,
                purpose="profile",
                model=result.model[:128],
                round_index=0,
                input_tokens=result.usage.get("input_tokens"),
                cached_tokens=result.usage.get("cached_tokens"),
                output_tokens=result.usage.get("output_tokens"),
                source_chars=result.source_chars,
            )
        )
        db.commit()
    except Exception:  # pragma: no cover - telemetry must never fail a build
        db.rollback()
    # Two writes, two durabilities: the row above is telemetry and may be lost,
    # this increments the counter the limit is enforced against.
    meter.record(
        {
            "model": result.model,
            "purpose": "profile",
            "round_index": 0,
            **result.usage,
        }
    )


def delete_profile_for_user(db: Session, user: User) -> bool:
    """Stop holding the derived copy. True when there was one.

    Called on withdrawal, and when the source pointer moves. The read path
    checks live consent too, so this is defence in depth -- but it is also
    data minimisation, which a consent check is not: we promised to stop
    passing the profile on, and the cheapest way to keep that promise is to
    not have it.

    Deliberately does not touch disclosure_events. The record of who already
    received the profile has to outlive the profile, which is why that table
    has no foreign key to this row.
    """
    profile = profile_for_user(db, user)
    if profile is None:
        return False
    db.delete(profile)
    db.flush()
    return True


def source_resume(db: Session, user: User) -> Resume | None:
    """The resume the person chose, if they chose one they still own."""
    if not user.profile_resume_id:
        return None
    resume = db.get(Resume, user.profile_resume_id)
    if resume is None or resume.user_id != user.id:
        return None
    return resume


def _input_hash(revision_id: str, source: str, facts: list[str]) -> str:
    joined = "\x00".join([revision_id, source, "\n".join(facts)])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _bundle(db: Session, user: User) -> tuple[Resume, str, str, list[str]] | None:
    """Everything a build reads: the resume, its published revision and facts."""
    resume = source_resume(db, user)
    if resume is None or resume.published_revision_id is None:
        return None
    source = published_source(db, resume)
    if not source:
        return None
    # Memory items hang off the master of the family, so a tailor chosen as
    # the source still reads its parent's facts.
    facts = accepted_memory_texts(db, family_master(db, resume).id)
    return resume, resume.published_revision_id, source, facts


def is_stale(db: Session, user: User, profile: TalentProfile) -> bool:
    """True when the profile no longer matches what it was built from.

    Covers republishing the resume and accepting a new memory item, which is
    why input_hash exists beside the revision pointer.
    """
    bundle = _bundle(db, user)
    if bundle is None:
        return False
    _resume, revision_id, source, facts = bundle
    if profile.source_revision_id != revision_id:
        return True
    return profile.input_hash != _input_hash(revision_id, source, facts)


def payload_of(profile: TalentProfile) -> TalentProfilePayload:
    return TalentProfilePayload.model_validate_json(profile.profile_json)


def detail_of(profile: TalentProfile) -> TalentProfileDetail:
    return TalentProfileDetail(
        profile_id=profile.id,
        updated_at=profile.built_at.isoformat(),
        profile=payload_of(profile),
    )


def summary_of(profile: TalentProfile) -> TalentProfileSummary:
    payload = payload_of(profile)
    return TalentProfileSummary(
        profile_id=profile.id,
        display_name=profile.display_name,
        headline=profile.headline,
        years_experience=profile.years_experience,
        locations=payload.locations,
        skills=payload.skills[:TALENT_SUMMARY_SKILLS],
        updated_at=profile.built_at.isoformat(),
    )


# --- building ----------------------------------------------------------------


@dataclass
class ProfileBuildResult:
    """What a build did. A failure never writes and never overwrites."""

    profile: TalentProfile | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    source_chars: int = 0
    resume_id: str | None = None


def _derived(payload: TalentProfilePayload) -> dict[str, Any]:
    """The denormalised columns, rebuilt from the payload on every write.

    skills_text is pipe-delimited so LIKE '%|java|%' cannot match 'javascript'.
    """
    skills = [skill.lower() for skill in payload.skills]
    words = [
        payload.display_name or "",
        payload.headline or "",
        payload.summary or "",
        " ".join(payload.locations),
        " ".join(payload.languages),
        " ".join(payload.skills),
        " ".join(
            " ".join(filter(None, [role.title, role.employer]))
            for role in payload.roles
        ),
        " ".join(
            " ".join(filter(None, [school.institution, school.credential, school.field]))
            for school in payload.education
        ),
    ]
    return {
        "display_name": (payload.display_name or None),
        "headline": (payload.headline or None),
        "years_experience": payload.years_experience,
        "search_text": re.sub(r"\s+", " ", " ".join(words)).strip().lower(),
        "skills_text": ("|" + "|".join(skills) + "|") if skills else "",
    }


def _is_empty(payload: TalentProfilePayload) -> bool:
    """No name, no headline and no roles is not a profile.

    Writing it would mean handing an employer an empty card and logging a
    disclosure for it.
    """
    return not (payload.display_name or payload.headline or payload.roles)


def _request_body(model: str, source: str, facts: list[str]) -> dict[str, Any]:
    parts = [PROMPT, "", "RESUME (Typst source):", source]
    if facts:
        parts += ["", "FACTS THE PERSON CONFIRMED ABOUT THEMSELVES:"]
        parts += [f"- {fact}" for fact in facts]
    return {
        "model": model,
        "input": [{"role": "user", "content": "\n".join(parts)}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "talent_profile",
                "strict": True,
                "schema": _schema(),
            }
        },
    }


def _cut(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed[:limit] or None


def clamp_payload(payload: TalentProfilePayload) -> TalentProfilePayload:
    """Bring the payload inside every cap, by truncating.

    The caps bound what a single read hands over. They are deliberately not
    pydantic constraints: a length constraint rejects, and rejecting means
    throwing away an otherwise good profile because one role had a fifth
    bullet point -- which is precisely what happened to the first real resume
    this ran on. Truncating loses the fifth bullet. Rejecting loses the person.
    """
    payload.schema_version = TALENT_PROFILE_SCHEMA_VERSION
    payload.display_name = _cut(payload.display_name, TALENT_NAME_CHARS)
    payload.headline = _cut(payload.headline, TALENT_HEADLINE_CHARS)
    payload.summary = _cut(payload.summary, TALENT_SUMMARY_CHARS)
    if payload.years_experience is not None:
        # The DB has a CHECK for this range, so a wild number must be clamped
        # here or the insert fails.
        payload.years_experience = max(0, min(TALENT_YEARS_MAX, payload.years_experience))
    payload.locations = payload.locations[:TALENT_LOCATION_LIMIT]
    payload.languages = payload.languages[:TALENT_LANGUAGE_LIMIT]
    payload.skills = payload.skills[:TALENT_SKILL_LIMIT]
    payload.education = payload.education[:TALENT_EDUCATION_LIMIT]
    payload.roles = payload.roles[:TALENT_ROLE_LIMIT]
    for role in payload.roles:
        role.highlights = role.highlights[:TALENT_HIGHLIGHT_LIMIT]
    return payload


def _usage_of(data: dict[str, Any]) -> dict[str, Any]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return {}
    cached = usage.get("input_tokens_details") or {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cached_tokens": cached.get("cached_tokens")
        if isinstance(cached, dict)
        else None,
    }


async def build_profile(db: Session, user: User) -> ProfileBuildResult:
    """Build or rebuild this person's profile. Never raises.

    Consent is required here, not only on the read path: extraction sends
    resume content to the language-model provider for a purpose that only the
    talent_introduction grant covers.
    """
    if not has_consent(db, user=user, purpose="talent_introduction"):
        return ProfileBuildResult(error="no_consent")
    bundle = _bundle(db, user)
    if bundle is None:
        return ProfileBuildResult(error="no_source")
    resume, revision_id, source, facts = bundle
    # Runs on the OpenAI Responses API regardless of what anyone picked in
    # the editor, and the model is the configured default rather than this
    # person's choice. Two reasons, and both are about the output rather than
    # the cost: this call has to return JSON that validates against
    # TalentProfilePayload, and `_request_body` shapes a Responses-API
    # structured-output request to get it. A profile build is also not an
    # interaction anyone is watching, so there is nothing for a preference to
    # be a preference about.
    #
    # It is still metered like any other call, which is why the model id below
    # is recorded: see jobs/refresh_profiles.py and the system_guard it holds.
    settings = get_settings()
    if not openai_provider.OpenAIProvider().configured():
        return ProfileBuildResult(error="llm_not_configured", resume_id=resume.id)
    pinned = spec_or_passthrough(settings.pinned_chat_model() or DEFAULT_MODEL)
    # An operator pin is honoured only when it names an OpenAI model. Pinning
    # an Anthropic one must not take the matching feature down with it, and
    # falling back to the catalogue default is better than refusing: this call
    # has one job, and the default model does it.
    spec = pinned if pinned.provider == openai_provider.NAME else spec_or_passthrough(
        DEFAULT_MODEL
    )
    model = spec.id
    url = _responses_url(settings.openai_base_url or "https://api.openai.com/v1")
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    result = ProfileBuildResult(
        model=model, source_chars=len(source), resume_id=resume.id
    )
    try:
        async with httpx.AsyncClient(timeout=PROFILE_TIMEOUT) as client:
            response = await client.post(
                url, headers=headers, json=_request_body(model, source, facts)
            )
            if response.status_code >= 400:
                logger.warning("profile build failed status=%s", response.status_code)
                result.error = "provider_error"
                return result
            data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("profile build transport failed: %s", exc.__class__.__name__)
        result.error = "provider_error"
        return result
    except ValueError:
        result.error = "unparseable"
        return result

    if not isinstance(data, dict):
        result.error = "unparseable"
        return result
    result.usage = _usage_of(data)
    output = data.get("output") if isinstance(data.get("output"), list) else []
    text = _output_text(data, output)
    if not text.strip():
        logger.warning("profile build returned no text status=%s", data.get("status"))
        result.error = "no_output"
        return result
    try:
        payload = TalentProfilePayload.model_validate_json(text)
    except ValidationError as exc:
        # Log where it failed, never what it said: the content is personal
        # data. Distinct from no_output so the two cannot be confused again.
        logger.warning(
            "profile payload rejected: %s",
            "; ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors()),
        )
        result.error = "invalid_payload"
        return result
    except ValueError:
        logger.warning("profile build output was not JSON")
        result.error = "unparseable"
        return result

    payload = clamp_payload(scrub_contacts(payload))
    # A name the person typed, or failing that one Google verified, beats one a
    # model read off a document. display_name_of settles that precedence in one
    # place -- the nav shows the same answer, and the name an employer reads
    # must not be a different one from the name the owner sees.
    own_name = display_name_of(user)
    if own_name:
        payload.display_name = _cut(own_name, TALENT_NAME_CHARS)
    if _is_empty(payload):
        logger.warning("profile build produced an empty payload")
        result.error = "empty"
        return result

    # Canonical JSON: the bytes are what content_hash commits to, and
    # disclosure_events will cite that hash as evidence of what a recipient
    # holds. sort_keys so an identical payload hashes identically.
    blob = json.dumps(
        payload.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    columns = _derived(payload)
    profile = profile_for_user(db, user)
    if profile is None:
        profile = TalentProfile(user_id=user.id)
        db.add(profile)
    profile.source_resume_id = resume.id
    profile.source_revision_id = revision_id
    profile.input_hash = _input_hash(revision_id, source, facts)
    profile.profile_json = blob
    profile.content_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    profile.model = model[:128]
    # Set on every write, not just the insert: built_at is what the owner and
    # the reader see as this payload's date.
    profile.built_at = _utcnow()
    for column, value in columns.items():
        setattr(profile, column, value)
    db.flush()
    result.profile = profile
    return result


# --- the gated read path -----------------------------------------------------


def readable_profiles_query(db: Session):
    """Profiles whose source is still live. Gate 3 of three.

    This is SQL only, and it is deliberately not the whole answer. It requires
    that the owner still points at the resume this profile was built from and
    that the resume still has a published revision -- so moving the pointer or
    unpublishing takes someone out of results with no extra bookkeeping.

    It cannot express "their newest consent answer is granted": that needs the
    tie rule in services.consent, which a GROUP BY max(created_at) join cannot
    reproduce. So the caller must filter the fetched page through
    readable_profiles() rather than using this query on its own.
    """
    return (
        db.query(TalentProfile)
        .join(User, User.id == TalentProfile.user_id)
        .join(Resume, Resume.id == TalentProfile.source_resume_id)
        .filter(
            User.profile_resume_id.isnot(None),
            User.profile_resume_id == TalentProfile.source_resume_id,
            Resume.published_revision_id.isnot(None),
        )
    )


def readable_profiles(
    db: Session, profiles: Sequence[TalentProfile]
) -> list[TalentProfile]:
    """Gate 2: drop anyone whose newest answer is not 'granted'.

    One query for the page, through services.consent so "newest" keeps a
    single definition. A page that comes back short is cosmetic; a page with a
    withdrawn person in it is a breach, so this runs after the SQL above and
    never instead of it.
    """
    if not profiles:
        return []
    allowed = has_consent_many(
        db, [profile.user_id for profile in profiles], purpose="talent_introduction"
    )
    return [profile for profile in profiles if profile.user_id in allowed]
