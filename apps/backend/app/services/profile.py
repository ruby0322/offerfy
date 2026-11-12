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

build_profile is a projection: it asks resume_facts.extract_facts for one result and
writes both talent_profiles (gated) and resume_facts (ungated) from it, under
one input_hash. It must never call the provider twice.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    LlmCall,
    Resume,
    TalentProfile,
    User,
)
from app.schemas import (
    TalentProfileDetail,
    TalentProfilePayload,
    TalentProfileSummary,
    TALENT_SUMMARY_SKILLS,
)
from app.services.consent import has_consent, has_consent_many
from app.services.resume_facts import (
    bundle,
    canonical_json,
    derived_columns,
    extract_facts,
    input_hash,
    source_resume,
    write_facts,
)
from app.services.usage import event_cost_uusd

logger = logging.getLogger(__name__)


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
    found = bundle(db, user)
    if found is None:
        return False
    _resume, revision_id, source, facts = found
    if profile.source_revision_id != revision_id:
        return True
    return profile.input_hash != input_hash(revision_id, source, facts)


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

    Consent is required here, not only on the read path: the talent_profiles
    row exists only for the talent_introduction purpose. The extraction itself
    needs no grant -- it is shared with resume_facts -- so it runs once and
    this writes both rows from that one result under one input_hash.
    """
    if not has_consent(db, user=user, purpose="talent_introduction"):
        return ProfileBuildResult(error="no_consent")
    extraction = await extract_facts(db, user)
    result = ProfileBuildResult(
        error=extraction.error,
        usage=extraction.usage,
        model=extraction.model,
        source_chars=extraction.source_chars,
        resume_id=extraction.resume_id,
    )
    payload = extraction.payload
    if payload is None:
        return result

    # Canonical JSON: the bytes are what content_hash commits to, and
    # disclosure_events will cite that hash as evidence of what a recipient
    # holds.
    blob = canonical_json(payload)
    columns = derived_columns(payload)
    profile = profile_for_user(db, user)
    if profile is None:
        profile = TalentProfile(user_id=user.id)
        db.add(profile)
    profile.source_resume_id = extraction.resume_id
    profile.source_revision_id = extraction.revision_id
    profile.input_hash = extraction.input_hash
    profile.profile_json = blob
    profile.content_hash = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    profile.model = extraction.model[:128]
    # Set on every write, not just the insert: built_at is what the owner and
    # the reader see as this payload's date.
    profile.built_at = _utcnow()
    for column, value in columns.items():
        setattr(profile, column, value)
    db.flush()
    write_facts(db, user, extraction)
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
