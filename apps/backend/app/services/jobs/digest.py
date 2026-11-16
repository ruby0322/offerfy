"""Sort scored candidates into the weekly digest's two tracks, and read digests back.

Two tracks, not one. A single "best matches" list that sometimes holds one
entry reads as failure -- "we found 1 role worth your time" -- whereas "1 new
role opened this week" reads as a fact. So:

  strong_fit     -- tier in STRONG_TIERS ("Strong fit", "Good fit"), whenever
                    the posting appeared.
  new_this_week  -- anything not "Not a fit" whose job_id is in the new set
                    AND that strong_fit is not already showing.

The tracks are DISJOINT, and strong_fit wins. A posting qualifying for both
used to be listed under each heading, which read as two different roles and
made the per-track counts sum to more than the digest held. strong_fit wins
because it is the first heading and it is capped by score: every posting it
drops scored below all five it kept, so the card's scores still fall as the
reader goes down it. Were "new" to win instead, the top heading would show
roles scoring BELOW the ones under the heading after it.

Disjointness is applied after strong_fit's cap, not before: a strong-tier
posting the cap pushed out is shown under new_this_week if the window admits
it, rather than being dropped from the digest altogether. So new_this_week
backfills -- each track still offers up to five, and the two together offer
up to ten DISTINCT postings, which is the number the spend ceiling was always
sized for.

Neither track is ever padded: a track with two qualifying postings has two
items, not five with blanks.

strong_fit deliberately has NO digest-based exclusion. A posting already shown
in last week's digest is shown again until the person acts on it; the
applications exclusion upstream is what drops it. Excluding on "already
digested" would make a strong match vanish merely because the person was busy.

`classify` is pure: no database, no clock, no I/O. The vocabularies in
STATUSES and TRACKS live here, not in CHECK constraints (see models.Digest).

`Digest.items` and `DigestItem.job` are view-only relationships;
`latest_for_user` eager-loads both.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Digest, DigestItem
from app.services.jobs.score import Scored

STATUSES = ("full", "degraded", "empty")
TRACKS = ("strong_fit", "new_this_week")

# The track a DEGRADED digest's items carry, and the only one `classify` never
# produces: it exists so that a row nothing judged cannot be filed under a name
# that judges it. Both real tracks are claims -- `strong_fit` asserts a tier
# the model never returned, and `new_this_week` is simply false for a posting
# first seen a month ago -- and a degraded digest is the prefilter's order and
# nothing more. Not in TRACKS, which is the vocabulary of the two tracks a
# reader displays; a consumer selecting on either of those can therefore never
# read a claim out of a degraded row, whether or not it remembered to check
# `status` first.
UNRANKED_TRACK = "unranked"
STRONG_TIERS = ("Strong fit", "Good fit")
ITEMS_PER_TRACK = 5

# An allowlist: the tiers are a closed vocabulary owned by score.py, and a
# malformed or empty tier must drop out rather than be shown as a match.
_NEW_TIERS = ("Strong fit", "Good fit", "Worth a look")


def classify(
    scored: list[Scored],
    *,
    new_job_ids: set[str] | frozenset[str],
    items_per_track: int | None = None,
) -> dict[str, list[Scored]]:
    limit = max(0, ITEMS_PER_TRACK if items_per_track is None else items_per_track)
    # Ties on score fall to job_id, so rank (persisted) does not depend on the
    # order the scoring pass happened to return.
    ranked = sorted(scored, key=lambda s: (-s.fit_score, s.job_id))
    strong = [s for s in ranked if s.tier in STRONG_TIERS][:limit]
    # The cap is applied FIRST, so this holds what strong_fit will actually
    # show -- not every strong-tier posting. A posting the cap dropped is
    # still eligible for new_this_week; one on screen above is not.
    shown = {s.job_id for s in strong}
    fresh = [
        s
        for s in ranked
        if s.tier in _NEW_TIERS and s.job_id in new_job_ids and s.job_id not in shown
    ]
    return {"strong_fit": strong, "new_this_week": fresh[:limit]}


def latest_for_user(db: Session, *, user) -> Digest | None:
    """The user's most recent digest by period_start, or None.

    Items (ordered by track, rank) and their Jobs are eager-loaded.
    """
    return db.scalars(
        select(Digest)
        .options(selectinload(Digest.items).joinedload(DigestItem.job))
        .where(Digest.user_id == user.id)
        .order_by(Digest.period_start.desc())
        .limit(1)
    ).first()


def mark_opened(db: Session, *, digest: Digest) -> Digest:
    """Stamp opened_at the first time only; later calls change nothing."""
    if digest.opened_at is None:
        digest.opened_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(digest)
    return digest
