"""The tailor runner: one server-owned turn, translated into run rows.

The two properties this feature exists for are pinned here: the pass writes
**no** user message, and it ends in a terminal status whatever happens, so a
reconnecting client is never left watching a run that nothing will finish.
"""

import json

import pytest

from app.models import ChatMessage, GuestSession, LlmCall, Resume, _utcnow
from app.services import tailor_run
from app.services.jobs_tools import READ_JOB_TOOL
from app.services.llm.prompts import EDIT_TOOL, READ_TOOL
from app.services.tailor_runner import (
    TIMEOUT_SECONDS,
    instruction,
    language_of,
    run_tailor,
)

from tests.openai_script import ScriptedOpenAI, llm_message, llm_usage, tool_call
from tests.test_jobs_api import _add_job

# ScriptedOpenAI speaks only the Responses API, so name an OpenAI row rather
# than relying on whatever model_choice would default to -- see
# tests/test_chat.py's OPENAI_ROW.
OPENAI_ROW = "gpt-6.1-terra"

pytestmark = [pytest.mark.filterwarnings("ignore::DeprecationWarning"), pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    """asyncio only, as tests/test_build_digests.py and test_jobs_score.py do.

    The run is driven by `asyncio.create_task` from a uvicorn worker, so
    asyncio is the only loop this code ever sees in production. Running the
    trio variant as well would only re-fail on `asyncio.to_thread` inside the
    Typst compile the edit path reaches -- a property of that helper, not of
    anything here.
    """
    return "asyncio"


@pytest.fixture
def master(db_session):
    guest = GuestSession(key_hash="deadbeef-tailor-runner", locale="zh-TW")
    db_session.add(guest)
    db_session.commit()
    resume = Resume(
        title="M",
        typst_source="= M",
        source="create",
        locale="zh-TW",
        kind="master",
        guest_session_id=guest.id,
    )
    db_session.add(resume)
    db_session.commit()
    return resume


@pytest.fixture
def tailor(db_session, master):
    resume = Resume(
        title="T",
        typst_source="= T",
        source="create",
        locale="zh-TW",
        kind="tailor",
        parent_resume_id=master.id,
        guest_session_id=master.guest_session_id,
        target_label="Backend @ Acme",
    )
    db_session.add(resume)
    db_session.commit()
    return resume


@pytest.fixture
def run(db_session, tailor):
    # One running pass per resume (partial unique index), so every test that
    # needs one reuses this.
    return tailor_run.start_run(db_session, tailor, job_id=None)


def _messages(db_session, resume_id) -> list[ChatMessage]:
    return (
        db_session.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


def _patch(monkeypatch, script):
    scripted = ScriptedOpenAI(script)
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    return scripted


# --- the prompt -------------------------------------------------------------


def test_the_prompt_names_the_language_because_nothing_else_can(db_session, tailor):
    """SYSTEM_PROMPT says "reply in the user's language", which works for a
    typed turn because their words are in the transcript. This turn has no
    user words at all, so the locale has to be stated or a zh-TW person gets
    an English answer."""
    assert "Traditional Chinese" in instruction(tailor, None)
    tailor.locale = "en"
    assert "English" in instruction(tailor, None)


def test_an_unknown_locale_falls_back_rather_than_omitting_the_language(db_session, tailor):
    tailor.locale = "kl-KL"
    assert "Reply in" in instruction(tailor, None)
    assert language_of(tailor)


def test_with_a_posting_the_prompt_sends_the_model_to_read_job(db_session, tailor):
    """The whole point of the simplification: the job description is fetched
    on demand by id instead of being copied into target_context and then
    replayed by build_role_card on every later turn."""
    text = instruction(tailor, "job-123")
    assert READ_JOB_TOOL in text
    assert "job-123" in text


def test_without_a_posting_the_prompt_points_at_the_document_context(db_session, tailor):
    text = instruction(tailor, None)
    assert READ_JOB_TOOL not in text
    assert "document context" in text


def test_the_prompt_tells_the_model_it_is_not_the_persons_message(db_session, tailor):
    """It used to be persisted as role="user", so the model answered it as if
    they had typed it and the person saw instructions attributed to them."""
    text = instruction(tailor, None)
    assert "not from them" in text
    assert READ_TOOL in text


# --- the turn ---------------------------------------------------------------


async def test_the_pass_writes_no_user_message(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    """The property this whole feature exists for."""
    _patch(monkeypatch, [llm_message(content="這份履歷和職缺的契合度如下…")])

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    roles = [m.role for m in _messages(db_session, tailor.id)]
    assert "user" not in roles
    assert "assistant" in roles


async def test_the_assistant_narration_lands_in_the_transcript(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    """So the conversation continues normally from here -- the card stands in
    for the user turn, it does not replace the agent's reply."""
    _patch(monkeypatch, [llm_message(content="Overall: a good fit.")])

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    assistant = [m for m in _messages(db_session, tailor.id) if m.role == "assistant"]
    assert [m.content for m in assistant] == ["Overall: a good fit."]


async def test_a_clean_pass_ends_done_with_no_phase(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    _patch(monkeypatch, [llm_message(content="Done.")])

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "done"
    assert run.phase is None
    assert run.error is None


async def test_tool_calls_are_persisted_and_move_the_phase_to_assessing(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    _patch(
        monkeypatch,
        [
            llm_message(tool_calls=[tool_call(READ_TOOL, {}, "rt-1")]),
            llm_message(content="Here is the fit."),
        ],
    )

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    rows = _messages(db_session, tailor.id)
    tools = [json.loads(m.content) for m in rows if m.role == "tool"]
    assert [t["name"] for t in tools] == [READ_TOOL]
    # The phase is cleared by finish(), so what is asserted is that it got
    # there: a run that only ever read would otherwise still say "reading".
    db_session.refresh(run)
    assert run.status == "done"


async def test_the_pass_can_read_the_posting_through_the_jobs_tool(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    """The tools the editor chat has are the tools this turn has -- without
    them the prompt's step 2 would come back "unknown tool"."""
    job = _add_job(db_session, source_id="s:1", company="Acme", title="Backend Engineer")
    _patch(
        monkeypatch,
        [
            llm_message(tool_calls=[tool_call(READ_JOB_TOOL, {"job_id": job.id}, "rj-1")]),
            llm_message(content="Assessed against the posting."),
        ],
    )

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    tools = [json.loads(m.content) for m in _messages(db_session, tailor.id) if m.role == "tool"]
    read = next(t for t in tools if t["name"] == READ_JOB_TOOL)
    assert read["result"]["company"] == "Acme"


async def test_an_edit_marks_the_phase_writing_and_moves_the_source(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    """The first pass is told not to edit, but if it does anyway the run must
    not claim it only read -- and the editor repaints on the source hash."""
    before = tailor_run.snapshot(db_session, tailor.id)["source_hash"]
    _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[tool_call(EDIT_TOOL, {"source": "= T\n\nEdited"}, "ed-1")]
            ),
            llm_message(content="Edited it."),
        ],
    )

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    db_session.refresh(tailor)
    assert "Edited" in tailor.typst_source
    assert tailor_run.snapshot(db_session, tailor.id)["source_hash"] != before


async def test_usage_is_billed_on_both_halves_of_the_seam(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    """One without the other is what usage.reconcile reports as an admin gap
    for that window forever."""
    _patch(
        monkeypatch,
        [llm_message(content="Done.", usage=llm_usage(input_tokens=100, output_tokens=20))],
    )

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    calls = db_session.query(LlmCall).filter(LlmCall.resume_id == tailor.id).all()
    assert len(calls) == 1
    # 'chat', not a new 'tailor' value: llm_calls.purpose is a closed CHECK
    # vocabulary and widening it is its own migration. See TAILOR_PURPOSE.
    assert calls[0].purpose == "chat"


async def test_a_provider_failure_ends_failed_not_running(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    """A run left `running` is the worst outcome: the card spins with nothing
    left to update it."""

    class Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise RuntimeError("provider exploded")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", Boom)

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "failed"
    assert run.error
    assert run.phase is None


async def test_run_tailor_never_raises(db_session, tailor, run, monkeypatch, openai_enabled):
    """Its callers are a detached asyncio task with nobody to catch it."""

    def explode(*a, **k):
        raise ValueError("nope")

    monkeypatch.setattr("app.services.tailor_runner.build_role_card", explode)

    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    db_session.refresh(run)
    assert run.status == "failed"


async def test_a_superseded_pass_stops_without_touching_the_new_runs_status(
    db_session, tailor, run, monkeypatch, openai_enabled
):
    """The old runner's task is in the other uvicorn worker and cannot be
    cancelled, so it discovers on its next event that the row moved on. It
    must not then mark the *new* run failed."""
    from datetime import timedelta

    run.heartbeat_at = _utcnow() - timedelta(seconds=tailor_run.STALE_AFTER_SECONDS + 1)
    db_session.commit()
    fresh = tailor_run.start_run(db_session, tailor, job_id=None)

    _patch(monkeypatch, [llm_message(content="late reply")])
    await run_tailor(db_session, tailor, run, model=OPENAI_ROW)

    db_session.refresh(fresh)
    assert fresh.status == "running"


def test_the_read_budget_stays_under_the_stale_threshold():
    assert TIMEOUT_SECONDS < tailor_run.STALE_AFTER_SECONDS
