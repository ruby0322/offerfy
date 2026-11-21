"""The dashboard in one request.

Composition only: every number comes from a reader that already exists and is
projected by the same function the single-purpose route uses. Nothing here
calls a model or rebuilds resume facts; a page visit must not spend someone's
credits. The one write a GET makes is the digest's opened_at, once: the digest
build's eligibility gate reads it, so without the stamp nobody ever counts as
engaged and the digest stops being built for everyone.

**This GET does not move the "last looked at matches" marker, and must not.**
Four pages call it -- the overview, Applications, Matches and Profile -- and
only the overview renders `arrivals`. A GET that stamped would let someone
check an application status, close the window, and then read "nothing new
since you last looked" above fifteen unseen roles. The marker is still the
server's number, measured from users.matches_seen_at; what moved is who says
when to advance it. The page that showed the arrivals says so, once, through
POST /v1/me/matches/seen (routers/matches.py). The client supplies no `since`
-- that was refused by the design and stays refused -- only "I showed them".
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.jobs.match_sweep import arrival_rows, count_matches, match_preview
from app.models import Job, JobScore, User
from app.routers.applications import application_out
from app.services.jobs.search import iso, list_item
from app.services.jobs.preferences import preference_out
from app.models import Digest
from app.schemas_jobs import (
    DashboardOut,
    DigestItemOut,
    DigestOut,
    EmployerViewsOut,
    MatchArrivalOut,
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

# Cap on arrivals. A first visit has no window, so without a cap a broad
# preference would ship the whole match set on a GET that fires every page
# load. Newest first, so the cap drops the oldest, not the freshest.
ARRIVALS_CAP = 50


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


def _window_start(marker: datetime | None, now: datetime) -> datetime | None:
    """The lower edge of "since you last looked", or None for a first visit.

    A marker ahead of `now` (clock skew between workers, a replayed request)
    is clamped to `now`. Read literally it would be a window starting in the
    future, which is empty anyway but only by accident; the clamp makes the
    meaning explicit -- "they looked just now" -- and the stamp that follows
    repairs the stored value instead of preserving the skew. It is never
    widened into "everything": that is what NULL means, and only NULL.
    A naive value (SQLite in tests) is read as UTC.
    """
    if marker is None:
        return None
    if marker.tzinfo is None:
        marker = marker.replace(tzinfo=timezone.utc)
    return min(marker, now)


def arrival_out(job: Job, score: JobScore | None) -> MatchArrivalOut:
    """Project one arrival; score is None for a posting not yet scored."""
    return MatchArrivalOut(
        job_id=job.id,
        job_title=job.title,
        company=job.company,
        apply_url=job.apply_url,
        job_is_active=job.is_active,
        location=job.location,
        tier=score.tier if score else None,
        fit_score=score.fit_score if score else None,
        reason=score.reason if score else None,
        gap=score.gap if score else None,
        scored_at=iso(score.scored_at) if score else None,
    )


@router.get("/v1/dashboard", response_model=DashboardOut)
def get_dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    pref = current_or_seeded(db, user)
    now = datetime.now(timezone.utc)
    # The marker as the last page that SHOWED arrivals left it. Nothing in this
    # handler advances it; see the module docstring.
    previous = user.matches_seen_at
    first_visit = previous is None
    window = _window_start(previous, now)
    new_arrivals = [
        arrival_out(job, score)
        for job, score in arrival_rows(db, user.id, pref, window, ARRIVALS_CAP)
    ]
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
    out = DashboardOut(
        preference=preference_out(db, user, pref),
        match_count=count_matches(db, pref),
        arrivals=new_arrivals,
        first_visit=first_visit,
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
    return out
