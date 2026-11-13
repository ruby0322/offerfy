"""Build one weekly digest per eligible person, and stop before the ceiling.

Offerfy emails nobody. This job writes the `digests` row the dashboard reads,
so "the digest went out" means "the digest was written down".

Usage (compose):
  docker compose run --rm backend python -m app.jobs.build_digests
  docker compose run --rm backend python -m app.jobs.build_digests --user-id U

**Who gets built for**, from the spec: everyone whose `job_preferences.activity`
is not `paused` **and** who opened one of their last two digests. First-time
users with a preference row are always built for once. A churned user therefore
stops costing money after two weeks, with no separate churn model and no
unsubscribe flow to maintain: weekly spend tracks engagement rather than
signups. No preference row means no digest at all -- the dashboard shows a
setup card seeded from `resume_facts`, because guessing silently and then
showing weak matches loses someone on first impression.

The rule lives in `is_eligible` rather than inline in the loop so that it can
be read, tested and bypassed (`--all-users`) on its own. `paused` is not part
of the bypass: the person asked for this to stop.

**Where the money stops.** `settings.global_weekly_ceiling_uusd` is checked
ONCE before each user is started, and never again inside one. That is the whole
mechanism behind "finish the user in flight, then stop": a user who has started
always completes, so no digest can be half-scored, and the job never 429s
itself. Users not reached are listed by id in `BuildReport.skipped_over_ceiling`.

**One transaction per user.** Every row of a digest -- the header and all its
items -- is added and committed together at the very end. A failure anywhere
before that leaves NO digest row, rather than a header that renders as "we
found nothing for you" when in fact the build crashed. The one deliberate
exception is the facts rebuild below, which settles first because the facts are
the person's own state and are worth keeping whatever happens to this week's
digest.

**Stale facts are rebuilt BEFORE scoring, never after.** Scoring first and
refreshing after produces a digest judged on text the person has since changed,
which reads as a bug in the score rather than in the schedule. A rebuild that
fails means no digest: judging someone against a resume we know they have
replaced is the exact failure the order exists to prevent, and the dashboard's
resume-health card already carries the reason.

**Metering.** Three paid calls, all under `system_guard` and none under
`preflight`: scoring (`purpose="match"`, in score.py), prose
(`purpose="digest"`, in reasons.py) and the facts rebuild (`purpose="profile"`,
here). Nobody is waiting on an HTTP response, so refusing would only mean the
job rate-limits itself; and a digest is not an edit, so it must not consume the
person's edit allowance -- they did not ask for it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine, get_session_factory
from app.jobs.match_sweep import prefilter_query
from app.models import Digest, DigestItem, Job, JobPreference, User
from app.services import usage
from app.services.jobs.digest import ITEMS_PER_TRACK, TRACKS, classify
from app.services.jobs.reasons import write_reasons
from app.services.jobs.score import score_candidates
from app.services.metering import metered, system_guard, window_totals
from app.services.profile import record_build_spend
from app.services.resume_facts import (
    FactsBuildResult,
    build_facts,
    facts_are_stale,
    facts_for_user,
)

logger = logging.getLogger(__name__)

# How many prefilter survivors get scored per user. One Decisions call per
# pair, so this is the per-user bill: ~30 x (facts ~2k + posting ~500) input
# tokens. A module constant AND a parameter on both entry points -- changing
# the number for one run is a call-site change, not an edit here.
CANDIDATE_CAP = 30

# The digest the engagement rule reads: how many weeks of being ignored it
# takes. Two, from the spec.
ENGAGEMENT_WINDOW = 2

# Ordering sentinel for a person who has never had a digest: they go first.
_NEVER = datetime(1970, 1, 1)


@dataclass
class BuildReport:
    """What one run did, by user id.

    Every user the run reached lands in one list, with one deliberate
    exception: someone whose resume facts cannot be built is logged rather than
    counted, because the spec gives that outcome no audience question to answer
    -- the dashboard's resume-health card is already their answer.
    """

    built: list[str] = field(default_factory=list)
    skipped_no_preference: list[str] = field(default_factory=list)
    skipped_not_engaged: list[str] = field(default_factory=list)
    skipped_over_ceiling: list[str] = field(default_factory=list)
    # Built, but on the prefilter alone: a subset of `built`, not a fourth
    # skip. Worth its own list because a run where every digest is degraded is
    # a provider outage, not a quiet week.
    degraded: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------


def saved_preference(db: Session, user: User) -> JobPreference | None:
    """The person's SAVED preference row, or None.

    Deliberately not `preferences.current_or_seeded`, which answers a different
    question: it returns a transient seed computed from the facts for someone
    who has never saved, and the existence of a row is precisely the "the user
    has edited this" marker. Building a digest off a seed would be the silent
    guess the spec rules out, and a seed that constrains nothing would make it
    an empty one at that.
    """
    return db.query(JobPreference).filter(JobPreference.user_id == user.id).one_or_none()


def is_eligible(db: Session, *, user: User, now: datetime | None = None) -> bool:
    """Whether this person is in the audience for a digest this week.

    `now` places the week: only digests from BEFORE the current spend window
    count, so the digest being built (or rebuilt) is never evidence about
    itself -- nobody can have opened a digest that was written a second ago.
    A backfill of an older week should therefore pass a `now` inside that week,
    or `require_eligible=False`.
    """
    pref = saved_preference(db, user)
    if pref is None or pref.activity == "paused":
        return False
    opened = db.execute(
        select(Digest.opened_at)
        .where(
            Digest.user_id == user.id,
            Digest.period_start < usage.window_start(now),
        )
        .order_by(Digest.period_start.desc())
        .limit(ENGAGEMENT_WINDOW)
    ).all()
    # No history at all: a first-time user with a preference row is always
    # built for once. What they do with it decides next week.
    if not opened:
        return True
    return any(stamp is not None for (stamp,) in opened)


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------


def candidates(db: Session, pref: JobPreference, *, cap: int) -> list[Job]:
    """The capped candidate set, freshest first.

    `prefilter_query` has no ORDER BY, and the digest needs one for two
    reasons: the cap has to pick a defined 30, and the degraded path shows the
    prefilter's own order as the whole answer. Recency is the ordering the
    prefilter can defend without a model -- `first_seen_at DESC`, tie-broken on
    `id` so the answer does not depend on the plan -- and it is also the only
    one that keeps the `new_this_week` track reachable: an order that put old
    high-scoring postings first would cap the fresh ones out of the set
    entirely.

    The spec's per-user soft score (spotlight's recency half-life and company
    spread, per user instead of globally) is NOT implemented. Recency is the
    half of it that a weekly digest actually depends on; the diversity loop is
    a later refinement over the same seam.
    """
    return (
        prefilter_query(db, pref)
        .order_by(Job.first_seen_at.desc(), Job.id.asc())
        .limit(max(0, cap))
        .all()
    )


def _new_job_ids(db: Session, jobs: list[Job], *, period_start: datetime) -> set[str]:
    """Which candidates first appeared inside the period.

    Asked in SQL rather than by comparing `job.first_seen_at` in Python: SQLite
    hands those columns back naive and Postgres hands them back aware, and
    comparing one with an aware `period_start` raises.
    """
    if not jobs:
        return set()
    rows = db.execute(
        select(Job.id).where(
            Job.id.in_([j.id for j in jobs]), Job.first_seen_at >= period_start
        )
    ).all()
    return {job_id for (job_id,) in rows}


def _input_hash(*, facts, period_start: datetime, jobs: list[Job], model: str) -> str:
    """What this digest was built from, as one 64-char fingerprint.

    The facts fingerprint rather than the facts: `resume_facts.input_hash` is
    already the hash of the resume this judgement was made against, so a
    rebuild after a resume change gets a different value while a rebuild of the
    same week gets the same one.
    """
    parts = [
        getattr(facts, "input_hash", "") or "",
        period_start.isoformat(),
        model,
        ",".join(j.id for j in jobs),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------


async def _facts_for_scoring(db: Session, user: User):
    """Fresh facts for this person, rebuilding first if they are stale.

    Returns None when there is nothing to score against. `system_guard`, not
    `preflight`: see the module docstring.
    """
    facts = facts_for_user(db, user)
    if facts is not None and not facts_are_stale(db, user, facts):
        return facts

    guard = system_guard(
        user=user,
        purpose="profile",
        reason=(
            "weekly digest build: the resume these matches are judged against "
            "is rebuilt before scoring; no HTTP caller to refuse"
        ),
    )
    async with metered(guard) as meter:
        result: FactsBuildResult = await build_facts(db, user)
        # Settle the build's own transaction before recording spend, which
        # commits on its own. A failed call may still have been billed, so the
        # spend is recorded either way.
        if result.facts is not None:
            db.commit()
        else:
            db.rollback()
        record_build_spend(db, user, result, meter)
    if result.facts is None:
        logger.info("digest skipped, facts unbuildable: user=%s %s", user.id, result.error)
    return result.facts


async def build_for_user(
    db: Session,
    *,
    user: User,
    period_start: datetime,
    now: datetime | None = None,
    items_per_track: int | None = None,
    candidate_cap: int | None = None,
    require_eligible: bool = True,
) -> Digest | None:
    """Build and write this person's digest for one week.

    Returns None when they are not in the audience, have no preference row, or
    have no facts to be judged against. Raises nothing of its own: a provider
    fault is an answer (`degraded`), while a real exception propagates AFTER
    the transaction is rolled back, so a failure leaves no digest row at all.
    """
    pref = saved_preference(db, user)
    if pref is None:
        return None
    if pref.activity == "paused":
        return None
    if require_eligible and not is_eligible(db, user=user, now=now):
        return None

    facts = await _facts_for_scoring(db, user)
    if facts is None:
        return None

    cap = CANDIDATE_CAP if candidate_cap is None else candidate_cap
    limit = ITEMS_PER_TRACK if items_per_track is None else items_per_track
    try:
        pool = candidates(db, pref, cap=cap)
        tracks, model, status = await _judge(
            db, user=user, facts=facts, pool=pool,
            period_start=period_start, limit=limit,
        )
        digest = _write(
            db, user=user, period_start=period_start, facts=facts, pool=pool,
            model=model, status=status, tracks=tracks,
        )
        db.commit()
    except Exception:
        # One transaction per user: nothing half-written survives a crash.
        db.rollback()
        raise
    db.refresh(digest)
    return digest


async def _judge(db, *, user, facts, pool, period_start, limit):
    """Score the pool and write prose for what will be shown.

    Returns (items by track, model, status). Reads the session, never writes
    to it, so the caller's transaction is still empty when this returns and a
    provider fault leaves nothing to roll back.
    """
    if not pool:
        # Nothing matched, so nothing is asked of a provider and nothing is
        # paid for. A model name would be a claim that one was consulted.
        return {track: [] for track in TRACKS}, "", "empty"

    run = await score_candidates(db, user=user, facts=facts, jobs=pool)
    if run.error is not None:
        # Row one of the spec's failure table: the prefilter order is the whole
        # answer, with no tiers, no scores and no prose. Silently reordering by
        # a worse signal while still showing confident tiers is the thing the
        # spec forbids, so the judgement columns stay NULL and the card says
        # the digest is degraded.
        logger.warning("digest degraded: user=%s %s", user.id, run.error)
        return {"strong_fit": [], "new_this_week": list(pool[:limit])}, run.model, "degraded"

    tracks = classify(
        run.scored,
        new_job_ids=_new_job_ids(db, pool, period_start=period_start),
        items_per_track=limit,
    )
    shown = {s.job_id: s for items in tracks.values() for s in items}
    if not shown:
        # Scored, and nothing qualified. "empty" is about what there is to
        # show, not about why: `candidate_count` is how a reader tells a thin
        # week from an unmatched preference.
        return {track: [] for track in TRACKS}, run.model, "empty"

    by_id = {job.id: job for job in pool}
    # Prose is bought only for the items that will be shown: the call is priced
    # per posting it carries, and a candidate that did not make either track
    # has nothing to say to anyone.
    prose = await write_reasons(
        db, user=user, facts=facts,
        scored=list(shown.values()),
        jobs=[by_id[job_id] for job_id in shown],
    )
    if prose.error is not None:
        # Row three: the ranking is real, so this is not a degraded digest --
        # just a bare one. The rows render without their sentence.
        logger.warning("digest prose failed: user=%s %s", user.id, prose.error)
    written = {p.job_id: p for p in prose.prose}
    return (
        {track: [(s, written.get(s.job_id)) for s in items] for track, items in tracks.items()},
        run.model,
        "full",
    )


def _write(db, *, user, period_start, facts, pool, model, status, tracks) -> Digest:
    """Add the digest and its items. Flushes, never commits.

    A rebuild of the same week REPLACES: the unique constraint on
    (user_id, period_start) says one digest per week, and deleting inside this
    transaction means a crash halfway leaves last build's digest intact.
    """
    existing = db.execute(
        select(Digest).where(
            Digest.user_id == user.id, Digest.period_start == period_start
        )
    ).scalar_one_or_none()
    if existing is not None:
        db.query(DigestItem).filter(DigestItem.digest_id == existing.id).delete()
        db.delete(existing)
        db.flush()

    digest = Digest(
        user_id=user.id,
        period_start=period_start,
        built_at=datetime.now(timezone.utc),
        # The scoring model, not the prose one: it is what produced the
        # judgement the person is shown. A degraded digest records the model
        # that was unreachable; an empty one records none.
        model=model[:64],
        input_hash=_input_hash(
            facts=facts, period_start=period_start, jobs=pool, model=model
        ),
        status=status,
        # The pool that was considered, which is what the card counts from.
        # On the degraded path nothing in it was scored, and `status` is how a
        # reader knows that.
        candidate_count=len(pool),
    )
    db.add(digest)
    db.flush()

    for track in TRACKS:
        for rank, entry in enumerate(tracks.get(track) or [], start=1):
            db.add(_item(digest, track=track, rank=rank, entry=entry))
    db.flush()
    return digest


def _item(digest: Digest, *, track: str, rank: int, entry) -> DigestItem:
    if isinstance(entry, Job):
        # The degraded path. `new_this_week` rather than `strong_fit` because
        # both track names are claims and only one of them is survivable here:
        # recency is what the prefilter order gives us, while `strong_fit`
        # would assert a judgement nothing made. The card renders the degraded
        # notice instead of the track labels (see the spec's Surfaces).
        return DigestItem(digest_id=digest.id, job_id=entry.id, rank=rank, track=track)
    scored, prose = entry
    return DigestItem(
        digest_id=digest.id,
        job_id=scored.job_id,
        rank=rank,
        track=track,
        tier=scored.tier,
        fit_score=scored.fit_score,
        reason=None if prose is None else prose.reason,
        gap=None if prose is None else prose.gap,
        requirements_met=scored.components.get("requirements_met"),
        seniority_fit=scored.components.get("seniority_fit"),
        location_fit=scored.components.get("location_fit"),
        direction_fit=scored.components.get("direction_fit"),
    )


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


def _ceiling_uusd() -> int | None:
    raw = get_settings().global_weekly_ceiling_uusd
    return raw if raw and raw > 0 else None


def _over_ceiling(db: Session, *, ceiling: int, now: datetime | None) -> bool:
    return window_totals(db, window_start=usage.window_start(now)) >= ceiling


def _naive(value: datetime | None) -> datetime:
    """A sortable period_start. SQLite returns these naive, Postgres aware."""
    if value is None:
        return _NEVER
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def build_order(db: Session) -> list[User]:
    """Every user, longest-without-a-digest first.

    The order matters because the ceiling can cut a run short, and a fixed
    order by id would starve the same tail every week. Sorting on the newest
    digest each person has makes the run self-correcting: whoever was cut last
    week is at the front of this one. Sorted in Python rather than in SQL
    because NULLS FIRST is a dialect difference and the population here is
    small enough that one pass over it is free.
    """
    newest = {
        user_id: period
        for user_id, period in db.execute(
            select(Digest.user_id, func.max(Digest.period_start)).group_by(Digest.user_id)
        ).all()
    }
    users = db.query(User).all()
    return sorted(users, key=lambda u: (_naive(newest.get(u.id)), u.id))


async def build_all(
    db: Session,
    *,
    period_start: datetime,
    now: datetime | None = None,
    items_per_track: int | None = None,
    candidate_cap: int | None = None,
    require_eligible: bool = True,
) -> BuildReport:
    """Walk every user, build for those in the audience, stop on the ceiling."""
    report = BuildReport()
    ceiling = _ceiling_uusd()
    stopped = False

    for user in build_order(db):
        pref = saved_preference(db, user)
        if pref is None:
            report.skipped_no_preference.append(user.id)
            continue
        # Audience before money: deciding not to build costs nothing, and
        # recording a paused person as "over the ceiling" would be false.
        if (require_eligible or pref.activity == "paused") and not is_eligible(
            db, user=user, now=now
        ):
            report.skipped_not_engaged.append(user.id)
            continue
        # Once per user, never inside one: a user who starts always finishes,
        # which is what makes the stop clean instead of a half-scored digest.
        if ceiling is not None and not stopped:
            stopped = _over_ceiling(db, ceiling=ceiling, now=now)
        if stopped:
            report.skipped_over_ceiling.append(user.id)
            continue
        try:
            digest = await build_for_user(
                db, user=user, period_start=period_start, now=now,
                items_per_track=items_per_track, candidate_cap=candidate_cap,
                require_eligible=require_eligible,
            )
        except Exception:
            # Per-user, like refresh_profiles: one person's crash must not cost
            # everyone else their week. The transaction is already rolled back,
            # so nothing of theirs was written. BuildReport has no `failed`
            # list by design -- the exception is the log's business, and a run
            # where this fires for everyone shows up as an empty `built`.
            logger.exception("digest build failed: user=%s", user.id)
            continue
        if digest is None:
            # Preference and audience were settled above, so the only reason
            # left is that there are no facts to judge them against.
            logger.info("digest skipped, no usable facts: user=%s", user.id)
            continue
        report.built.append(user.id)
        if digest.status == "degraded":
            report.degraded.append(user.id)
    return report


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def _period(raw: str | None, now: datetime) -> datetime:
    """The week to build. Defaults to the current spend window.

    Same boundary as the meter (Monday 00:00 Taipei), so a digest's week and
    the week its cost lands in are the same week.
    """
    if not raw:
        return usage.window_start(now)
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.build_digests")
    parser.add_argument(
        "--period-start",
        help="ISO date or datetime; defaults to this week's window start",
    )
    parser.add_argument("--user-id", help="Build for one person only")
    parser.add_argument(
        "--all-users",
        action="store_true",
        help="Ignore the engagement gate (paused preferences are still skipped)",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    period_start = _period(args.period_start, now)

    get_engine()
    session = get_session_factory()()
    try:
        if args.user_id:
            user = session.get(User, args.user_id)
            if user is None:
                print(f"no such user: {args.user_id}", file=sys.stderr)
                return 1
            digest = asyncio.run(
                build_for_user(
                    session, user=user, period_start=period_start, now=now,
                    require_eligible=not args.all_users,
                )
            )
            print(f"built {0 if digest is None else 1}")
            return 0
        report = asyncio.run(
            build_all(
                session, period_start=period_start, now=now,
                require_eligible=not args.all_users,
            )
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()
    print(
        f"built {len(report.built)} (degraded {len(report.degraded)}); "
        f"skipped: no_preference {len(report.skipped_no_preference)}, "
        f"not_engaged {len(report.skipped_not_engaged)}, "
        f"over_ceiling {len(report.skipped_over_ceiling)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
