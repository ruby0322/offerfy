"""The dashboard in one request.

Composition only: every number comes from a reader that already exists and is
projected by the same function the single-purpose route uses. Nothing here
writes (the preference may be a transient seed) and nothing here calls a model
or rebuilds resume facts; a page visit must not spend someone's credits.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.jobs.match_sweep import count_matches
from app.models import User
from app.routers.applications import application_out
from app.routers.preferences import preference_out
from app.schemas_jobs import DashboardOut, EmployerViewsOut, ResumeHealthOut
from app.services.disclosure import disclosure_count, last_disclosed_at
from app.services.jobs import applications as pipeline_service
from app.services.jobs.preferences import current_or_seeded
from app.services.resume_health import health

router = APIRouter()


@router.get("/v1/dashboard", response_model=DashboardOut)
def get_dashboard(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    pref = current_or_seeded(db, user)
    last = last_disclosed_at(db, user)
    return DashboardOut(
        preference=preference_out(db, user, pref),
        match_count=count_matches(db, pref),
        applications=[
            application_out(row) for row in pipeline_service.pipeline(db, user=user)
        ],
        resume_health=ResumeHealthOut(**asdict(health(db, user=user))),
        employer_views=EmployerViewsOut(
            disclosure_count=disclosure_count(db, user),
            last_disclosed_at=last.isoformat() if last else None,
        ),
    )
