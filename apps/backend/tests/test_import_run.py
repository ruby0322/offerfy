from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import GuestSession, Resume, ResumeImport, ResumeImportStep, _utcnow
from app.services import import_run
from app.services.import_run import (
    ImportAlreadyRunning,
    ImportSuperseded,
    LABEL_MAX,
    MAX_STEPS,
    STALE_AFTER_SECONDS,
    declare_plan,
    fail,
    finish,
    is_stale,
    snapshot,
    start_run,
    touch,
)


@pytest.fixture
def resume(db_session):
    guest = GuestSession(key_hash="deadbeef-import-run", locale="en")
    db_session.add(guest)
    db_session.commit()
    resume = Resume(
        title="R",
        typst_source="= R",
        source="upload",
        guest_session_id=guest.id,
        upload_s3_key="uploads/resume.pdf",
    )
    db_session.add(resume)
    db_session.commit()
    return resume


def test_constants_declared():
    assert MAX_STEPS == 12
    assert LABEL_MAX == 120


def test_the_stale_threshold_sits_above_the_turns_own_timeout():
    """"Stale" has to mean dead, not slow.

    `import_runner.TIMEOUT_SECONDS` is a *per-chunk read* budget, so a wait
    of nearly that long for the first token is permitted and silent. A
    threshold below it declared such a run dead, offered Resume, and let two
    agents write the same resume concurrently from two sessions.
    """
    from app.services.import_runner import TIMEOUT_SECONDS

    assert STALE_AFTER_SECONDS == 240
    assert STALE_AFTER_SECONDS > TIMEOUT_SECONDS


def test_start_run_sets_running_and_pending_status(db_session, resume):
    run = start_run(db_session, resume)
    assert run.status == "running"
    assert resume.import_status == "pending"
    assert run.file_key == "uploads/resume.pdf"


def test_second_concurrent_run_is_refused(db_session, resume):  # Review Focus 1
    start_run(db_session, resume)
    with pytest.raises(ImportAlreadyRunning):
        start_run(db_session, resume)


def test_stale_run_may_be_replaced(db_session, resume):
    run = start_run(db_session, resume)
    run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.flush()
    assert start_run(db_session, resume).id != run.id


def test_stale_superseded_run_is_marked_failed(db_session, resume):
    run = start_run(db_session, resume)
    run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.flush()

    start_run(db_session, resume)

    db_session.refresh(run)
    assert run.status == "failed"
    assert run.error is not None


def test_touch_bumps_version_monotonically(db_session, resume):
    run = start_run(db_session, resume)
    before = run.version
    touch(db_session, run)
    touch(db_session, run)
    assert run.version == before + 2


def test_is_stale_true_after_threshold(db_session, resume):
    run = start_run(db_session, resume)
    assert is_stale(run) is False
    run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    assert is_stale(run) is True


def test_finish_skips_unreached_steps(db_session, resume):
    run = start_run(db_session, resume)
    db_session.add_all(
        [
            ResumeImportStep(
                import_id=run.id, seq=0, key="summary", label="Summary", status="done"
            ),
            ResumeImportStep(
                import_id=run.id, seq=1, key="education", label="Education", status="active"
            ),
            ResumeImportStep(
                import_id=run.id, seq=2, key="experience", label="Experience", status="pending"
            ),
        ]
    )
    db_session.commit()
    before_version = run.version

    finish(db_session, run)

    steps = (
        db_session.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id)
        .order_by(ResumeImportStep.seq)
        .all()
    )
    assert [s.status for s in steps] == ["done", "skipped", "skipped"]
    assert run.status == "done"
    assert resume.import_status == "done"
    assert run.version == before_version + 1


def test_fail_sets_error_and_resume_status(db_session, resume):
    run = start_run(db_session, resume)
    before_version = run.version

    fail(db_session, run, error="boom")

    assert run.status == "failed"
    assert run.error == "boom"
    assert resume.import_status == "failed"
    assert run.version == before_version + 1


