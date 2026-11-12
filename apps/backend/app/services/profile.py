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

The extraction itself -- prompt, response schema, contact scrubbing, caps, the
derived search columns, and reading the source resume -- lives in
app.services.resume_facts, because job matching needs the same facts for
people who have not granted talent_introduction. What stays here is what is
specific to the employer-facing profile: the consent gate, the provider call,
the talent_profiles row, staleness, the gated read path and spend recording.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    LlmCall,
    Resume,
    TalentProfile,
    User,
)
from app.schemas import (
    TALENT_NAME_CHARS,
    TALENT_SUMMARY_SKILLS,
    TalentProfileDetail,
    TalentProfilePayload,
    TalentProfileSummary,
)
from app.services.account import display_name_of
from app.services.consent import has_consent, has_consent_many
from app.services.llm import _output_text, _responses_url
from app.services.llm.providers import openai as openai_provider
from app.services.model_catalog import spec_or_passthrough
from app.services.resume_facts import (
    PROFILE_FALLBACK_MODEL,
    PROFILE_TIMEOUT,
    bundle as _bundle,
    clamp_payload,
    cut as _cut,
    derived_columns,
    input_hash as _input_hash,
    is_empty as _is_empty,
    request_body as _request_body,
    scrub_contacts,
    source_resume,
    usage_of as _usage_of,
)
from app.services.usage import event_cost_uusd

logger = logging.getLogger(__name__)


# Kept so existing importers of the old private name keep working.
_derived = derived_columns


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    # Same rule as the chat router: priced once, here, at today's rates. The
    # meter below recomputes it from the same usage dict through the same
    # function, so the row and the counter cannot disagree.
    cost, _ = event_cost_uusd({"model": result.model, **result.usage})
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
                cost_uusd=cost,
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
    # the editor, and on a fixed model rather than this person's choice. Two
    # reasons, and both are about the output rather than the cost: this call
    # has to return JSON that validates against TalentProfilePayload, and
    # `_request_body` shapes a Responses-API structured-output request to get
    # it. A profile build is also not an interaction anyone is watching, so
    # there is nothing for a preference to be a preference about.
    #
    # That fixed model is PROFILE_FALLBACK_MODEL, *not* the catalogue default
    # -- see that constant for why the catalogue default cannot serve here.
    #
    # It is still metered like any other call, which is why the model id below
    # is recorded: see jobs/refresh_profiles.py and the system_guard it holds.
    settings = get_settings()
    if not openai_provider.OpenAIProvider().configured():
        return ProfileBuildResult(error="llm_not_configured", resume_id=resume.id)
    pin = settings.pinned_chat_model()
    pinned = spec_or_passthrough(pin) if pin else None
    # An operator pin is honoured only when it names an OpenAI model: pinning
    # an Anthropic one must not take this feature down with it. Anything else
    # -- no pin, or a pin naming a non-OpenAI model -- falls back to
    # PROFILE_FALLBACK_MODEL.
    spec = (
        pinned
        if pinned is not None and pinned.provider == openai_provider.NAME
        else spec_or_passthrough(PROFILE_FALLBACK_MODEL)
    )
    if spec.provider != openai_provider.NAME:
        # The fallback itself did not resolve to an OpenAI model -- nothing
        # usable to call, so this makes no request at all rather than sending
        # the wrong provider's id to this endpoint.
        return ProfileBuildResult(error="llm_not_configured", resume_id=resume.id)
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
    columns = derived_columns(payload)
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
