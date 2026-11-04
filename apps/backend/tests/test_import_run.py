from datetime import timedelta

import pytest

from app.models import GuestSession, Resume, ResumeImportStep, _utcnow
from app.services.import_run import (
    ImportAlreadyRunning,
    LABEL_MAX,
    MAX_STEPS,
    STALE_AFTER_SECONDS,
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
