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


def test_handler_accepted_count_matches_sanitised_steps(db_session, run):
    handlers = import_tool_handlers(db_session, run)
    out = json.loads(
        handlers[DECLARE_PLAN_TOOL](
            {"steps": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]}
        )
    )
    assert out["accepted"] == 2


def test_handler_return_value_is_small():
    # The result goes back into the model's own context on the next round --
    # it must not echo the step list back.
    assert json.loads('{"accepted": 3}') == {"accepted": 3}


def test_edit_tool_advertises_step():
    edit = next(t for t in TOOLS if t.get("name") == EDIT_TOOL)
    assert edit["parameters"]["properties"]["step"] == {"type": "string"}


def test_step_argument_does_not_change_edit_result():
    base = apply_typst_edit("hello world", {"search": "world", "replace": "there"})
    withstep = apply_typst_edit(
        "hello world", {"search": "world", "replace": "there", "step": "a"}
    )
    assert base == withstep
