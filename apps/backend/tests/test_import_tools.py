import json

import pytest

from app.models import GuestSession, Resume, ResumeImportStep
from app.services import import_run
from app.services.import_tools import (
    DECLARE_PLAN_TOOL,
    import_tool_handlers,
    import_tool_specs,
)
from app.services.llm.prompts import EDIT_TOOL
from app.services.llm.tools import TOOLS
from app.typst_edit import apply_typst_edit


@pytest.fixture
def resume(db_session):
    guest = GuestSession(key_hash="deadbeef-import-tools", locale="en")
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
    # One `running` import per resume at a time (partial unique index), so
    # every test in this module that needs a run reuses this single one.
    return import_run.start_run(db_session, resume)


def test_declare_plan_tool_spec_shape():
    spec = import_tool_specs()[0]
    assert spec["type"] == "function"
    assert spec["name"] == DECLARE_PLAN_TOOL == "declare_import_plan"
    assert "steps" in spec["parameters"]["properties"]


def test_handler_persists_the_plan(db_session, run):
    handlers = import_tool_handlers(db_session, run)
    out = json.loads(handlers[DECLARE_PLAN_TOOL]({"steps": [{"key": "a", "label": "A"}]}))
    assert out["accepted"] == 1

    steps = (
        db_session.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id)
        .all()
    )
    assert len(steps) == 1
    assert steps[0].key == "a"
    assert steps[0].label == "A"


def test_handler_sanitises_and_returns_only_accepted(db_session, run):
    # Deliberately messy input: a duplicate key and a malformed entry (empty
    # key) that `declare_plan`'s sanitiser must drop. 4 submitted, only 2
    # should survive -- proving `accepted` reflects the filtered count, not
    # len(steps). The result's own shape must also carry nothing but that
    # count: no `steps`, `key`, or `label` riding back into model context.
    handlers = import_tool_handlers(db_session, run)
    out = json.loads(
        handlers[DECLARE_PLAN_TOOL](
            {
                "steps": [
                    {"key": "a", "label": "A"},
                    {"key": "a", "label": "A duplicate"},
                    {"key": "", "label": "x"},
                    {"key": "b", "label": "B"},
                ]
            }
        )
    )
    assert set(out.keys()) == {"accepted"}
    assert out["accepted"] == 2


def test_edit_tool_advertises_step():
    edit = next(t for t in TOOLS if t.get("name") == EDIT_TOOL)
    assert edit["parameters"]["properties"]["step"] == {"type": "string"}


def test_step_argument_does_not_change_edit_result():
    base = apply_typst_edit("hello world", {"search": "world", "replace": "there"})
    withstep = apply_typst_edit(
        "hello world", {"search": "world", "replace": "there", "step": "a"}
    )
    assert base == withstep
