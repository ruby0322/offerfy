"""A person's job preferences, and the endpoint that builds their resume facts."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.jobs.match_sweep import count_matches
from app.models import JobPreference, User
from app.schemas_jobs import JobPreferenceIn, JobPreferenceOut, ResumeFactsRefreshOut
from app.services.jobs.preferences import current_or_seeded
from app.services.metering import metered, preflight
from app.services.profile import record_build_spend
from app.services.resume_facts import build_facts, facts_state

router = APIRouter()


def _split(text: str) -> list[str]:
    return [p for p in text.split("|") if p]


def _pipe(values: list[str]) -> str:
    return f"|{'|'.join(values)}|" if values else ""


def preference_out(db: Session, user: User, row: JobPreference) -> JobPreferenceOut:
    return JobPreferenceOut(
        target_functions=_split(row.target_functions),
        target_locations=_split(row.target_locations),
        target_seniority=row.target_seniority,
        remote_required=row.remote_required,
        activity=row.activity,
        # A transient seed has no row yet, hence no timestamp. null, never "":
        # an empty string in a timestamp field trips the first client that
        # parses it.
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
        facts_state=facts_state(db, user),
    )


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
    # Mutate the loaded row in place: updated_at's onupdate fires only on an
    # UPDATE, so delete-and-reinsert would reset it to creation time.
    row = current_or_seeded(db, user)
    if row not in db:
        db.add(row)
    row.target_functions = _pipe(body.target_functions)
    row.target_locations = _pipe(body.target_locations)
    row.target_seniority = body.target_seniority
    row.remote_required = body.remote_required
    row.activity = body.activity
    db.commit()
    db.refresh(row)
    return preference_out(db, user, row)


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

    The only caller of build_facts a person waits on: the weekly digest build
    rebuilds stale facts too, under system_guard. This one is a POST, not a
    side effect of the GET, because extraction is a provider call that can
    take a minute and spends money -- and, unlike the digest's, this build is
    asked for, so it goes through preflight and does count against them. No consent is needed: these facts are the person's own
    and no employer can see them.
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
