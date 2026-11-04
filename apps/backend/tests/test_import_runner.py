"""The import runner: one agent turn over an uploaded resume, translated into
progress rows.

Fixtures mirror tests/test_import_tools.py: there is no shared `resume` or
`run` fixture in conftest.py, and the partial unique index on
`resume_imports (resume_id) WHERE status = 'running'` means every test that
needs a run reuses one run per resume rather than starting a second.
"""

import pytest

from app.models import ChatMessage, GuestSession, Resume, ResumeImportStep
from app.services import import_run
from app.services.import_runner import run_import
from app.services.import_tools import DECLARE_PLAN_TOOL
from app.services.llm.prompts import EDIT_TOOL
from tests.openai_script import ScriptedOpenAI, llm_message, tool_call

# ScriptedOpenAI only speaks the Responses API, so every scripted test
# names an OpenAI catalogue row explicitly rather than relying on
# whichever model model_choice.selected() would default to -- see
# tests/test_chat.py's OPENAI_ROW for the same reasoning.
OPENAI_ROW = "gpt-6.1-terra"

pytestmark = [pytest.mark.filterwarnings("ignore::DeprecationWarning"), pytest.mark.anyio]


@pytest.fixture
def resume(db_session):
    guest = GuestSession(key_hash="deadbeef-import-runner", locale="en")
    db_session.add(guest)
    db_session.commit()
    resume = Resume(
        title="R",
        typst_source="= R",
        source="upload",
        guest_session_id=guest.id,
        upload_s3_key="uploads/guest-1/resume-1/My Resume.txt",
    )
    db_session.add(resume)
    db_session.commit()
    return resume


@pytest.fixture
def run(db_session, resume):
    # One `running` import per resume at a time (partial unique index), so
    # every test in this module that needs a run reuses this single one.
    return import_run.start_run(db_session, resume)


def _steps(db_session, run) -> list[ResumeImportStep]:
    return (
        db_session.query(ResumeImportStep)
        .filter(ResumeImportStep.import_id == run.id)
        .order_by(ResumeImportStep.seq)
        .all()
    )


