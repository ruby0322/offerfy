"""The first run: score a brand-new account's matches while they are still here.

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
means, and it bounds re-triggering without a rate limit. Two tabs posting at
once both see zero rows; `write_back` is an upsert on (user, posting), so the
duplicate costs a little money and corrupts nothing.

**Over the global ceiling it says matching is paused and promises no time.** The
sweep, the digest and this endpoint draw on one ceiling, so "the sweep will get
to you" is the very thing that has run out.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db, get_session_factory
from app.deps import require_user
from app.jobs.build_digests import _ceiling_uusd, _over_ceiling, saved_preference
from app.jobs.match_sweep import count_matches
from app.jobs.score_sweep import SWEEP_CAP, sweep_user
from app.models import JobScore, User
from app.schemas_jobs import FirstRunOut, MatchProgressOut
from app.services.resume_facts import bundle

logger = logging.getLogger(__name__)

router = APIRouter()

# The event loop keeps only a weak reference to a task, so one nobody holds can
# be collected mid-run. Held here until it finishes.
_running: set[asyncio.Task] = set()


def _scored_count(db: Session, user: User) -> int:
    return db.execute(
        select(func.count()).select_from(JobScore).where(JobScore.user_id == user.id)
    ).scalar_one()


async def _run(user_id: str) -> None:
    """The detached work, on a session of its own. Never raises."""
    session = get_session_factory()()
    try:
        user = session.get(User, user_id)
        if user is None:
            return
        # Commits its own scores; rolls back and re-raises on a fault.
        await sweep_user(session, user=user, cap=SWEEP_CAP)
    except Exception:
        logger.exception("first run failed: user=%s", user_id)
        session.rollback()
    finally:
        session.close()


def _start(user_id: str) -> None:
    task = asyncio.get_running_loop().create_task(_run(user_id))
    _running.add(task)
    task.add_done_callback(_running.discard)


@router.post("/v1/me/matches/first-run", response_model=FirstRunOut)
async def start_first_run(
    db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Start scoring this person's matches, or say why it did not."""
    if _scored_count(db, user) > 0:
        return FirstRunOut(started=False, reason="already_scored")
    if saved_preference(db, user) is None:
        # The sweep's audience is SAVED preference rows only (`saved_preference`);
        # a seed is a guess it deliberately never scores against. So a person
        # with only a seed would get a run that does nothing and a progress of
        # 0 out of 0 forever. Do not "simplify" this to `current_or_seeded`.
        return FirstRunOut(started=False, reason="no_preference")
    # Free to ask. build_facts would answer the same with an error, but only
    # after paying for an extraction.
    if bundle(db, user, fall_back=True) is None:
        return FirstRunOut(started=False, reason="no_resume")
    ceiling = _ceiling_uusd()
    if ceiling is not None and _over_ceiling(db, ceiling=ceiling, now=None):
        return FirstRunOut(started=False, reason="budget_paused")
    _start(user.id)
    return FirstRunOut(started=True, reason=None)


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
    return MatchProgressOut(scored=scored, expected=expected)
