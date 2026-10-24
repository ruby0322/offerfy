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
from app.services.metering import metered, preflight
from app.services.disclosure import disclosure_count, last_disclosed_at
from app.services.profile import (
    record_build_spend,
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

# The hourly build limit used to live here as REFRESH_LIMIT_PER_HOUR, with its
# own LlmCall count. It is now TIERS[...].profile_builds_per_hour, enforced by
# the same preflight as everything else -- "too much" had three definitions
# across three routers and now has one. The weekly cost budget applies to
# builds too, because it is the same money.


def _status(db: Session, user: User) -> TalentProfileStatus:
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

    if body.resume_id is not None:
        # Opportunistic: a failure here shows up in the status, it does not
        # refuse the choice they just made. The preflight can still refuse --
        # this is a model call like any other -- which is why it runs before
        # the build rather than being skipped for an "opportunistic" one.
        guard = preflight(db, user=user, guest=None, purpose="profile")
        async with metered(guard) as meter:
            result = await build_profile(db, user)
            if result.profile is not None:
                db.commit()
            else:
                db.rollback()
            record_build_spend(db, user, result, meter)
    return _status(db, user)


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
    # Replaces the local REFRESH_LIMIT_PER_HOUR check. Same hourly cap, now
    # from the tier, and the weekly cost budget applies too.
    guard = preflight(db, user=user, guest=None, purpose="profile")
    async with metered(guard) as meter:
        result = await build_profile(db, user)
        if result.profile is None:
            db.rollback()
            record_build_spend(db, user, result, meter)
            if result.error == "llm_not_configured":
                raise HTTPException(status_code=503, detail="Profile build unavailable")
            if result.error == "empty":
                raise HTTPException(
                    status_code=422, detail="This resume has too little to go on"
                )
            raise HTTPException(status_code=502, detail="Profile build failed")
        # Read the payload out before committing, so the response does not
        # depend on reloading an expired row.
        detail = detail_of(result.profile)
        db.commit()
        record_build_spend(db, user, result, meter)
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
