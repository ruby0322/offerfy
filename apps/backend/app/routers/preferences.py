"""A person's job preferences, and the endpoint that builds their resume facts."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import JobPreference, User
from app.schemas_jobs import JobPreferenceIn, JobPreferenceOut, ResumeFactsRefreshOut
from app.services.jobs.preferences import get_or_seed
from app.services.metering import metered, preflight
from app.services.profile import record_build_spend
from app.services.resume_facts import build_facts, facts_are_stale, facts_for_user

router = APIRouter()


def _split(text: str) -> list[str]:
    return [p for p in text.split("|") if p]


def _pipe(values: list[str]) -> str:
    return f"|{'|'.join(values)}|" if values else ""


def _facts_state(db: Session, user: User) -> str:
    facts = facts_for_user(db, user)
    if facts is None:
        return "missing"
    return "stale" if facts_are_stale(db, user, facts) else "fresh"


def _out(db: Session, user: User, row: JobPreference) -> JobPreferenceOut:
    return JobPreferenceOut(
        target_functions=_split(row.target_functions),
        target_locations=_split(row.target_locations),
        target_seniority=row.target_seniority,
        remote_required=row.remote_required,
        activity=row.activity,
        updated_at=row.updated_at.isoformat(),
        facts_state=_facts_state(db, user),
    )


@router.get("/v1/me/job-preferences", response_model=JobPreferenceOut)
def get_job_preferences(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Seeds from the resume facts on first call and persists the seed, so the
    editor and the matcher agree about what the person is matched on."""
    row = get_or_seed(db, user)
    db.commit()
    return _out(db, user, row)


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
    row = get_or_seed(db, user)
    row.target_functions = _pipe(body.target_functions)
    row.target_locations = _pipe(body.target_locations)
    row.target_seniority = body.target_seniority
    row.remote_required = body.remote_required
    row.activity = body.activity
    db.commit()
    db.refresh(row)
    return _out(db, user, row)


@router.post("/v1/me/resume-facts/refresh", response_model=ResumeFactsRefreshOut)
async def refresh_resume_facts(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Build the person's resume facts now.

    The only production caller of build_facts. It is a POST, not a side effect
    of the GET, because extraction is a provider call that can take a minute
    and spends money. No consent is needed: these facts are the person's own
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
        facts_state=_facts_state(db, user), error=result.error
    )
