"""Sort scored candidates into the weekly digest's two tracks, and read digests back.

Two tracks, not one. A single "best matches" list that sometimes holds one
entry reads as failure -- "we found 1 role worth your time" -- whereas "1 new
role opened this week" reads as a fact. So:

  strong_fit     -- tier in STRONG_TIERS ("Strong fit", "Good fit"), whenever
                    the posting appeared.
  new_this_week  -- anything not "Not a fit" whose job_id is in the new set.

A posting can sit in both. Neither track is ever padded: a track with two
qualifying postings has two items, not five with blanks.

strong_fit deliberately has NO digest-based exclusion. A posting already shown
in last week's digest is shown again until the person acts on it; the
applications exclusion upstream is what drops it. Excluding on "already
digested" would make a strong match vanish merely because the person was busy.

`classify` is pure: no database, no clock, no I/O. The vocabularies in
STATUSES and TRACKS live here, not in CHECK constraints (see models.Digest).

Digest and DigestItem declare no relationships, so "items with their Job" is
`items_for`, one explicit join, rather than an eager-load option.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Digest, DigestItem, Job
from app.services.jobs.score import Scored

STATUSES = ("full", "degraded", "empty")
TRACKS = ("strong_fit", "new_this_week")
STRONG_TIERS = ("Strong fit", "Good fit")
ITEMS_PER_TRACK = 5

_NOT_A_FIT = "Not a fit"


def classify(
    scored: list[Scored],
    *,
    new_job_ids: set[str] | frozenset[str],
    items_per_track: int | None = None,
) -> dict[str, list[Scored]]:
    limit = ITEMS_PER_TRACK if items_per_track is None else items_per_track
    ranked = sorted(scored, key=lambda s: s.fit_score, reverse=True)  # stable
    strong = [s for s in ranked if s.tier in STRONG_TIERS]
    fresh = [s for s in ranked if s.tier != _NOT_A_FIT and s.job_id in new_job_ids]
    return {"strong_fit": strong[:limit], "new_this_week": fresh[:limit]}


def latest_for_user(db: Session, *, user) -> Digest | None:
    """The user's most recent digest by period_start, or None."""
    return db.scalars(
        select(Digest)
        .where(Digest.user_id == user.id)
        .order_by(Digest.period_start.desc())
        .limit(1)
    ).first()


def items_for(db: Session, *, digest: Digest) -> list[tuple[DigestItem, Job]]:
    """The digest's items paired with their Job, ordered by track then rank."""
    rows = db.execute(
        select(DigestItem, Job)
        .join(Job, Job.id == DigestItem.job_id)
        .where(DigestItem.digest_id == digest.id)
        .order_by(DigestItem.track, DigestItem.rank)
    ).all()
    return [(item, job) for item, job in rows]


def mark_opened(db: Session, *, digest: Digest) -> Digest:
    """Stamp opened_at the first time only; later calls change nothing."""
    if digest.opened_at is None:
        digest.opened_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(digest)
    return digest
