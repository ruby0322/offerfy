"""The owner's side of the talent profile.

Everything here requires a signed-in user, so a guest can never reach it --
and talent_profiles has no guest column at all, which makes "a guest has no
profile" a fact about the schema rather than a check someone has to remember.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import LlmCall, Resume, User
from app.schemas import (
    TalentProfileDetail,
    TalentProfileSourceUpdate,
    TalentProfileStatus,
)
from app.services.consent import has_consent
from app.services.disclosure import disclosure_count, last_disclosed_at
from app.services.profile import (
    build_profile,
    delete_profile_for_user,
    detail_of,
    is_stale,
    payload_of,
    profile_for_user,
    source_resume,
)
from app.services.revisions import published_source

router = APIRouter()

# A build is one model call the owner pays for in latency and we pay for in
# tokens. Metered on LlmCall rows rather than on a new table, because the row
# is written anyway.
REFRESH_LIMIT_PER_HOUR = 5


def _recent_builds(db: Session, user: User) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    return (
        db.query(LlmCall)
        .join(Resume, Resume.id == LlmCall.resume_id)
        .filter(
            LlmCall.purpose == "profile",
            LlmCall.created_at >= since,
            Resume.user_id == user.id,
        )
        .count()
    )


def _record_spend(db: Session, result) -> None:
    """Profile builds are a billable path and belong in LlmCall like the rest.

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


def _status(
    db: Session, user: User, *, build_error: str | None = None
) -> TalentProfileStatus:
    resume = source_resume(db, user)
    profile = profile_for_user(db, user)
    published = bool(
        resume is not None
        and resume.published_revision_id
        and published_source(db, resume)
    )
    last = last_disclosed_at(db, user)
    return TalentProfileStatus(
        consent_granted=has_consent(db, user=user, purpose="talent_introduction"),
        source_resume_id=resume.id if resume else None,
        source_resume_title=resume.title if resume else None,
        has_published_revision=published,
        profile=payload_of(profile) if profile else None,
        stale=is_stale(db, user, profile) if profile else False,
        built_at=profile.built_at.isoformat() if profile else None,
        # Read from the ledger, not from the profile: a withdrawal deletes the
        # profile and keeps the disclosures, and after re-granting the person
        # still needs to see who already had the old one.
        disclosure_count=disclosure_count(db, user),
        last_disclosed_at=last.isoformat() if last else None,
        build_error=build_error,
    )


@router.get("/v1/me/talent-profile", response_model=TalentProfileStatus)
def get_talent_profile(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    return _status(db, user)


@router.put("/v1/me/talent-profile/source", response_model=TalentProfileStatus)
async def put_talent_profile_source(
    body: TalentProfileSourceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Choose which resume represents this person, or clear the choice."""
    if body.resume_id is not None:
        resume = db.get(Resume, body.resume_id)
        if resume is None or resume.user_id != user.id:
            raise HTTPException(status_code=404, detail="Not found")
    previous = profile_for_user(db, user)
    user.profile_resume_id = body.resume_id
    # A profile built from a resume they no longer point at must not be
    # servable, so the old one goes before the new one is attempted.
    if previous is not None and previous.source_resume_id != body.resume_id:
        delete_profile_for_user(db, user)
    db.commit()

    if body.resume_id is None:
        return _status(db, user)

    # Opportunistic: a failure here is reported in the status, it does not
    # refuse the choice they just made. It has to be reported, though --
    # returning 200 with no profile and no reason is indistinguishable from
    # never having tried, which is how the first real failure went unnoticed.
    result = await build_profile(db, user)
    if result.profile is not None:
        db.commit()
    else:
        db.rollback()
    _record_spend(db, result)
    return _status(db, user, build_error=result.error)


@router.post("/v1/me/talent-profile/refresh", response_model=TalentProfileDetail)
async def refresh_talent_profile(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    if not has_consent(db, user=user, purpose="talent_introduction"):
        raise HTTPException(status_code=409, detail="Turn on Open to matching first")
    resume = source_resume(db, user)
    if resume is None:
        raise HTTPException(status_code=409, detail="Choose a resume first")
    if not (resume.published_revision_id and published_source(db, resume)):
        raise HTTPException(status_code=409, detail="Publish this resume first")
    if _recent_builds(db, user) >= REFRESH_LIMIT_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many rebuilds")

    result = await build_profile(db, user)
    if result.profile is None:
        db.rollback()
        _record_spend(db, result)
        if result.error == "llm_not_configured":
            raise HTTPException(status_code=503, detail="Profile build unavailable")
        if result.error == "empty":
            raise HTTPException(
                status_code=422, detail="This resume has too little to go on"
            )
        raise HTTPException(status_code=502, detail="Profile build failed")
    # Read the payload out before committing, so the response does not depend
    # on reloading an expired row.
    detail = detail_of(result.profile)
    db.commit()
    _record_spend(db, result)
    return detail


@router.delete("/v1/me/talent-profile", status_code=204)
def delete_talent_profile(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Drop the profile without answering the consent question either way.

    Deliberately leaves consent alone: deleting what we built is not the same
    decision as withdrawing permission to build it, and the ledger is not
    ours to write on their behalf.
    """
    delete_profile_for_user(db, user)
    db.commit()
    return Response(status_code=204)
