"""Drift rules: the model's declared plan and its actual edits disagree.

The agent declares its own list of sections (`declare_plan`) and then
reports progress on them one at a time (`advance`). Our code never
enumerates section names, so every way the plan and the edits can
disagree -- a malformed plan, a re-declared plan, an edit on an unplanned
key, an edit with no key at all -- must have a defined, non-fatal outcome.
These tests pin those outcomes down.
"""

import pytest

from app.models import GuestSession, Resume, ResumeImportStep
from app.services.import_run import (
    LABEL_MAX,
    MAX_STEPS,
    advance,
    declare_plan,
    finish,
    start_run,
)


@pytest.fixture
def resume(db_session):
    guest = GuestSession(key_hash="deadbeef-import-drift", locale="en")
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


@pytest.fixture
def run(db_session, resume):
    return start_run(db_session, resume)


def _steps(db_session, run):
    return (
        db_session.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id)
        .order_by(ResumeImportStep.seq)
        .all()
    )


def test_plan_drops_bad_entries_and_bounds_size(db_session, run):  # Review Focus 3
    steps = (
        [{"key": "", "label": "x"}, {"key": "a", "label": ""}, {"key": "b", "label": 7}]
        + [{"key": f"k{i}", "label": "L" * 300} for i in range(40)]
    )
    kept = declare_plan(db_session, run, steps)
    assert len(kept) == MAX_STEPS
    assert all(len(s.label) <= LABEL_MAX for s in kept)


def test_plan_dedupes_by_key_keeping_first(db_session, run):
    kept = declare_plan(
        db_session, run, [{"key": "a", "label": "First"}, {"key": "a", "label": "Second"}]
    )
    assert [s.label for s in kept] == ["First"]


def test_first_step_is_active(db_session, run):
    kept = declare_plan(db_session, run, [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}])
    assert [s.status for s in kept] == ["active", "pending"]


def test_advance_marks_done_and_activates_next(db_session, run):
    declare_plan(db_session, run, [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}])
    advance(db_session, run, "a", detail="2 degrees")
    assert [s.status for s in _steps(db_session, run)] == ["done", "active"]
    assert run.applied == 1


def test_unplanned_step_is_appended_done(db_session, run):  # Review Focus 5
    declare_plan(db_session, run, [{"key": "a", "label": "A"}])
    advance(db_session, run, "surprise")
    rows = _steps(db_session, run)
    assert rows[-1].key == "surprise" and rows[-1].status == "done"


def test_planned_step_never_edited_is_skipped_and_run_completes(db_session, run):  # Review Focus 5
    declare_plan(db_session, run, [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}])
    advance(db_session, run, "a")
    finish(db_session, run)
    assert run.status == "done"
    assert [s.status for s in _steps(db_session, run)] == ["done", "skipped"]


def test_redeclare_preserves_status_of_surviving_keys(db_session, run):
    declare_plan(db_session, run, [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}])
    advance(db_session, run, "a")
    kept = declare_plan(db_session, run, [{"key": "a", "label": "A"}, {"key": "c", "label": "C"}])
    assert {s.key: s.status for s in kept}["a"] == "done"


def test_advance_bumps_version(db_session, run):
    # every declare_plan and advance call increases run.version
    v0 = run.version
    declare_plan(db_session, run, [{"key": "a", "label": "A"}])
    v1 = run.version
    assert v1 == v0 + 1

    advance(db_session, run, "a")
    assert run.version == v1 + 1


def test_consecutive_unlabelled_edits_do_not_collide(db_session, run):
    advance(db_session, run, None)
    advance(db_session, run, "")
    rows = _steps(db_session, run)
    assert [r.key for r in rows] == ["edit-1", "edit-2"]
    assert [r.label for r in rows] == ["", ""]
    assert run.applied == 2


def test_unlabelled_edits_work_without_any_plan(db_session, run):
    advance(db_session, run, None)
    finish(db_session, run)
    assert run.status == "done" and run.applied == 1
