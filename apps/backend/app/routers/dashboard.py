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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.jobs.match_sweep import count_matches, match_preview
from app.models import User
from app.routers.applications import application_out
from app.routers.jobs import list_item
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

# How many matching postings travel with the count.
#
# The dashboard shows the roles themselves now rather than only how many
# there are, and it shows them in the jobs page's own rows -- so the number
# is a cap on a preview, not a page size: "see all N" goes to /jobs, which
# is where paging lives. Five fills the card on a phone without turning the
# dashboard into a second jobs page.
#
# Raised from 5 to 12 when the Matches tab landed: the overview shows three
# of these and the tab shows the rest grouped by the day they arrived, which
# needs more than a card's worth. Still a cap on a preview rather than a
# page size -- "see all" goes to /jobs, which is where paging lives.
MATCH_PREVIEW = 12


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


def _since(raw: str | None) -> datetime | None:
    """Parse the caller's "what is new to me" marker, or None.

    A bad value is treated as absent rather than as an error: the line it
    feeds is a nicety, and refusing the whole dashboard over a malformed
    timestamp from a stale tab would be the wrong trade. A naive value is read
    as UTC so a comparison against first_seen_at cannot raise.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@router.get("/v1/dashboard", response_model=DashboardOut)
def get_dashboard(
    since: str | None = Query(
        default=None,
        description=(
            "ISO timestamp of the caller's previous visit. When given, the "
            "response carries new_match_count: postings first seen after it."
        ),
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    pref = current_or_seeded(db, user)
    window = _since(since)
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
        new_match_count=(
            None if window is None else count_matches(db, pref, since=window)
        ),
        matches=[list_item(row) for row in match_preview(db, pref, MATCH_PREVIEW)],
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
