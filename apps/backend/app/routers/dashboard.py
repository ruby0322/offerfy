"""The dashboard in one request.

Composition only: every number comes from a reader that already exists and is
projected by the same function the single-purpose route uses. Nothing here
calls a model or rebuilds resume facts; a page visit must not spend someone's
credits. The writes a GET makes are two stamps. The first is the digest's
opened_at, once: the digest build's eligibility gate reads it, so without the stamp nobody ever
counts as engaged and the digest stops being built for everyone. The second
is users.matches_seen_at, every visit, after the arrivals it bounds are read.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.jobs.match_sweep import count_matches, match_preview, prefilter_query
from app.models import Job, JobScore, User
from app.routers.applications import application_out
from app.services.jobs.search import iso, list_item
from app.routers.preferences import preference_out
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


def arrivals(
    db: Session, user: User, pref, since: datetime | None
) -> list[MatchArrivalOut]:
    """What is new to this person: scored since `since`, plus not-yet-scored.

    A LEFT JOIN from the postings to this person's score rows, not an inner
    join over scores. A broad preference fills in over several sweep passes, so
    for up to a day the prefilter holds postings job_scores has no row for;
    dropping them would make match_count disagree with the list under it, and
    showing them unlabelled would read as "poor match" rather than "not looked
    at yet". Those rows carry tier=None and scored_at=None.

    The window keys on scored_at for a scored posting (the moment the answer
    arrived, which is what is new to the reader) and on first_seen_at for an
    unscored one (so a posting they already saw unscored is not "new" again
    until it has a score). `since=None` is a first visit: everything.

    Visible means in the prefilter, or scored-and-since-closed. The prefilter
    requires is_active, so a posting that closed after being scored would
    otherwise vanish; it travels with job_is_active=False instead, and the
    page can say it closed. A score for a posting the current preference no
    longer matches stays out, so editing the preference does not resurrect the
    old one's arrivals.
    """
    matching = prefilter_query(db, pref).with_entities(Job.id).subquery()
    q = (
        db.query(Job, JobScore)
        .outerjoin(
            JobScore,
            and_(JobScore.job_id == Job.id, JobScore.user_id == user.id),
        )
        .filter(
            or_(
                Job.id.in_(select(matching.c.id)),
                and_(JobScore.id.isnot(None), Job.is_active.is_(False)),
            )
        )
    )
    if since is not None:
        q = q.filter(
            or_(
                and_(JobScore.id.isnot(None), JobScore.scored_at > since),
                and_(JobScore.id.is_(None), Job.first_seen_at > since),
            )
        )
    rows = (
        q.order_by(
            func.coalesce(JobScore.scored_at, Job.first_seen_at).desc(), Job.id.desc()
        )
        .limit(ARRIVALS_CAP)
        .all()
    )
    return [
        MatchArrivalOut(
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
        for job, score in rows
    ]


def stamp_matches_seen(db: Session, user: User, now: datetime) -> None:
    """Record that the person looked, so the next visit's window starts here."""
    user.matches_seen_at = now
    db.commit()


@router.get("/v1/dashboard", response_model=DashboardOut)
def get_dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    pref = current_or_seeded(db, user)
    now = datetime.now(timezone.utc)
    # Read the previous marker BEFORE anything stamps the new one: the window
    # describes the previous visit, so stamping first would empty every one.
    previous = user.matches_seen_at
    first_visit = previous is None
    window = _window_start(previous, now)
    new_arrivals = arrivals(db, user, pref, window)
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
    user_id = user.id
    try:
        stamp_matches_seen(db, user, now)
    except Exception:
        # Same treatment as the digest stamp above, same reason: a lost stamp
        # must not take down the page, but it silently leaves the marker
        # behind, so the same arrivals would be called new again next visit.
        logger.exception(
            "dashboard: matches_seen_at could not be stamped for user %s; "
            "serving the dashboard anyway (arrivals will repeat)",
            user_id,
        )
        db.rollback()
    return DashboardOut(
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
