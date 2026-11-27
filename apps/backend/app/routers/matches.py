"""The first run, and the "I showed them" marker the arrivals window turns on.

Scores live in a per-(person, posting) cache that the sweep fills four times a
day (`jobs.score_sweep`). This is the accelerator for the one moment that
matters: someone has just said what work they want, and the product should show
them something now rather than within six hours. It is an accelerator and not
the mechanism; a person who closes the tab is picked up by the next pass, which
is why nothing is queued at save time. See section 4 of
docs/superpowers/specs/2026-11-19-onboarding-first-run-design.md.

**The work is detached and owns its own database session.** The POST returns at
once and the scoring carries on, but `get_db` closes the request's session when
the response goes out. A task holding it would find it closed, or would race the
next request that reuses it. So `_run` opens a session from the factory, as the
`app/jobs` entry points do, and closes it in a `finally`. `sweep_user` commits
its own scores; a task that did not would lose what it paid for.

**The pass's queries are synchronous and run on the event loop, deliberately.**
`_run` is a task, so everything in `sweep_user` that is not an `await` on httpx
occupies the loop. The trade: the prefilter scan is tens of milliseconds at
this catalogue size (about 20k postings) and the pass is a handful of
statements, against the cost and risk of moving a function that awaits provider
calls onto a thread. What was NOT acceptable was an unbounded wait, and that
was a row lock rather than a scan -- see the claim below. Do not try to thread
an async function; if the loop time ever becomes the problem, the pass has to
move out of the API process entirely, which is what `score_sweep`'s own unit
already is.

**Spend is the platform's.** The scoring and any facts rebuild inside
`sweep_user` run under `system_guard`, never `preflight`: billing a signup's
calls to a registered account's 20 weekly edit credits would spend their
allowance on something they cannot see being spent.

**Progress is the `job_scores` rows, counted.** No progress table and no stream:
the rows are durable and are the thing being produced, so a reload or a second
tab reads the same number. Nothing about a run is held in this process, because
`offerfy-api` runs more than one worker and a poll may land on any of them.
That is also why there is no `running` flag: rows are facts, "still working" is
not derivable from them, and a response the server cannot answer truthfully is
worse than a silent one. See `get_progress` for what the client may conclude.

**Only a person with no scores at all may start one.** That is what "first"
means, and it bounds re-triggering without a rate limit.

**And only one at a time.** Two tabs posting at once both see zero rows, so
that test cannot separate them; `_claim_first_run` can, with one conditional
UPDATE on `users.first_run_started_at`. The loser is told `already_running`
and starts nothing.

The design blessed the duplicate -- "the window is seconds and the loser
writes the same answer" -- and that is true on SQLite and false on Postgres.
There, the loser's first `write_back` insert hits the unique key the winner
inserted and BLOCKS on the winner's transaction until it commits, which is
the end of the winner's entire pass. `_run` is scheduled with `create_task`,
so that block is a synchronous psycopg2 call sitting on the event loop: for
those seconds the worker serves nothing, `/healthz` included. The claim
removes the contention rather than making the pass safe to block in.

The claim is in the database and not in this process because `offerfy-api`
runs more than one worker and the two tabs may land on different ones. `_run`
hands it back in its `finally`, whatever happened in the pass, so a run that
crashed or bought nothing does not leave the next POST told
`already_running` -- which would be false, and said for ten minutes. The
release is conditional on the timestamp this run claimed, so it cannot clear a
newer run's claim. The expiry below is now the backstop for a worker that died
before reaching that `finally`, not the only way a claim is ever freed. It
expires (`FIRST_RUN_CLAIM_TTL`) so a run whose worker died does not lock the
person out for good; and `write_back`'s savepoint upsert stays, because a
first run can still overlap a `score_sweep` pass for the same person in
another process, which this claim does not cover and does not pretend to.

**Over the global ceiling it says matching is paused and promises no time.** The
sweep, the digest and this endpoint draw on one ceiling, so "the sweep will get
to you" is the very thing that has run out.

**`POST /v1/me/matches/seen` is the arrivals window's only advance.** The window
stays server-measured -- `users.matches_seen_at`, no caller-supplied `since`,
which the design refused and this does not reintroduce. What the client says is
narrower: "I have rendered the arrivals you gave me." It has to be the client,
because `GET /v1/dashboard` is called by four pages and only one of them shows
arrivals; a stamp on the GET closed the window for a reader who went to check
an application status. The timestamp written is the server's `now`, never a
value from the request body, so a wrong clock on the caller cannot move it.

It is idempotent in the only sense that matters: calling it twice leaves the
marker at the second `now`, and anything scored in between is correctly no
longer new, because the page it was shown on was the one asking. A failure
cannot take a page down -- the response the person is reading has already been
served -- so this endpoint lets the fault surface as a 500 rather than
swallowing it, and the caller treats it as fire-and-forget.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db import get_db, get_session_factory
from app.deps import require_user
from app.jobs.build_digests import (
    _ceiling_uusd,
    _over_ceiling,
    build_for_user,
    saved_preference,
)
from app.jobs.match_sweep import count_matches
from app.jobs.score_sweep import SWEEP_CAP, sweep_user
from app.models import JobScore, User
from app.schemas_jobs import FirstRunOut, MatchProgressOut, MatchesSeenOut
from app.services import usage
from app.services.resume_facts import bundle

logger = logging.getLogger(__name__)

router = APIRouter()

# The event loop keeps only a weak reference to a task, so one nobody holds can
# be collected mid-run. Held here until it finishes.
_running: set[asyncio.Task] = set()

# How long a claim stands before another run may take it. A first run is about
# fifteen pairs and a few seconds, so ten minutes is far longer than any real
# one and still short enough that a worker killed mid-pass does not lock the
# person out: they would otherwise sit at "0 of 15" with no way to retry, and
# the only repair would be the next sweep pass up to six hours later.
#
# This is a backstop, not the normal path: `_run` releases the claim in its
# `finally`, so the TTL only matters when the worker died before getting
# there. It is deliberately not shortened to match a pass's real duration --
# a slow provider must not let a second run start while the first is still
# buying pairs.
FIRST_RUN_CLAIM_TTL = timedelta(minutes=10)


def _scored_count(db: Session, user: User) -> int:
    return db.execute(
        select(func.count()).select_from(JobScore).where(JobScore.user_id == user.id)
    ).scalar_one()


def _claim_first_run(db: Session, user: User, now: datetime) -> bool:
    """Take the one claim to run for this person, or report that it is taken.

    One statement, so two tabs on two workers cannot both win: the WHERE does
    the deciding and `rowcount` reports who did. A claim older than
    FIRST_RUN_CLAIM_TTL is stale and may be taken again.

    The commit is immediate and on purpose: an uncommitted claim is invisible
    to the other worker, which is the whole thing this prevents.
    """
    stale_before = now - FIRST_RUN_CLAIM_TTL
    result = db.execute(
        update(User)
        .where(
            User.id == user.id,
            or_(
                User.first_run_started_at.is_(None),
                User.first_run_started_at < stale_before,
            ),
        )
        .values(first_run_started_at=now)
        # The database decides, not the ORM: with the default synchronisation
        # SQLAlchemy re-evaluates the WHERE in Python against objects in this
        # session, which raises on a naive stored value against an aware `now`
        # (SQLite stores naive) and would mean the claim was decided twice, in
        # two places, by two rules.
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return bool(result.rowcount)


def _release_first_run(db: Session, user_id: str, claimed: datetime) -> bool:
    """Give the claim back, but only if it is still ours.

    `claimed` is the exact timestamp this run wrote. A run that outlived
    FIRST_RUN_CLAIM_TTL may have had its claim taken by a newer one, and
    clearing that would be worse than the bug this function fixes: the new
    owner would be running with no claim and a third tab could start a
    duplicate. So the WHERE carries the timestamp and the database decides,
    exactly as `_claim_first_run` does.
    """
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.first_run_started_at == claimed)
        .values(first_run_started_at=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return bool(result.rowcount)


async def _run(user_id: str, claimed: datetime) -> None:
    """The detached work, on a session of its own. Never raises.

    The claim is released in the `finally`, so a pass that crashed or bought
    nothing does not leave the next POST answered `already_running` -- a
    sentence that would be false for up to FIRST_RUN_CLAIM_TTL. The TTL is now
    the backstop for a worker that died without running this, not the only way
    a claim is ever freed.
    """
    session = get_session_factory()()
    try:
        user = session.get(User, user_id)
        if user is None:
            return
        # Commits its own scores; rolls back and re-raises on a fault.
        bought = await sweep_user(session, user=user, cap=SWEEP_CAP)
        if bought is None:
            logger.warning(
                "first run bought nothing, facts unbuildable: user=%s", user_id
            )
        # The other half of the run, and the reason progress reports `running`
        # rather than a row count: the overview's left column is the weekly
        # digest, and it used to be weekly ONLY. Someone who saved a preference
        # on a Friday got an empty "this week's picks" until Monday 05:00 --
        # four days of a card that reads as broken, on the one screen this
        # whole design exists to make pay off. The scores the sweep has just
        # committed are exactly what the digest ranks, so this costs the prose
        # call and nothing else.
        #
        # This week's period_start, the same one `build_digests.main` uses, so
        # Monday's run REPLACES this digest (see `_write`) instead of
        # colliding with it or leaving two records of one week.
        #
        # Its own try: the sweep has already committed, so a digest fault must
        # not be logged as a scoring fault and must not take the scores with
        # it. `build_for_user` rolls its own transaction back before raising.
        try:
            await build_for_user(
                session, user=user, period_start=usage.window_start()
            )
        except Exception:
            logger.exception("first run digest failed: user=%s", user_id)
            session.rollback()
    except Exception:
        logger.exception("first run failed: user=%s", user_id)
        session.rollback()
    finally:
        # Its own try: a failure to release must not replace the real error in
        # the log, and must not stop the session being closed.
        try:
            _release_first_run(session, user_id, claimed)
        except Exception:
            logger.exception("first run claim not released: user=%s", user_id)
            session.rollback()
        session.close()


def _start(user_id: str, claimed: datetime) -> None:
    task = asyncio.get_running_loop().create_task(_run(user_id, claimed))
    _running.add(task)
    task.add_done_callback(_running.discard)


@router.post("/v1/me/matches/first-run", response_model=FirstRunOut)
async def start_first_run(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Start scoring this person's matches, or say why it did not."""
    if _scored_count(db, user) > 0:
        return FirstRunOut(started=False, reason="already_scored")
    pref = saved_preference(db, user)
    if pref is None:
        # The sweep's audience is SAVED preference rows only (`saved_preference`);
        # a seed is a guess it deliberately never scores against. So a person
        # with only a seed would get a run that does nothing and a progress of
        # 0 out of 0 forever. Do not "simplify" this to `current_or_seeded`.
        return FirstRunOut(started=False, reason="no_preference")
    if pref.activity == "paused":
        # "The person asked for this to stop" is honoured here as everywhere
        # else: `sweep_all` skips a paused preference through `is_eligible`,
        # and `build_digests` refuses one even under `--all-users`, which
        # bypasses every other gate. This endpoint was the one scoring path
        # that ignored it.
        return FirstRunOut(started=False, reason="activity_paused")
    # Free to ask. build_facts would answer the same with an error, but only
    # after paying for an extraction.
    if bundle(db, user, fall_back=True) is None:
        return FirstRunOut(started=False, reason="no_resume")
    ceiling = _ceiling_uusd()
    if ceiling is not None and _over_ceiling(db, ceiling=ceiling, now=None):
        return FirstRunOut(started=False, reason="budget_paused")
    # Last, and immediately before the task is scheduled: a claim taken and
    # then refused for some other reason would block the retry that reason
    # invites. Nothing below here can refuse.
    claimed = datetime.now(timezone.utc)
    if not _claim_first_run(db, user, claimed):
        return FirstRunOut(started=False, reason="already_running")
    # The same timestamp travels with the task, which is how it can give the
    # claim back without clobbering a newer one.
    _start(user.id, claimed)
    return FirstRunOut(started=True, reason=None)


