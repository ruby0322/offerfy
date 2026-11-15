"""The jobs tools as the editor agent's turn loop sees them.

The handlers themselves are covered by tests/test_jobs_tools.py. What is
pinned here is the wiring: which tools are advertised to whom, that a tool
call runs and lands in the transcript, and that the compile-repair loop is
handed the same set as the main turn.
"""

import json

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import User
from app.services.jobs_tools import (
    READ_JOB_PROFILE_TOOL,
    READ_JOB_TOOL,
    SEARCH_JOBS_TOOL,
)

from tests.openai_script import ScriptedOpenAI, llm_message, tool_call
from tests.test_chat import post_chat
from tests.test_chat_family import _patch, _tool_names, _tool_rows
from tests.test_jobs_tools import _fresh


def _signed_in(client, db_session, sub="sub-chat-jobs"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def test_a_guest_turn_carries_the_catalogue_tools_but_not_the_personal_one(
    client, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = _patch(monkeypatch, [llm_message(content="Hi.")])
    assert post_chat(client, created["id"], json={"message": "hello"})["status"] == 200
    names = _tool_names(scripted.requests[0])
    assert {SEARCH_JOBS_TOOL, READ_JOB_TOOL} <= names
    assert READ_JOB_PROFILE_TOOL not in names


def test_a_signed_in_turn_carries_all_three(
    client, db_session, openai_enabled, monkeypatch
):
    _signed_in(client, db_session)
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = _patch(monkeypatch, [llm_message(content="Hi.")])
    assert post_chat(client, created["id"], json={"message": "hello"})["status"] == 200
    assert {
        SEARCH_JOBS_TOOL,
        READ_JOB_TOOL,
        READ_JOB_PROFILE_TOOL,
    } <= _tool_names(scripted.requests[0])


def test_a_search_call_runs_and_its_result_reaches_the_transcript(
    client, db_session, openai_enabled, monkeypatch
):
    _fresh(db_session, source_id="s:1", title="Payments Engineer", company="Stripe")
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[
                    tool_call(SEARCH_JOBS_TOOL, {"q": "Payments"}, "sj-1")
                ]
            ),
            llm_message(content="Found one at Stripe."),
        ],
    )
    result = post_chat(client, created["id"], json={"message": "find me a payments role"})
    assert result["status"] == 200
    traces = [row for row in _tool_rows(client, created["id"]) if row["name"] == SEARCH_JOBS_TOOL]
    assert len(traces) == 1
    assert traces[0]["result"]["total"] == 1
    assert traces[0]["result"]["items"][0]["company"] == "Stripe"


def test_a_read_job_call_returns_the_posting_to_the_model(
    client, db_session, openai_enabled, monkeypatch
):
    job = _fresh(db_session, source_id="s:1", description_text="You will build rails.")
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[tool_call(READ_JOB_TOOL, {"job_id": job.id}, "rj-1")]
            ),
            llm_message(content="Tailoring for it."),
        ],
    )
    assert post_chat(client, created["id"], json={"message": "tailor for this"})["status"] == 200
    # What the model was handed back, not only what the transcript stored.
    second = json.dumps(scripted.requests[1]["input"])
    assert "You will build rails." in second


def test_a_guest_calling_the_personal_tool_is_told_to_sign_in_not_errored(
    client, openai_enabled, monkeypatch
):
    """The spec is withheld from guests, but a stale transcript can still
    produce the call. A registered handler answers it; without one the loop
    would reply "unknown tool", which tells the model nothing useful."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[tool_call(READ_JOB_PROFILE_TOOL, {}, "jp-1")]
            ),
            llm_message(content="Please sign in."),
        ],
    )
    result = post_chat(client, created["id"], json={"message": "what am I looking for?"})
    assert result["status"] == 200
    trace = next(
        row for row in _tool_rows(client, created["id"]) if row["name"] == READ_JOB_PROFILE_TOOL
    )
    assert trace["result"]["requires_login"] is True


def test_the_repair_loop_is_handed_the_same_tools_as_the_main_turn(
    client, openai_enabled, monkeypatch
):
    """Both aiter_chat_edit call sites must get the same extra_tools. A tool
    present on the first round and absent on the repair round reads to the
    model as the catalogue disappearing mid-turn."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    edited = created["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    monkeypatch.setattr(
        "app.services.llm.turn.compile_digest", lambda source: {"compile": {"ok": True}}
    )
    verdicts = iter([{"ok": False, "error": "boom"}, {"ok": True}])
    monkeypatch.setattr(
        "app.routers.chat.compile_status", lambda source: next(verdicts, {"ok": True})
    )
    scripted = ScriptedOpenAI(
        [
            llm_message(
                tool_calls=[tool_call("apply_typst_edit", {"source": edited}, "edit-1")]
            ),
            llm_message(content="Renamed."),
            llm_message(content="Fixed it."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    assert post_chat(client, created["id"], json={"message": "rename me"})["status"] == 200
    first = _tool_names(scripted.requests[0])
    repair = _tool_names(scripted.requests[-1])
    assert SEARCH_JOBS_TOOL in first
    assert first == repair
