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
from app.jobs.match_sweep import count_matches, match_preview
from app.models import User
from app.routers.applications import application_out
from app.routers.jobs import list_item
from app.routers.preferences import preference_out
from app.schemas_jobs import DashboardOut, EmployerViewsOut, ResumeHealthOut
from app.services.disclosure import disclosure_count, last_disclosed_at
from app.services.jobs import applications as pipeline_service
from app.services.jobs.preferences import current_or_seeded
from app.services.resume_health import health

router = APIRouter()

# How many matching postings travel with the count.
#
# The dashboard shows the roles themselves now rather than only how many
# there are, and it shows them in the jobs page's own rows -- so the number
# is a cap on a preview, not a page size: "see all N" goes to /jobs, which
# is where paging lives. Five fills the card on a phone without turning the
# dashboard into a second jobs page.
MATCH_PREVIEW = 5


@router.get("/v1/dashboard", response_model=DashboardOut)
def get_dashboard(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    pref = current_or_seeded(db, user)
    last = last_disclosed_at(db, user)
    return DashboardOut(
        preference=preference_out(db, user, pref),
        match_count=count_matches(db, pref),
        matches=[list_item(row) for row in match_preview(db, pref, MATCH_PREVIEW)],
        applications=[
            application_out(row) for row in pipeline_service.pipeline(db, user=user)
        ],
        resume_health=ResumeHealthOut(**asdict(health(db, user=user))),
        employer_views=EmployerViewsOut(
            disclosure_count=disclosure_count(db, user),
            last_disclosed_at=last.isoformat() if last else None,
        ),
    )