@router.post("/v1/me/matches/seen", response_model=MatchesSeenOut)
def mark_matches_seen(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Advance the arrivals window: the caller has shown this person their arrivals.

    The server's own `now` is what is written. The caller says only that it
    showed them; it does not get to say when, which is what keeps the window
    one number from one clock. See the module docstring for why this is not a
    side effect of the dashboard GET.
    """
    now = datetime.now(timezone.utc)
    user.matches_seen_at = now
    db.commit()
    return MatchesSeenOut(seen_at=now.isoformat())


@router.get("/v1/me/matches/progress", response_model=MatchProgressOut)
def get_progress(db: Session = Depends(get_db), user: User = Depends(require_user)):
    """How many matches are scored, out of how many a run will produce.

    **The client decides when to stop polling.** `scored < expected` means "not
    finished as far as we can see", never "still working": a run that died
    leaves it true forever, and so does a person who never started one. The
    caller must bound its own polling and not wait on this endpoint to say stop.

    **Progress is binary.** `sweep_user` commits once, at the end of its pass,
    and that single commit is the transaction boundary the sweep owns. So
    `scored` reads 0 until the pass finishes and then reads the final count; it
    does not rise. A caller that wants to show motion should show an
    indeterminate indicator, not a percentage. A typical first run is about
    fifteen pairs and a few seconds.
    """
    pref = saved_preference(db, user)
    expected = min(count_matches(db, pref), SWEEP_CAP) if pref is not None else 0
    scored = _scored_count(db, user)
    return MatchProgressOut(
        scored=scored, expected=expected, running=_claim_is_live(user),
    )


def _claim_is_live(user: User, now: datetime | None = None) -> bool:
    """Whether a first run is still working for this person.

    A claim this old is one `_claim_first_run` would hand to the next caller,
    so reporting it as live would keep a client waiting on a worker that is
    never coming back. That is the bound on how wrong this answer can be: a
    pass whose process died is reported as running for at most
    FIRST_RUN_CLAIM_TTL, and the client's own poll limit is shorter than that.

    Compared naively on both sides because SQLite stores what it was given
    without a zone, which is the same trap `_claim_first_run` avoids by doing
    its comparison in the database.
    """
    claimed = user.first_run_started_at
    if claimed is None:
        return False
    now = datetime.now(timezone.utc) if now is None else now
    bare = claimed if claimed.tzinfo is None else claimed.astimezone(timezone.utc).replace(tzinfo=None)
    cutoff = (now - FIRST_RUN_CLAIM_TTL).replace(tzinfo=None)
    return bare > cutoff