def test_snapshot_steps_shape_and_order(db_session, resume):
    run = start_run(db_session, resume)
    # Inserted out of seq order on purpose, so the order in the snapshot
    # proves `order_by(seq)` is applied rather than happening to match
    # insertion order.
    db_session.add_all(
        [
            ResumeImportStep(
                import_id=run.id,
                seq=1,
                key="experience",
                label="Experience",
                status="active",
                detail="2 of 3 edits",
            ),
            ResumeImportStep(
                import_id=run.id, seq=0, key="summary", label="Summary", status="done"
            ),
        ]
    )
    db_session.commit()

    snap = snapshot(db_session, resume.id)

    assert snap["steps"] == [
        {"key": "summary", "label": "Summary", "status": "done", "detail": None},
        {
            "key": "experience",
            "label": "Experience",
            "status": "active",
            "detail": "2 of 3 edits",
        },
    ]
    for step in snap["steps"]:
        assert set(step) == {"key", "label", "status", "detail"}


def test_two_running_rows_for_same_resume_violate_db_index(db_session, resume):
    db_session.add(ResumeImport(resume_id=resume.id, status="running"))
    db_session.commit()

    db_session.add(ResumeImport(resume_id=resume.id, status="running"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_start_run_translates_db_conflict_into_already_running(db_session, resume, monkeypatch):
    # A real running row exists, but we make the app-level check blind to it
    # (as if another worker's insert raced past it) to prove start_run falls
    # back on the database's partial unique index and still raises
    # ImportAlreadyRunning, never a raw IntegrityError.
    start_run(db_session, resume)
    monkeypatch.setattr(import_run, "_latest_run", lambda db, resume_id: None)

    with pytest.raises(ImportAlreadyRunning):
        start_run(db_session, resume)


def test_snapshot_shape_and_stale_flag(db_session, resume):
    start_run(db_session, resume)
    snap = snapshot(db_session, resume.id)
    assert set(snap) == {
        "import_id",
        "status",
        "version",
        "applied",
        "steps",
        "error",
        "started_at",
        "stale",
    }
    assert snap["stale"] is False


def test_snapshot_is_none_without_a_run(db_session, resume):
    assert snapshot(db_session, resume.id) is None


# --------------------------------------------------------------------------
# a superseded runner stops itself
# --------------------------------------------------------------------------


def _supersede(db_session, resume):
    """What the *other* uvicorn worker does: age the live run out and start a
    fresh one over it. The old run's own task is in another process and
    cannot be cancelled from here, which is the whole reason `touch` checks.
    """
    old = start_run(db_session, resume)
    old.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.flush()
    new = start_run(db_session, resume)
    return old, new


def test_touch_refuses_a_superseded_run(db_session, resume):
    old, _new = _supersede(db_session, resume)
    with pytest.raises(ImportSuperseded):
        touch(db_session, old)


def test_touch_on_a_superseded_run_bumps_nothing(db_session, resume):
    old, new = _supersede(db_session, resume)
    old_version, new_version = old.version, new.version
    with pytest.raises(ImportSuperseded):
        touch(db_session, old)
    db_session.rollback()
    db_session.refresh(old)
    db_session.refresh(new)
    assert old.version == old_version
    assert new.version == new_version


def test_fail_does_not_resurrect_a_superseded_run(db_session, resume):
    """The old runner's exception handler calls `fail`, and it must not land.

    Unguarded it would overwrite the supersession's own error and -- the
    damaging one -- set `resume.import_status = "failed"` while the run that
    replaced it is still pending.
    """
    old, new = _supersede(db_session, resume)
    superseded_error = old.error

    fail(db_session, old, "provider connection dropped")

    db_session.refresh(old)
    db_session.refresh(resume)
    assert old.error == superseded_error
    assert resume.import_status == "pending"
    assert new.status == "running"


def test_finish_does_not_complete_a_superseded_run(db_session, resume):
    old, new = _supersede(db_session, resume)

    finish(db_session, old)

    db_session.refresh(old)
    db_session.refresh(resume)
    assert old.status == "failed"
    assert resume.import_status == "pending"
    assert new.status == "running"


def test_declare_plan_from_a_superseded_runner_never_gets_that_far(db_session, resume):
    """Deferred item 8, closed at the same chokepoint.

    `declare_plan` has no status guard of its own and does not need one: the
    runner `touch`es before every event, so a tool call from a runner that was
    superseded minutes ago raises before its handler runs.
    """
    old, new = _supersede(db_session, resume)
    with pytest.raises(ImportSuperseded):
        touch(db_session, old)
    # Proof that this is the guard doing the work and not declare_plan: called
    # directly, it still writes -- which is exactly why touch has to be first.
    declare_plan(db_session, new, [{"key": "summary", "label": "Summary"}])
    assert (
        db_session.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == new.id)
        .count()
        == 1
    )
