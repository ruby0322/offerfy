"""A person's job preferences, and the endpoint that builds their resume facts."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.jobs.match_sweep import count_matches
from app.models import JobPreference, User
from app.schemas_jobs import JobPreferenceIn, JobPreferenceOut, ResumeFactsRefreshOut
from app.services import preferences_write
from app.services.jobs.preferences import current_or_seeded, preference_out
from app.services.metering import metered, preflight
from app.services.profile import record_build_spend
from app.services.resume_facts import build_facts, facts_state

router = APIRouter()


@router.get("/v1/me/job-preferences", response_model=JobPreferenceOut)
def get_job_preferences(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """The saved preferences, or, before the first save, a seed computed from the
    resume facts and not stored. Storing happens only on PUT."""
    row = current_or_seeded(db, user)
    return preference_out(db, user, row)


@router.put("/v1/me/job-preferences", response_model=JobPreferenceOut)
def put_job_preferences(
    body: JobPreferenceIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Replace the preferences wholesale. This is not a patch: a field omitted
    from the body is cleared (activity returns to its default)."""
    row, first_save = preferences_write.save(db, user, body)
    return preference_out(db, user, row, first_save=first_save)


@router.get("/v1/me/matches/count")
def get_match_count(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict[str, int]:
    """How many active postings pass the person's preference filter.

    Uses the saved row, or the unsaved seed, so it agrees with what the editor
    shows. A preference that constrains nothing counts as 0, not the catalogue.
    """
    return {"count": count_matches(db, current_or_seeded(db, user))}


@router.post("/v1/me/resume-facts/refresh", response_model=ResumeFactsRefreshOut)
async def refresh_resume_facts(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Build the person's resume facts now.

    The only caller of build_facts a person waits on; the weekly digest build
    rebuilds stale facts too, under system_guard.

    A POST, not a side effect of the GET, because extraction is a provider
    call that can take a minute and spends money. Unlike the digest's, this
    build was asked for, so it goes through preflight and does count against
    the person's allowance.

    No consent is needed: these facts are the person's own, and no employer
    can see them.
    """
    guard = preflight(db, user=user, guest=None, purpose="profile")
    async with metered(guard) as meter:
        result = await build_facts(db, user)
        # Settle the build's own transaction before recording spend, which
        # commits on its own.
        if result.facts is not None:
            db.commit()
        else:
            db.rollback()
        record_build_spend(db, user, result, meter)
    return ResumeFactsRefreshOut(
        facts_state=facts_state(db, user), error=result.error
    )
