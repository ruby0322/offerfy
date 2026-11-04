"""The resume import run store.

An uploaded resume is imported by an LLM agent that declares its own list of
sections (a "plan") and edits the resume one section at a time. This module
owns the lifecycle of one import attempt: starting it, recording that it is
still alive, and ending it (successfully or not).

`version` is the reconnection contract: a client polling or streaming the
`snapshot()` below only needs to re-render when `version` changes, rather
than diffing the whole step list. Every function that mutates a run bumps it
through `_bump`, in the same transaction as the mutation it is paired with --
a write that forgot to bump it would freeze the progress card while the
resume keeps changing underneath it, and the bug would look like a frontend
problem instead of a backend one.

`MAX_STEPS`, `LABEL_MAX` and `KEY_MAX` bound `declare_plan`'s (and
`advance`'s) sanitising of agent-supplied text: a plan is clamped to at
most `MAX_STEPS` steps, each label is truncated to `LABEL_MAX` characters
and each key to `KEY_MAX` -- matching the `ResumeImportStep.key` column
width, which SQLite does not enforce but Postgres does -- before anything
is written. A malformed plan must never reach the database. Keys are
truncated *before* de-duplication: two distinct long keys can truncate to
the same short one, and de-duplicating first would let that pair reach the
database as a `UNIQUE(import_id, key)` violation instead of being
collapsed into one step the way the sanitiser is supposed to.

`declare_plan` and `advance` are where the plan and the agent's actual
edits are allowed to drift apart without failing the import: a plan can be
redeclared mid-run (keys that survive keep their status; the LLM's section
list is not fixed up front), an edit can name a step absent from the plan
(appended as a new `done` step), and an edit can carry no step name at all
(synthesised a `key` so a second one does not collide on
`UNIQUE(import_id, key)`). Every one of these has a defined, non-fatal
outcome, because our code never enumerates section names -- the agent's
plan is the only list that exists.

**The one-active invariant**, which both functions are responsible for
maintaining: while a run's status is `running`, if any step is still
`pending`, exactly one step is `active` -- that is the progress card's
"current step" pointer, and there must be exactly one of it, never zero
(the card would show no current step) and never two (the card wouldn't
know which to highlight). When no `pending` step remains, zero `active` is
correct: there is nothing left to point at. (`finish` converts every
`pending`/`active` step to `skipped`, so the invariant stops applying the
moment the run is no longer `running`.)

`declare_plan` can break this *in either direction* when a plan is
redeclared, because it preserves each surviving key's own status and
cannot know, key by key, what the rest of the new list will look like: a
survivor can bring its own already-`active` status into a list where a
new key also lands at `seq == 0`, giving two `active`; or every survivor
can hold some non-`active` status (e.g. `done`) with no new key promoted
either, giving zero. `declare_plan` handles this with a single
normalisation pass (`_normalise_active`), run once after the full list is
built, that corrects both directions the same way: more than one `active`
keeps the first (by `seq`) and demotes the rest to `pending`; none at all
promotes the first `pending`. This is why the build loop itself gives
every new key a neutral `pending` default rather than special-casing
`seq == 0` -- that special case is exactly what let the two-`active` case
through in an earlier version of this function, because it could race a
preserved `active` from a survivor elsewhere in the same list.

`advance` can break it by being called twice for the same already-`done`
key (a retried or duplicated tool call): naively re-running "promote the
next `pending` step" a second time would promote *another* step on top of
the one already promoted the first time, leaving two `active`. `advance`
instead guards against this at the entry: a step that is already `done`
is a no-op, so it never re-promotes and never double-counts
`run.applied`.

`start_run`'s "is one already running" check is a query followed by an
insert, which is not atomic by itself: offerfy-api runs uvicorn with two
workers sharing only Postgres, so two concurrent calls for the same resume
(e.g. Retry double-clicked) could both read "no running row" and both try to
insert. The real guard is the partial unique index on
`resume_imports (resume_id) WHERE status = 'running'` (see the model and
migration 024) -- the query above is only a fast path that avoids hitting
that index on the common, non-racing case. Whichever caller loses the race
sees `ImportAlreadyRunning`, translated from the `IntegrityError` the index
raises, never a raw database error.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Resume, ResumeImport, ResumeImportStep, _utcnow

logger = logging.getLogger(__name__)

# Sanitising bounds for `declare_plan` and `advance`. `KEY_MAX` matches
# `ResumeImportStep.key`'s column width (`String(64)`) -- see the module
# docstring for why that must be enforced here, not just in the schema.
MAX_STEPS = 12
LABEL_MAX = 120
KEY_MAX = 64

# How long a run may go without a heartbeat before it is considered dead --
# e.g. its worker process crashed or was killed -- and may be superseded by a
# new run for the same resume.
#
# **This has to sit above the turn's own timeout, not below it.**
# `import_runner.TIMEOUT_SECONDS` is 180s and it is a *per-chunk read* budget,
# so a 180-second wait for the first token is permitted and silent. At 60s
# this threshold called that run dead: the card said "interrupted", the person
# pressed Resume, and two agents then wrote `resume.typst_source` concurrently
# from two sessions, each holding its own snapshot of it -- interleaved and
# lost sections, plus the old runner's `finish()` landing on the new run. At
# 240s "stale" means dead rather than merely slow.
STALE_AFTER_SECONDS = 240


class ImportAlreadyRunning(Exception):
    """Raised by `start_run` when a non-stale run is already in progress."""


class ImportSuperseded(Exception):
    """Raised by `touch` when its run is no longer the live one.

    The only mechanism that can stop a superseded runner. `start_run` marks a
    stale run `failed` and inserts a fresh one, but nothing cancels the
    `asyncio.Task` still driving the old one -- and across two uvicorn workers
    nothing can: the task lives in the other process's event loop. So the
    stopping has to be something the old runner itself notices, and the only
    thing both workers can see is Postgres. Every event of a run funnels
    through `touch`, which makes it the one chokepoint where that check costs
    one query and catches everything -- including a tool call (`declare_plan`)
    arriving from a runner that was superseded minutes ago.
    """


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat a naive value as UTC."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _bump(run: ResumeImport) -> None:
    """Advance `version` and the heartbeat. The only place that does either.

    Every public function that changes a run routes its write through here,
    in the same transaction, so a future mutating function cannot land a
    change the reconnecting client never finds out about.
    """
    run.version += 1
    run.heartbeat_at = _utcnow()


def _latest_run(db: Session, resume_id: str) -> ResumeImport | None:
    return (
        db.query(ResumeImport)
        .filter(ResumeImport.resume_id == resume_id)
        .order_by(ResumeImport.created_at.desc(), ResumeImport.id.desc())
        .first()
    )


def is_stale(run: ResumeImport, now: datetime | None = None) -> bool:
    now = now or _utcnow()
    return _aware(run.heartbeat_at) < _aware(now) - timedelta(seconds=STALE_AFTER_SECONDS)


def start_run(db: Session, resume: Resume) -> ResumeImport:
    """Start a new import run for `resume`.

    Refuses with `ImportAlreadyRunning` if a `running` row for this resume
    already exists and is not stale. A stale `running` row is superseded: it
    is marked `failed` (its worker is presumed dead) rather than left behind
    as a run that can never finish, and then a fresh run is started -- in the
    same transaction as the supersession, so no reader can ever observe the
    resume as `failed` moments before it becomes `pending` again.

    The existing-row check is an optimisation, not the guard: the database's
    partial unique index is. If this call loses a race with another `start_run`
    for the same resume, the index raises `IntegrityError` on commit, which is
    translated into `ImportAlreadyRunning` here so every caller sees the same
    exception regardless of which path noticed the conflict.
    """
    existing = _latest_run(db, resume.id)
    if existing is not None and existing.status == "running":
        if not is_stale(existing):
            raise ImportAlreadyRunning(f"an import is already running for resume {resume.id}")
        _mark_failed(existing, error="superseded: heartbeat went stale while still running")

    run = ResumeImport(resume_id=resume.id, status="running", file_key=resume.upload_s3_key)
    resume.import_status = "pending"
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        # Unscoped on purpose: the only other integrity failure reachable here
        # is the resume_id FK, which means the resume was deleted mid-call.
        db.rollback()
        raise ImportAlreadyRunning(
            f"an import is already running for resume {resume.id}"
        ) from None
    return run


def _live_status(db: Session, run: ResumeImport) -> str | None:
    """`run.status` as the database has it *now*.

    Read as a column rather than off the instance: the row may have been moved
    to `failed` by a `start_run` in the other uvicorn worker, which this
    session's identity map knows nothing about.
    """
    return db.query(ResumeImport.status).filter(ResumeImport.id == run.id).scalar()


def touch(db: Session, run: ResumeImport) -> None:
    """Heartbeat one event of a live run -- and stop a superseded one.

    Raises `ImportSuperseded` if the row is no longer `running`. The runner
    funnels every exception into `fail()`, which is a no-op on a run it no
    longer owns, so a superseded runner stops at its next event without
    touching the run that replaced it. See `ImportSuperseded`.
    """
    if _live_status(db, run) != "running":
        raise ImportSuperseded(
            f"import run {run.id} is no longer running; a newer run has taken over"
        )
    _bump(run)
    db.commit()


def finish(db: Session, run: ResumeImport) -> None:
    """End a run successfully. Any step not yet reached is marked skipped.

    A no-op on a run that is no longer `running`, for the same reason `fail`
    is: a superseded runner reaching its last event must not write `done` --
    on itself or, through `resume.import_status`, on the run that replaced it.
    """
    if _live_status(db, run) != "running":
        logger.warning(
            "ignoring finish() on import run %s: status is %s, not running",
            run.id,
            _live_status(db, run),
        )
        return
    unreached = (
        db.query(ResumeImportStep)
        .filter(
            ResumeImportStep.import_id == run.id,
            ResumeImportStep.status.in_(["pending", "active"]),
        )
        .all()
    )
    for step in unreached:
        step.status = "skipped"
    run.status = "done"
    run.resume.import_status = "done"
    _bump(run)
    db.commit()


def _mark_failed(run: ResumeImport, error: str) -> None:
    """Apply the `failed` transition without committing.

    Shared by `fail` (which commits on its own) and `start_run`'s
    supersession path (which commits together with the new run, as one
    transaction).
    """
    run.status = "failed"
    run.error = error
    run.resume.import_status = "failed"
    _bump(run)


def fail(db: Session, run: ResumeImport, error: str) -> None:
    """End a run as failed. A no-op on a run that is no longer `running`.

    The guard is what makes `ImportSuperseded` safe to raise out of `touch`:
    the superseded runner's own exception handler calls this, and without the
    check it would overwrite the supersession's error with its own, bump a
    version nobody is listening to, and -- worst of the three -- set
    `resume.import_status = "failed"` while the run that replaced it is still
    pending.
    """
    if _live_status(db, run) != "running":
        logger.warning(
            "ignoring fail() on import run %s (%s); it is no longer running",
            run.id,
            error,
        )
        return
    _mark_failed(run, error)
    db.commit()


def snapshot(db: Session, resume_id: str) -> dict | None:
    """The newest run for `resume_id`, as the wire contract for the SSE
    endpoint and progress card built in later tasks. `None` if no run exists.
    """
    run = _latest_run(db, resume_id)
    if run is None:
        return None
    steps = (
        db.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id)
        .order_by(ResumeImportStep.seq)
        .all()
    )
    return {
        "import_id": run.id,
        "status": run.status,
        "version": run.version,
        "applied": run.applied,
        "steps": [
            {"key": step.key, "label": step.label, "status": step.status, "detail": step.detail}
            for step in steps
        ],
        "error": run.error,
        "started_at": _aware(run.created_at).isoformat(),
        "stale": run.status == "running" and is_stale(run),
    }


def _sanitise_plan(steps: list[dict]) -> list[tuple[str, str]]:
    """Validate and bound a raw agent-declared plan.

    Drops entries whose key or label is not a non-empty string,
    truncates each key to `KEY_MAX` and each label to `LABEL_MAX`,
    de-duplicates by (truncated) key keeping the first occurrence, and
    keeps at most `MAX_STEPS`. Runs entirely before any row is written, so
    a malformed plan can never reach the database.

    Truncation happens *before* de-duplication on purpose: two distinct
    keys longer than `KEY_MAX` can truncate to the same value, and
    de-duplicating on the untruncated key would let that pair both reach
    the database -- which then violates `UNIQUE(import_id, key)` instead
    of being collapsed into the single step the sanitiser is supposed to
    produce.
    """
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in steps:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        label = entry.get("label")
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(label, str) or not label:
            continue
        key = key[:KEY_MAX]
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((key, label[:LABEL_MAX]))
        if len(cleaned) == MAX_STEPS:
            break
    return cleaned


def _normalise_active(steps: list[ResumeImportStep]) -> None:
    """Enforce the one-active invariant (see the module docstring) on an
    already-built, `seq`-ordered step list, in a single pass that handles
    both directions:

    - more than one `active` -- keep the first (by `seq`), demote the
      rest to `pending`. Demoting is not a status regression: a demoted
      step is not `done`, it is queued, which is exactly what `pending`
      means.
    - no `active` at all but at least one `pending` -- promote the first
      `pending` step.

    Either direction can arise from `declare_plan` preserving a survivor's
    old status while giving every new key a neutral `pending` default:
    a survivor can bring its own `active` into a list that also has a new
    key sitting at `seq == 0`, or every survivor can be non-`active` (e.g.
    `done`) with no new key ending up `active` either. One pass, run after
    the list is built, covers both without a `seq == 0` special case in
    the build loop that would only correctly handle a fresh plan.
    """
    actives = [step for step in steps if step.status == "active"]
    if len(actives) > 1:
        for step in actives[1:]:
            step.status = "pending"
    elif not actives:
        for step in steps:
            if step.status == "pending":
                step.status = "active"
                break


def declare_plan(db: Session, run: ResumeImport, steps: list[dict]) -> list[ResumeImportStep]:
    """Replace `run`'s step list with a sanitised version of `steps`.

    Called more than once on the same run -- the agent is allowed to
    redeclare its plan mid-import -- this replaces the row set while
    preserving the `status` of any key that survives into the new plan, so
    a step already marked `done` does not get reset to `pending` just
    because the plan was redeclared. Every key that does not survive
    starts `pending` -- no `seq == 0 -> active` special case here, because
    a survivor elsewhere in the list can already hold `active`, and
    forcing a new key at `seq == 0` to also be `active` would produce two.

    `_normalise_active` is then run once over the rebuilt list to make
    exactly one step `active` whenever any step is `pending` (and zero
    when none is) -- see the module docstring for the invariant and why
    preserving status can otherwise land on either zero or two. For a
    fresh plan (no survivors at all), every key is new and thus `pending`,
    so normalisation promotes `seq` 0, reproducing "first step active,
    rest pending" from the single shared pass rather than a separate rule.
    """
    cleaned = _sanitise_plan(steps)

    existing_status = {
        step.key: step.status
        for step in db.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id)
        .all()
    }
    db.query(ResumeImportStep).filter(ResumeImportStep.import_id == run.id).delete()

    new_steps: list[ResumeImportStep] = []
    for seq, (key, label) in enumerate(cleaned):
        status = existing_status.get(key, "pending")
        step = ResumeImportStep(import_id=run.id, seq=seq, key=key, label=label, status=status)
        db.add(step)
        new_steps.append(step)

    _normalise_active(new_steps)

    _bump(run)
    db.commit()
    return new_steps


def advance(
    db: Session, run: ResumeImport, step_key: str | None, detail: str | None = None
) -> None:
    """Record one completed edit, whether or not it matches the plan.

    The common case: `step_key` names a step from the declared plan. That
    step is marked `done` and the next `pending` step (by declared order)
    becomes `active`, advancing the progress card's pointer.

    Two drift cases never fail the import:
    - `step_key` names no step in the plan (the agent edited a section it
      never declared, or declared under a different key). It is appended
      as a new `done` step instead, and the plan's own pointer is left
      alone -- this is a side-channel edit, not plan progression. Its
      `label` is the (truncated) key itself, not `""`: the model did name
      this step, so falling back to the frontend's generic wording would
      discard information the model actually gave us.
    - `step_key` is `None` or empty (the agent edited without naming a
      step at all). A key is synthesised as `edit-{run.applied + 1}` --
      never the literal `""`, which would collide with a second unlabelled
      edit on `UNIQUE(import_id, key)` and abort the whole import -- with
      an empty `label`, which is the frontend's cue (Task 8) to render its
      own fallback wording rather than guess at a translation here.

    A model-supplied `step_key` is truncated to `KEY_MAX` before it is
    used for either lookup or insert, for the same column-width reason
    `declare_plan` truncates a declared key.

    Idempotent for a repeated call on an already-`done` key -- a retried
    or duplicated tool call hits this, the same failure mode `start_run`
    already defends against for run creation. Without the guard, "promote
    the next `pending` step" is not idempotent: a second call would
    promote a *different* step on top of the one the first call already
    promoted, leaving two `active` and violating the one-active invariant
    (see the module docstring). A no-op call also does not increment
    `run.applied`: it counts edits applied, drives the frontend's preview
    refresh, and counting a retry would trigger a pointless recompile and
    inflate the card's progress.
    """
    synthesised = not step_key
    key = f"edit-{run.applied + 1}" if synthesised else step_key[:KEY_MAX]

    step = (
        db.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id, ResumeImportStep.key == key)
        .first()
    )

    if step is not None:
        if step.status == "done":
            return
        step.status = "done"
        if detail is not None:
            step.detail = detail
        next_step = (
            db.query(ResumeImportStep)
            .filter(ResumeImportStep.import_id == run.id, ResumeImportStep.status == "pending")
            .order_by(ResumeImportStep.seq)
            .first()
        )
        if next_step is not None:
            next_step.status = "active"
    else:
        seq = (
            db.query(ResumeImportStep).filter(ResumeImportStep.import_id == run.id).count()
        )
        db.add(
            ResumeImportStep(
                import_id=run.id,
                seq=seq,
                key=key,
                label="" if synthesised else key[:LABEL_MAX],
                status="done",
                detail=detail,
            )
        )

    run.applied += 1
    _bump(run)
    db.commit()
