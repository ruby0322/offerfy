"""The dashboard in one request.

Composition only: every number comes from a reader that already exists and is
projected by the same function the single-purpose route uses. Nothing here
calls a model or rebuilds resume facts; a page visit must not spend someone's
credits. The one write a GET makes is stamping the digest's opened_at, once:
the digest build's eligibility gate reads it, so without the stamp nobody ever
counts as engaged and the digest stops being built for everyone.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.jobs.match_sweep import count_matches
from app.models import User
from app.routers.applications import application_out
from app.routers.preferences import preference_out
from app.models import Digest
from app.schemas_jobs import (
    DashboardOut,
    DigestItemOut,
    DigestOut,
    EmployerViewsOut,
    ResumeHealthOut,
)
from app.services.disclosure import disclosure_count, last_disclosed_at
from app.services.jobs import applications as pipeline_service
from app.services.jobs import digest as digest_service
from app.services.jobs.preferences import current_or_seeded
from app.services.resume_health import health

logger = logging.getLogger(__name__)

router = APIRouter()


def digest_out(digest: Digest) -> DigestOut:
    """Project a digest; each posting comes through the item's eager-loaded
    view-only `job`, so the whole digest costs no per-item query."""
    return DigestOut(
        status=digest.status,
        built_at=digest.built_at.isoformat(),
        opened_at=digest.opened_at.isoformat() if digest.opened_at else None,
        candidate_count=digest.candidate_count,
        items=[
            DigestItemOut(
                job_id=item.job_id,
                job_title=item.job.title,
                company=item.job.company,
                apply_url=item.job.apply_url,
                job_is_active=item.job.is_active,
                track=item.track,
                rank=item.rank,
                tier=item.tier,
                fit_score=item.fit_score,
                reason=item.reason,
                gap=item.gap,
            )
            for item in digest.items
        ],
    )


@router.get("/v1/dashboard", response_model=DashboardOut)
def get_dashboard(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    pref = current_or_seeded(db, user)
    last = last_disclosed_at(db, user)
    digest = digest_service.latest_for_user(db, user=user)
    if digest is not None:
        # The one write a dashboard GET makes; idempotent after the first.
        # "Opened" means the dashboard loaded, not that the digest was seen: a
        # prefetch, a collapsed card, an empty or degraded digest all count. It
        # is an engagement proxy (the build gate only needs the person to have
        # come back), not a read receipt.
        digest_id = digest.id
        try:
            digest = digest_service.mark_opened(db, digest=digest)
        except Exception:
            # A bookkeeping stamp must not take down the page. But a lost stamp
            # silently erodes the build's eligibility gate, so say so loudly.
            logger.exception(
                "dashboard: digest %s could not be marked opened for user %s; "
                "serving the dashboard anyway (eligibility may degrade)",
                digest_id,
                user.id,
            )
            db.rollback()
            digest = digest_service.latest_for_user(db, user=user)
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
        digest=digest_out(digest) if digest is not None else None,
    )
