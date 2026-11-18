"""Score new postings four times a day, and buy prose for the ones that fit.

Usage (compose):
  docker compose run --rm backend python -m app.jobs.score_sweep
  docker compose run --rm backend python -m app.jobs.score_sweep --dry-run

THIS JOB SPENDS MONEY, unattended, against the production database. The guards
below are the requirement and the scoring is the thing they fence in. Scoring
lives in a per-(person, posting) cache (`score_cache`); this job walks the
audience every six hours, buys only the pairs nobody has scored yet, and so
makes "matches renew daily" true without a daily artifact. A pass over a pool
that has not changed costs nothing. See section 3 of
docs/superpowers/specs/2026-11-19-onboarding-first-run-design.md, which carries
the reasoning for each ruling recorded here.

It sends nothing. Offerfy emails nobody: a failure here means some rows went
unscored, not that a message is stuck in a queue.

**Audience: `build_digests.is_eligible`, unchanged.** There is one answer to "is
this person still looking", and the weekly digest still feeds the signal it
reads. Paused preferences are skipped; a first-timer with a preference row is
in, which is what makes this job the durable backstop for the first-run
endpoint.

**`SWEEP_CAP` = 30 purchases per user per pass, newest posting first.** A
typical preference's whole eligible pool is about 15 postings, so it finishes
in one pass; a broad one (about 910 eligible) fills in over several. It bounds
the cold-start cost of the first pass after deploy, the one pass where no cache
exists. The cap is on PURCHASES: pairs already scored are free and do not count
against it.

**Stale facts are rebuilt before scoring**, under `system_guard` with purpose
`profile`, as `build_digests._facts_for_scoring` does it, and SKIPPED when the
person has nothing to judge: the candidate set is free SQL and the rebuild is a
provider call. Nothing else repairs `ResumeFacts` (`refresh_profiles` rebuilds
`TalentProfile` only), so without this the sweep would write judgements against
a resume the person has replaced. The cost is one extraction per resume edit,
not one per pass, because afterwards the facts are fresh and scores are
compared against the rebuilt row's hash. A rebuild that fails means that person
is skipped this pass, never scored against the old resume.

**Prose only for arrivals that land in `STRONG_TIERS`.** About one new posting a
week for a typical preference, so one or two calls per user per week, and a
fresh role that fits arrives explained instead of waiting for Monday. "Arrival"
means a pair bought THIS pass: a strong pair scored earlier is the digest's to
explain. Prose is recorded through `score_cache.record_prose`, so the cache
keeps one writer; a sentence the validator dropped is recorded as
`prose_rejected` and not bought again. A failed prose call records nothing and
is not retried by this job: the score stands and the digest writes the sentence.

**`system_guard` for all three purposes** (`profile`, `match`, `digest`), never
`preflight`. Nobody is waiting on an HTTP response, so `preflight` would only
make the job 429 itself.

**One shared spend ceiling, and no reserve.** The sweep, the weekly digest and
the first-run endpoint all draw on `global_weekly_ceiling_uusd`,
first-come-first-served. That was a decision, not an oversight: a per-pass
ceiling and a reserved slice for first runs were both considered and declined.
The consequence is that the sweep can exhaust the week. The ceiling is checked
ONCE per user, before they start, and never inside one, so a user who starts
always finishes and no pass leaves a half-scored pool. Users not reached are
listed by id in `SweepReport.skipped_over_ceiling`; that list is the signal to
watch after the timer is enabled.

**Order: longest-unswept first**, by each person's newest `job_scores` row, so a
pass cut short by the ceiling does not starve the same tail every time. The
same self-correcting argument as `build_digests.build_order`.

**`prune` is one global statement, outside the per-user loop and outside the
ceiling check**, and runs even on a pass that scores nothing. Inside the loop it
would inherit the loop's audience, and someone who stops opening digests leaves
that audience after two weeks, so their rows would never be visited again and
never pruned: the unbounded growth pruning exists to prevent, merely relocated.
Running out of money to score is no reason to stop deleting dead rows. It runs
first, so that a crash later in the pass does not skip it.

**One transaction per user**, committed at the end of that user's pass, and a
per-user `except` that rolls back: one person's database fault must not cost
everyone else the pass. A crash discards that user's purchases, which are
bought again next pass; the same bargain `build_digests` records.

**`--dry-run` buys nothing and writes nothing.** The timer ships disabled and is
enabled only after a dry run has shown what a pass costs, so the number it
prints is the one an operator needs: how many pairs would be bought, and for how
many people. It ignores the ceiling on purpose (a pass that the ceiling would
cut short is still the number to know), and it counts a person whose facts are
stale as buying their whole capped pool, since the rebuild invalidates every
score they hold. Those people are listed in `would_rebuild_facts`, because each
is one more provider call that the pair count does not include.

`SweepReport` has no `failed` list, as `BuildReport` has none: the exception is
the log's business, and a pass where it fires for everyone shows up as an empty
`swept`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_engine, get_session_factory
from app.jobs.build_digests import (
    _ceiling_uusd,
    _naive,
    _over_ceiling,
    is_eligible,
    saved_preference,
)
from app.jobs.match_sweep import prefilter_query
from app.models import Job, JobScore, User
from app.services.jobs.digest import STRONG_TIERS
from app.services.jobs.reasons import write_reasons
from app.services.jobs.score_cache import cached_scores, record_prose, stale_pairs
from app.services.jobs.store import freshness_cutoff
from app.services.metering import metered, system_guard
from app.services.profile import record_build_spend
from app.services.resume_facts import (
    FactsBuildResult,
    build_facts,
    facts_are_stale,
    facts_for_user,
)

logger = logging.getLogger(__name__)

# Purchases per user per pass. One Decisions call per pair, so this is the
# per-user bill of a pass; a module constant AND a parameter on both entry
# points, so changing it for one run is a call-site change.
SWEEP_CAP = 30


@dataclass
class SweepReport:
    """What one pass did, by user id.

    Every person the pass reached lands in one list, with the exception
    `BuildReport` makes: someone whose resume facts cannot be built is logged,
    not counted. There is deliberately no `failed` list.
    """

    swept: list[str] = field(default_factory=list)
    skipped_not_engaged: list[str] = field(default_factory=list)
    skipped_no_preference: list[str] = field(default_factory=list)
    skipped_over_ceiling: list[str] = field(default_factory=list)
    # Rows deleted (on a dry run, rows that would be).
    pruned: int = 0
    # Pairs bought (on a dry run, pairs that would be), across everyone.
    bought: int = 0
    # The same number by person, so an operator can see how it is distributed.
    bought_by_user: dict[str, int] = field(default_factory=dict)
    # Dry run only: whose facts a real pass would rebuild first.
    would_rebuild_facts: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# pieces
# --------------------------------------------------------------------------


def _pool(db: Session, user: User) -> list[Job]:
    """Every posting this person's preference could match, newest first.

    Ordered like `build_digests.candidates` and for the same reason: the cap has
    to pick a defined set, and recency is the ordering that needs no model. The
    whole pool, not a capped slice, because the cap here is on PURCHASES: a
    cached pair costs nothing and must not use up the allowance.
    """
    pref = saved_preference(db, user)
    if pref is None:
        return []
    return prefilter_query(db, pref).order_by(Job.first_seen_at.desc(), Job.id.asc()).all()


async def _rebuild_facts(db: Session, user: User):
    """Rebuild this person's facts under `system_guard`; None if that fails."""
    guard = system_guard(
        user=user,
        purpose="profile",
        reason=(
            "score sweep: the resume these matches are judged against is "
            "rebuilt before scoring; no HTTP caller to refuse"
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
        logger.info("sweep skipped, facts unbuildable: user=%s %s", user.id, result.error)
    return result.facts


async def _buy_prose(db: Session, *, user: User, facts, scored, jobs) -> None:
    """Prose for strong-tier arrivals, recorded through the cache's one writer."""
    strong = [s for s in scored if s.tier in STRONG_TIERS]
    if not strong:
        return
    by_id = {str(j.id): j for j in jobs}
    run = await write_reasons(
        db, user=user, facts=facts, scored=strong,
        jobs=[by_id[s.job_id] for s in strong],
    )
    if run.error is not None:
        # Nothing recorded: the score stands, and the digest will explain it.
        logger.warning("sweep prose failed: user=%s %s", user.id, run.error)
        return
    for item in run.prose:
        record_prose(
            db, user=user, job=by_id[item.job_id],
            reason=item.reason, gap=item.gap, model=run.model,
        )
    for job_id, _why in run.dropped:
        if job_id in by_id:
            record_prose(db, user=user, job=by_id[job_id], rejected=True, model=run.model)


async def sweep_user(db: Session, *, user: User, cap: int = SWEEP_CAP) -> int | None:
    """Score what is new for one person. Returns pairs bought, or None.

    None means there were facts to be built and none could be; the caller does
    not count that person as swept. Commits once, at the end: the scores, the
    prose and nothing half-done. A real exception propagates after a rollback.
    """
    try:
        # The candidate set first: it is free, and the rebuild is not.
        pool = _pool(db, user)
        if not pool:
            return 0
        facts = facts_for_user(db, user)
        if facts is None or facts_are_stale(db, user, facts):
            facts = await _rebuild_facts(db, user)
            if facts is None:
                return None
        # What a purchase is, by the cache's own definition, in the order the
        # cache will buy it. Bought pairs are the prose candidates.
        arrivals = stale_pairs(db, user=user, facts=facts, jobs=pool)[: max(0, cap)]
        if not arrivals:
            return 0
        run = await cached_scores(db, user=user, facts=facts, jobs=pool, cap=cap)
        if run.error is not None:
            logger.warning("sweep could not buy every pair: user=%s %s", user.id, run.error)
        arrived = {str(j.id) for j in arrivals}
        fresh = [s for s in run.scored if s.job_id in arrived]
        await _buy_prose(db, user=user, facts=facts, scored=fresh, jobs=arrivals)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(fresh) + len([j for j in run.refused if j in arrived])


def _dead_scores():
    """Rows whose posting is inactive or past the freshness cutoff."""
    dead = select(Job.id).where(
        or_(Job.is_active.is_(False), Job.last_seen_at < freshness_cutoff())
    )
    return JobScore.job_id.in_(dead)


def prune(db: Session, *, dry_run: bool = False) -> int:
    """Delete scores for postings that are gone. One statement, no audience.

    The FK cascade only covers deletion, and postings are flipped inactive
    rather than deleted, so without this the table grows forever. Global on
    purpose: see the module docstring. Returns the rows deleted (on a dry run,
    the rows that would be, and nothing is deleted).
    """
    if dry_run:
        return db.execute(
            select(func.count()).select_from(JobScore).where(_dead_scores())
        ).scalar_one()
    count = db.execute(delete(JobScore).where(_dead_scores())).rowcount
    db.commit()
    return count or 0


def sweep_order(db: Session) -> list[User]:
    """Every user, longest-unswept first. Never-swept people go before everyone."""
    newest = {
        user_id: stamp
        for user_id, stamp in db.execute(
            select(JobScore.user_id, func.max(JobScore.scored_at)).group_by(JobScore.user_id)
        ).all()
    }
    users = db.query(User).all()
    return sorted(users, key=lambda u: (_naive(newest.get(u.id)), u.id))


def _preview(db: Session, user: User, cap: int, report: SweepReport) -> int:
    """A dry run's answer for one person: what a real pass would buy. No writes."""
    pool = _pool(db, user)
    if not pool:
        return 0
    facts = facts_for_user(db, user)
    if facts is None or facts_are_stale(db, user, facts):
        # The rebuild invalidates every score they hold, so the whole pool is
        # stale by then. (With no facts at all it may yet fail to build; the
        # dry run cannot know without paying to find out.)
        report.would_rebuild_facts.append(user.id)
        return min(len(pool), max(0, cap))
    return min(len(stale_pairs(db, user=user, facts=facts, jobs=pool)), max(0, cap))


async def sweep_all(
    db: Session,
    *,
    now: datetime | None = None,
    cap: int | None = None,
    dry_run: bool = False,
) -> SweepReport:
    """Walk the audience, buy what is new, stop before the ceiling."""
    cap = SWEEP_CAP if cap is None else cap
    report = SweepReport()
    # Outside the loop and outside the ceiling, even when nothing is scored.
    report.pruned = prune(db, dry_run=dry_run)
    ceiling = _ceiling_uusd()
    stopped = False

    for user in sweep_order(db):
        pref = saved_preference(db, user)
        if pref is None:
            report.skipped_no_preference.append(user.id)
            continue
        # Audience before money: deciding not to sweep costs nothing, and
        # recording a paused person as "over the ceiling" would be false.
        if not is_eligible(db, user=user, now=now):
            report.skipped_not_engaged.append(user.id)
            continue
        if dry_run:
            n = _preview(db, user, cap, report)
            report.swept.append(user.id)
            report.bought_by_user[user.id] = n
            report.bought += n
            continue
        # Once per user, never inside one: a user who starts always finishes.
        if ceiling is not None and not stopped:
            stopped = _over_ceiling(db, ceiling=ceiling, now=now)
        if stopped:
            report.skipped_over_ceiling.append(user.id)
            continue
        try:
            bought = await sweep_user(db, user=user, cap=cap)
        except Exception:
            # Per-user, like build_all: one person's crash must not cost
            # everyone else the pass. No `failed` list by design. The rollback
            # is belt and braces; sweep_user has already done it.
            logger.exception("score sweep failed: user=%s", user.id)
            db.rollback()
            continue
        if bought is None:
            logger.info("sweep skipped, no usable facts: user=%s", user.id)
            continue
        report.swept.append(user.id)
        report.bought_by_user[user.id] = bought
        report.bought += bought
    return report


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.score_sweep")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many pairs a pass would buy, and for how many people; buy nothing",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    get_engine()
    session = get_session_factory()()
    try:
        report = asyncio.run(sweep_all(session, now=now, dry_run=args.dry_run))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        session.close()
    people = sum(1 for n in report.bought_by_user.values() if n > 0)
    verb = "would buy" if args.dry_run else "bought"
    prune_verb = "would prune" if args.dry_run else "pruned"
    print(
        f"{verb} {report.bought} pairs for {people} people "
        f"(swept {len(report.swept)}, facts to rebuild "
        f"{len(report.would_rebuild_facts)}); {prune_verb} {report.pruned}; "
        f"skipped: no_preference {len(report.skipped_no_preference)}, "
        f"not_engaged {len(report.skipped_not_engaged)}, "
        f"over_ceiling {len(report.skipped_over_ceiling)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
