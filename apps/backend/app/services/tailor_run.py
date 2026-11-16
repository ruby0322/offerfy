"""The tailor run store.

A tailor resume's first pass is one agent turn: read the resume, read the
posting it was forked for, say how well they match and what to change. This
module owns the lifecycle of one such pass -- starting it, recording that it
is still alive, and ending it.

It is `app.services.import_run` with the plan removed, and the parts that
survive are the parts that make a run survive a reload:

`version` is the reconnection contract. A client polling or streaming
`snapshot()` re-renders when `version` changes and otherwise does nothing, so
every mutation bumps it through `_bump`, in the same transaction as the
mutation it is paired with. A write that forgot to bump it would freeze the
card while the resume changed underneath it.

`source_hash` is the *source* contract, and separate from `version` for the
reason `import_run` documents at length: the editor repaints its preview when
the resume's source identity moves, which is not the same event as the run
making progress. A pass that reaches `writing` and edits the resume has to
tell the editor the source moved; a pass that merely changed phase must not
make it repaint.

`heartbeat_at` plus `is_stale` is what tells a runner whose worker died from
one still thinking. Nothing reaps a run left `running` by an API restart --
`kill -HUP` mid-pass orphans the asyncio task exactly as it does an import --
so `stale` is a common state, not an edge case, and it is the client's cue to
offer the pass again.

`STALE_AFTER_SECONDS` sits above the turn's own per-chunk read budget on
purpose. `import_run`'s docstring records what happens when it does not: a
slow first token is read as a dead runner, the person retries, and two agents
then write `resume.typst_source` concurrently from two sessions, interleaving
and losing edits. The same arithmetic applies here, so the same margin does.

`start_run`'s "is one already running" check is a query followed by an insert,
which is not atomic: offerfy-api runs uvicorn with two workers sharing only
Postgres, so two concurrent starts for one resume could both read "none
running". The real guard is the partial unique index on
`tailor_runs (resume_id) WHERE status = 'running'`; the query is a fast path
that keeps the common case off it. Whichever caller loses sees
`TailorRunAlreadyRunning`, never a raw `IntegrityError`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ChatMessage, Job, Resume, TailorRun, _utcnow
from app.services.revisions import source_hash

logger = logging.getLogger(__name__)

# See the module docstring: above the turn's per-chunk read budget, not below
# it. Kept equal to import_run.STALE_AFTER_SECONDS rather than derived from
# it, because the two runs' budgets are free to diverge; if you change one,
# decide about the other rather than inheriting silently.
STALE_AFTER_SECONDS = 240

# The pass's four stages, in order, each one the tool that enters it. They are
# a fixed, server-owned list rather than a steps table (see `models.TailorRun`)
# precisely because they are fixed: `phase` is then the whole cursor, and the
# card draws the list from it.
PHASES = ("read_resume", "read_job", "mapping", "proposing")

# Rank, for the forward-only rule in `advance_phase`.
#
# The legacy three are ranked rather than dropped because a pass can outlive a
# deploy: only `finish`/`fail` clear `phase`, and a **stale** run keeps its
# last phase forever, so rows holding these values exist and must still sort.
# `assessing` ranks with `mapping` and `writing` past the end, which is where
# an edit belongs now that the pass is not supposed to make one.
PHASE_ORDER = {
    "read_resume": 1,
    "reading": 1,
    "read_job": 2,
    "mapping": 3,
    "assessing": 3,
    "proposing": 4,
    "writing": 5,
}

# What the runner reports when the posting it was started for has gone. Not
# an exception: a tailor whose posting was deleted can still be assessed on
# its own terms, and the pass says so rather than failing.
JOB_MISSING = "job_missing"


class TailorRunAlreadyRunning(Exception):
    """Raised by `start_run` when a non-stale run is already in progress."""


class TailorRunSuperseded(Exception):
    """Raised by `touch` when its run is no longer the live one.

    The only mechanism that stops a superseded runner across two uvicorn
    workers: the old runner's task lives in the other process and cannot be
    cancelled from here, so it has to discover for itself, on its next event,
    that the row it is writing is no longer `running`.
    """


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat a naive value as UTC."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _bump(run: TailorRun) -> None:
    """Advance `version` and the heartbeat. The only place that does either."""
    run.version += 1
    run.heartbeat_at = _utcnow()


def latest_run(db: Session, resume_id: str) -> TailorRun | None:
    return (
        db.query(TailorRun)
        .filter(TailorRun.resume_id == resume_id)
        .order_by(TailorRun.created_at.desc(), TailorRun.id.desc())
        .first()
    )


def is_stale(run: TailorRun, now: datetime | None = None) -> bool:
    if run.status != "running":
        return False
    cutoff = (now or _utcnow()) - timedelta(seconds=STALE_AFTER_SECONDS)
    return _aware(run.heartbeat_at) < cutoff


def start_run(db: Session, resume: Resume, *, job_id: str | None) -> TailorRun:
    """Start a pass for `resume`, or raise if a live one already exists.

    A stale run is superseded rather than blocking: it is marked `failed` in
    the same transaction that inserts the replacement, so the partial unique
    index never sees two `running` rows and the old runner's next `touch`
    raises `TailorRunSuperseded`.
    """
    existing = latest_run(db, resume.id)
    if existing is not None and existing.status == "running":
        if not is_stale(existing):
            raise TailorRunAlreadyRunning("A tailor pass is already running for this resume")
        existing.status = "failed"
        existing.error = "superseded"
        _bump(existing)
        db.flush()
    run = TailorRun(
        resume_id=resume.id,
        job_id=job_id,
        status="running",
        phase="read_resume",
        version=0,
        heartbeat_at=_utcnow(),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        # The index caught a race the query above could not; see the module
        # docstring. The loser gets the same error the fast path raises.
        db.rollback()
        raise TailorRunAlreadyRunning(
            "A tailor pass is already running for this resume"
        ) from exc
    db.refresh(run)
    return run


def _live_status(db: Session, run: TailorRun) -> str | None:
    """`run.status` as the database has it *now*, not as this session cached it."""
    return db.query(TailorRun.status).filter(TailorRun.id == run.id).scalar()


def touch(db: Session, run: TailorRun) -> None:
    """Heartbeat one event of a live run -- and stop a superseded one."""
    if _live_status(db, run) != "running":
        raise TailorRunSuperseded(f"tailor run {run.id} is no longer the live run")
    _bump(run)
    db.commit()


def set_phase(db: Session, run: TailorRun, phase: str) -> None:
    """Move the card's phase. Unknown values are ignored, not raised.

    The phases are ours, derived from which tool the turn just called, so a
    name that is not in `PHASES` means this module and its caller have drifted
    -- a reason to log and carry on, not to fail a pass the person is watching.
    """
    if phase not in PHASES:
        logger.warning("tailor run %s: ignoring unknown phase %r", run.id, phase)
        return
    if run.phase == phase:
        return
    if _live_status(db, run) != "running":
        raise TailorRunSuperseded(f"tailor run {run.id} is no longer the live run")
    run.phase = phase
    _bump(run)
    db.commit()


def advance_phase(db: Session, run: TailorRun, phase: str) -> None:
    """Move the card's phase forward, and never back.

    `set_phase` is the raw setter; this is what the runner calls, because the
    *model* decides the order the stages are entered in and it does not have
    to be the order they are listed in. A second `read_typst` after proposing
    (likely -- the prompt asks the model to re-read before copying a `before`
    string), or `propose_resume_actions` before `map_jd_requirements`, would
    otherwise walk the cursor backwards and untick steps the person watched
    complete. The card's claim is "it got at least this far", so the cursor
    only ever climbs.

    A phase outside `PHASE_ORDER` is ignored by `set_phase`, which logs it.
    """
    rank = PHASE_ORDER.get(phase)
    if rank is None:
        logger.warning("tailor run %s: ignoring unknown phase %r", run.id, phase)
        return
    current = PHASE_ORDER.get(run.phase or "", 0)
    if rank <= current:
        return
    set_phase(db, run, phase)


def finish(db: Session, run: TailorRun) -> None:
    """End a pass successfully. Idempotent on an already-finished run.

    `phase` is deliberately **kept**, where it used to be nulled: on a
    terminal row it means "the furthest stage this pass reached". The card
    renders a fixed four-step list, so without it a pass where the model
    skipped a tool would show four ticks. With it, the steps past this one
    render as skipped, which is both true and the thing the person needs to
    know when the suggestions look thin.
    """
    if run.status != "running":
        return
    run.status = "done"
    _bump(run)
    db.commit()


def fail(db: Session, run: TailorRun, error: str) -> None:
    """End a pass with a reason. Idempotent, and never raises.

    Called from the runner's `finally`-shaped exits, including ones reached
    because the session is already in a bad state, so a rollback-and-retry is
    attempted once before giving up: a run left `running` is the worst
    outcome, because the card then spins with nothing left to update it.
    """
    try:
        if run.status != "running":
            return
        # `phase` kept, for the reason `finish` gives: on a terminal row it is
        # how far the pass got, which is exactly what a failed card should
        # say.
        run.status = "failed"
        run.error = error
        _bump(run)
        db.commit()
    except Exception:
        logger.exception("tailor run %s could not be marked failed; retrying once", run.id)
        try:
            db.rollback()
            db.query(TailorRun).filter(TailorRun.id == run.id, TailorRun.status == "running").update(
                {"status": "failed", "error": error, "version": TailorRun.version + 1}
            )
            db.commit()
        except Exception:
            logger.exception("tailor run %s is stuck running", run.id)


def snapshot(db: Session, resume_id: str) -> dict[str, Any] | None:
    """The latest run for `resume_id` as the client reads it, or None.

    Carries the posting inline -- title, company, the id the card links to --
    so the card can draw the employer mark and the job line without a second
    request, and so a run whose posting was since deleted still says what it
    was about where it can (`job_id` survives; `job` goes None).
    """
    run = latest_run(db, resume_id)
    if run is None:
        return None
    resume = db.get(Resume, resume_id)
    job = db.get(Job, run.job_id) if run.job_id else None
    return {
        "run_id": run.id,
        "status": run.status,
        "phase": run.phase,
        "error": run.error,
        "version": run.version,
        "stale": is_stale(run),
        "created_at": _aware(run.created_at).isoformat(),
        "job_id": run.job_id,
        "job": (
            {
                "job_id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "is_active": job.is_active,
            }
            if job is not None
            else None
        ),
        # Same contract as import_run.snapshot: the editor repaints on this,
        # not on `version`.
        "source_hash": source_hash(resume.typst_source) if resume is not None else None,
        # The "re-read the transcript" contract, and a third signal rather
        # than a reuse of `version` for a reason worth stating here.
        #
        # The runner writes its `tool` and `assistant` rows straight to this
        # database and nothing streams them, so a client that does not re-read
        # watches the card above an empty transcript until it reloads. The
        # obvious signal is `version` -- and it is the wrong one. `touch()` is
        # called on *every* event `aiter_chat_edit` yields, and that includes
        # one `delta` per streamed text chunk, so `version` moves hundreds of
        # times per pass: it says "still alive", not "there is something new to
        # read". Keyed on it, the 0.4s stream poll re-reads the whole
        # transcript on every frame, which this feature's own large tool rows
        # would then make expensive.
        #
        # A COUNT, not a column: it moves only when `persist_tool_message` or
        # `persist_assistant_message` actually wrote a row, it needs no
        # migration, and it costs strictly less than the fetch it prevents.
        "message_count": (
            db.query(func.count(ChatMessage.id))
            .filter(ChatMessage.resume_id == resume_id)
            .scalar()
            or 0
        ),
    }
