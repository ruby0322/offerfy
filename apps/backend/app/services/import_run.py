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

`MAX_STEPS` and `LABEL_MAX` are not used in this module. They are the next
task's contract for sanitising an agent-declared plan (`declare_plan`) and
are declared here because the brief fixes them as values this module owns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Resume, ResumeImport, ResumeImportStep, _utcnow

# The next task's contract for `declare_plan`, not read here.
MAX_STEPS = 12
LABEL_MAX = 120

# How long a run may go without a heartbeat before it is considered dead --
# e.g. its worker process crashed or was killed -- and may be superseded by a
# new run for the same resume.
STALE_AFTER_SECONDS = 60


class ImportAlreadyRunning(Exception):
    """Raised by `start_run` when a non-stale run is already in progress."""


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
    as a run that can never finish, and then a fresh run is started.
    """
    existing = _latest_run(db, resume.id)
    if existing is not None and existing.status == "running":
        if not is_stale(existing):
            raise ImportAlreadyRunning(f"an import is already running for resume {resume.id}")
        fail(db, existing, error="superseded: heartbeat went stale while still running")

    run = ResumeImport(resume_id=resume.id, status="running", file_key=resume.upload_s3_key)
    resume.import_status = "pending"
    db.add(run)
    db.commit()
    return run


def touch(db: Session, run: ResumeImport) -> None:
    _bump(run)
    db.commit()


def finish(db: Session, run: ResumeImport) -> None:
    """End a run successfully. Any step not yet reached is marked skipped."""
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


def fail(db: Session, run: ResumeImport, error: str) -> None:
    run.status = "failed"
    run.error = error
    run.resume.import_status = "failed"
    _bump(run)
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