def _messages(db_session, resume_id) -> list[ChatMessage]:
    return (
        db_session.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


def _stub_upload(monkeypatch, data: bytes = b"Contact: a@b.com\nExperience: things") -> None:
    monkeypatch.setattr("app.services.import_runner.get_object", lambda key: data)


async def test_missing_upload_fails_cleanly(db_session, resume, run, monkeypatch):
    monkeypatch.setattr("app.services.import_runner.get_object", lambda key: None)

    await run_import(db_session, resume, run)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "failed"
    assert run.error
    assert resume.import_status == "failed"


async def test_scripted_import_walks_its_plan(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "declare_import_plan",
                        {
                            "steps": [
                                {"key": "basics", "label": "Basics"},
                                {"key": "experience", "label": "Experience"},
                                {"key": "education", "label": "Education"},
                            ]
                        },
                        "plan-1",
                    )
                ]
            ),
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "R", "replace": "R\nBasics", "step": "basics"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Basics", "replace": "Basics\nExperience", "step": "experience"},
                        "edit-2",
                    )
                ]
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Experience", "replace": "Experience\nEducation", "step": "education"},
                        "edit-3",
                    )
                ]
            ),
            llm_message(content="Imported your resume."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    assert run.applied == 3
    assert [s.status for s in _steps(db_session, run)] == ["done", "done", "done"]
    assert [s.key for s in _steps(db_session, run)] == ["basics", "experience", "education"]


async def test_import_writes_no_user_message(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(content="Imported."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    assert not [m for m in _messages(db_session, resume.id) if m.role == "user"]
    # The turn did run, though -- an assistant row and a tool row both landed.
    roles = [m.role for m in _messages(db_session, resume.id)]
    assert "assistant" in roles
    assert "tool" in roles


async def test_import_applies_sections_incrementally(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "R", "replace": "R\nBasics", "step": "basics"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Basics", "replace": "Basics\nExperience", "step": "experience"},
                        "edit-2",
                    )
                ]
            ),
            llm_message(content="Imported."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    # Two changed edits in one turn -> applied is 2, not 1. A runner that
    # counted once per *turn* rather than once per *changed edit* would be
    # stuck at 1 here regardless of how many sections were actually edited.
    assert run.applied == 2
    assert [s.status for s in _steps(db_session, run)] == ["done", "done"]


async def test_model_error_marks_failed_not_running(db_session, resume, run, monkeypatch, openai_enabled):
    _stub_upload(monkeypatch)
    # truncate=True drops the response.completed event, so the adapter never
    # gets a usable round and raises -- the same path a dropped connection or
    # a provider 5xx takes mid-turn.
    scripted = ScriptedOpenAI([llm_message(content="never seen")], truncate=True)
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "failed"
    assert run.status != "running"
    assert run.error
    assert resume.import_status == "failed"


async def test_failure_after_partial_progress_keeps_what_landed(
    db_session, resume, run, monkeypatch
):
    """The `failed` card's actual promise: "N sections were saved. Nothing
    you had was lost" -- not just "a failure is recorded as failed".

    `ScriptedOpenAI`'s `truncate` flag is constructor-level, applied to
    every round it serves, so it cannot express "two sections succeed, then
    the model fails" -- there is no per-round failure hook, and extending
    the shared scripted client for one test is a wider change than this
    needs (many other tests depend on its current shape). The behaviour
    under test lives entirely in `run_import`'s own event loop, not in the
    turn or the provider adapter, so the turn is faked directly at the name
    `import_runner.py` imports it under: a realistic prefix (a plan
    declaration, two labelled changed edits each followed by their `source`
    event) and then a raise, standing in for a dropped connection or a
    provider 5xx partway through a real turn.
    """
    _stub_upload(monkeypatch)

    async def fake_turn(*args, **kwargs):
        yield {
            "kind": "tool",
            "trace": {
                "name": DECLARE_PLAN_TOOL,
                "arguments": {
                    "steps": [
                        {"key": "basics", "label": "Basics"},
                        {"key": "experience", "label": "Experience"},
                    ]
                },
                "result": {"accepted": 2},
            },
        }
        yield {
            "kind": "tool",
            "trace": {
                "name": EDIT_TOOL,
                "arguments": {"search": "R", "replace": "R\nBasics", "step": "basics"},
                "result": {"ok": True, "changed": True, "source": "= R\nBasics"},
            },
        }
        yield {"kind": "source", "typst_source": "= R\nBasics"}
        yield {
            "kind": "tool",
            "trace": {
                "name": EDIT_TOOL,
                "arguments": {
                    "search": "Basics",
                    "replace": "Basics\nExperience",
                    "step": "experience",
                },
                "result": {"ok": True, "changed": True, "source": "= R\nBasics\nExperience"},
            },
        }
        yield {"kind": "source", "typst_source": "= R\nBasics\nExperience"}
        raise RuntimeError("provider connection dropped mid-turn")

    monkeypatch.setattr("app.services.import_runner.aiter_chat_edit", fake_turn)

    await run_import(db_session, resume, run)

    db_session.refresh(run)
    db_session.refresh(resume)
    assert run.status == "failed"
    assert run.error
    # The two sections that landed before the failure are still counted and
    # still done -- a crash mid-turn must not roll back work already applied.
    assert run.applied == 2
    assert [s.status for s in _steps(db_session, run)] == ["done", "done"]
    assert resume.import_status == "failed"


async def test_import_without_a_declared_plan_still_completes(
    db_session, resume, run, monkeypatch, openai_enabled
):
    _stub_upload(monkeypatch)
    # No declare_import_plan call at all -- just two unlabelled edits and a
    # final message. The import must still reach "done".
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[tool_call("apply_typst_edit", {"search": "R", "replace": "R\nA"}, "edit-1")]
            ),
            llm_message(
                tool_calls=[tool_call("apply_typst_edit", {"search": "A", "replace": "A\nB"}, "edit-2")]
            ),
            llm_message(content="Imported."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    assert run.applied == 2
    steps = _steps(db_session, run)
    assert [s.status for s in steps] == ["done", "done"]
    # Synthesised keys, never the literal "" -- see import_run.advance.
    assert all(s.key for s in steps)
    assert len({s.key for s in steps}) == 2


async def test_runner_uses_its_own_round_budget(db_session, resume, run, monkeypatch, openai_enabled):
    import app.services.import_runner as import_runner_module

    _stub_upload(monkeypatch)
    real_aiter_chat_edit = import_runner_module.aiter_chat_edit
    seen: dict = {}

    def wrapper(*args, **kwargs):
        seen.update(kwargs)
        return real_aiter_chat_edit(*args, **kwargs)

    monkeypatch.setattr(import_runner_module, "aiter_chat_edit", wrapper)

    scripted = ScriptedOpenAI([llm_message(content="Imported.")])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    await run_import(db_session, resume, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    assert seen["max_rounds"] == 14
    assert seen["prefer_full_source"] is False
    assert seen["timeout"] == 180.0
    assert seen["verify_compile"] is True
