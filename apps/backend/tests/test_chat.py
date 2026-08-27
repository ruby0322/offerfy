from fastapi.testclient import TestClient
from fastapi import HTTPException
from types import SimpleNamespace
import json
import threading

import pytest

from app.services.llm import completions_tool_extras, normalize_openai_model
from app.services.templates import apply_prompt
from app.services.typst_compile import typst_available
from tests.openai_script import ScriptedOpenAI, llm_message, tool_call, web_search_call


def parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        line = block.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload:
            events.append(json.loads(payload))
    return events


def post_chat(client: TestClient, resume_id: str, **kwargs) -> dict:
    with client.stream("POST", f"/v1/resumes/{resume_id}/chat", **kwargs) as response:
        status = response.status_code
        content_type = response.headers.get("content-type") or ""
        body_text = "".join(response.iter_text())
    if status != 200 or "event-stream" not in content_type:
        try:
            payload = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            payload = {"detail": body_text}
        return {
            "status": status,
            "events": [],
            "error": None,
            "json": payload,
        }
    events = parse_sse(body_text)
    listed = client.get(f"/v1/resumes/{resume_id}/messages").json()
    resume = client.get(f"/v1/resumes/{resume_id}").json()
    assistants = [event["message"] for event in events if event.get("type") == "assistant"]
    done = next((event for event in events if event.get("type") == "done"), {})
    error = next((event for event in events if event.get("type") == "error"), None)
    return {
        "status": status,
        "events": events,
        "error": error,
        "json": {
            "messages": listed,
            "message": assistants[-1] if assistants else None,
            "typst_source": done.get("typst_source") or resume.get("typst_source"),
            "applied": bool(done.get("applied", False)),
        },
    }


def _system_blobs(requests: list[dict]) -> str:
    parts: list[str] = []
    for body in requests:
        if body.get("instructions"):
            parts.append(str(body.get("instructions") or ""))
        for message in body.get("input") or []:
            if isinstance(message, dict) and message.get("role") == "system":
                parts.append(str(message.get("content") or ""))
    return "\n".join(parts)


def test_normalize_openai_model_maps_display_names():
    assert normalize_openai_model("GPT-5.6 Terra") == "gpt-5.6-terra"
    assert normalize_openai_model("gpt-5.6-terra") == "gpt-5.6-terra"
    assert normalize_openai_model("GPT-5.6 Sol") == "gpt-5.6-sol"
    assert normalize_openai_model("custom-proxy-model") == "custom-proxy-model"
    assert normalize_openai_model("") == "gpt-5.6-terra"


def test_gpt56_chat_completions_disables_reasoning_for_tools():
    assert completions_tool_extras("gpt-5.6-terra") == {"reasoning": {"effort": "none"}}
    assert completions_tool_extras("gpt-5.6-sol") == {"reasoning": {"effort": "none"}}
    assert completions_tool_extras("gpt-4o") == {}


def test_chat_503_without_key(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "Change the name"},
    )
    assert response.status_code == 503


def test_chat_read_then_search_replace_and_second_read(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    original = created["typst_source"]
    assert "Your Name" in original

    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada Lovelace"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-2")]),
            llm_message(content="Updated the name."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    assert [event.get("type") for event in result["events"]] == [
        "tool",
        "tool",
        "source",
        "tool",
        "assistant",
        "done",
    ]
    body = result["json"]
    assert "Ada Lovelace" in body["typst_source"]
    assert body["applied"] is True
    assert body["message"]["content"] == "Updated the name."
    listed = client.get(f"/v1/resumes/{created['id']}/messages").json()
    tool_rows = [row for row in listed if row["role"] == "tool"]
    assert [json.loads(row["content"])["name"] for row in tool_rows] == [
        "read_typst",
        "apply_typst_edit",
        "read_typst",
    ]
    edit = json.loads(tool_rows[1]["content"])
    assert edit["arguments"]["search"] == "Your Name"
    assert edit["arguments"]["replace"] == "Ada Lovelace"
    assert edit["result"]["ok"] is True
    assert edit["result"]["changed"] is True
    assert edit["result"]["previous_source"] == original
    assert edit["result"]["diff"] == [
        {"op": "del", "text": '#let name = "Your Name"'},
        {"op": "add", "text": '#let name = "Ada Lovelace"'},
    ]
    response_tools = [row for row in body.get("messages") or [] if row["role"] == "tool"]
    assert len(response_tools) == 3

    systems = _system_blobs(scripted.requests)
    assert "Current Typst source" not in systems
    assert "#let name" not in systems
    assert original not in systems
    assert 'Keep #import "@preview/basic-resume:0.2.9"' not in systems
    assert "unless the user asks to switch" in systems

    assert len(scripted.requests) >= 4
    second_read_result = None
    for item in scripted.requests[3].get("input") or []:
        if isinstance(item, dict) and item.get("type") == "function_call_output" and item.get("call_id") == "read-2":
            second_read_result = item.get("output") or ""
    assert second_read_result is not None
    assert "Ada Lovelace" in second_read_result
    edit_model_out = None
    for body_req in scripted.requests:
        for item in body_req.get("input") or []:
            if (
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "edit-1"
            ):
                raw = item.get("output") or "{}"
                edit_model_out = json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(edit_model_out, dict)
    assert "previous_source" not in edit_model_out
    assert "@preview/basic-resume:0.2.9" in body["typst_source"]
    assert 'paper: "a4"' in body["typst_source"]
    assert 'accent-color: "#f4be82"' in body["typst_source"]
    assert scripted.requests[0]["model"] == "gpt-5.6-terra"
    assert scripted.requests[0]["reasoning"]["effort"] == "none"
    tool_types = [row.get("type") for row in scripted.requests[0].get("tools") or []]
    assert "web_search" in tool_types


def test_chat_stale_source_plus_search_replace_applies_search(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    original = created["typst_source"]
    stale = original + "\n#let extra = 1\n"
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {
                            "source": stale,
                            "search": "Your Name",
                            "replace": "Ada Lovelace",
                        },
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Renamed."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    body = result["json"]
    assert "Ada Lovelace" in body["typst_source"]
    assert "extra" not in body["typst_source"]
    assert body["applied"] is True
    edit = json.loads(
        next(row["content"] for row in body["messages"] if row["role"] == "tool"
             and "apply_typst_edit" in row["content"])
    )
    assert any(row["op"] == "add" and "Ada Lovelace" in row["text"] for row in edit["result"]["diff"])


def test_chat_noop_search_with_full_source_rewrites(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    rewritten = created["typst_source"].replace("Your Name", "Ada Lovelace", 1) + "\n// CAMP intern\n"
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {
                            "source": rewritten,
                            "search": "Your Name",
                            "replace": "Your Name",
                        },
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Rewrote from the attachment."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Rewrite from my old resume"})
    assert result["status"] == 200
    body = result["json"]
    assert "Ada Lovelace" in body["typst_source"]
    assert "CAMP intern" in body["typst_source"]
    assert body["applied"] is True


def test_chat_full_source_write_includes_real_diff(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    rewritten = created["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": rewritten, "search": "", "replace": ""},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Rewrote the name."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is True
    assert "Ada Lovelace" in body["typst_source"]
    edit = json.loads(
        next(row["content"] for row in body["messages"] if row["role"] == "tool"
             and "apply_typst_edit" in row["content"])
    )
    assert edit["result"]["changed"] is True
    assert edit["result"]["diff"] == [
        {"op": "del", "text": '#let name = "Your Name"'},
        {"op": "add", "text": '#let name = "Ada Lovelace"'},
    ]


def test_chat_noop_source_write_is_not_applied(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": created["typst_source"]},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Already correct."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Fix the name"})
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is False
    assert body["typst_source"] == created["typst_source"]


def test_chat_read_only_does_not_mark_applied(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(content="Looks good."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "What is on this resume?"})
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is False
    assert body["typst_source"] == created["typst_source"]


def test_chat_web_search_is_traced(client: TestClient, openai_enabled, monkeypatch):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI(
        [
            llm_message(
                web_search=web_search_call(
                    "Ada Lovelace biography",
                    sources=[{"url": "https://en.wikipedia.org/wiki/Ada_Lovelace", "title": "Ada Lovelace"}],
                ),
                content="Ada Lovelace wrote notes on the Analytical Engine.",
            )
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Who was Ada Lovelace?"})
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is False
    assert "Analytical Engine" in body["message"]["content"]
    tools = [json.loads(row["content"]) for row in body["messages"] if row["role"] == "tool"]
    assert [row["name"] for row in tools] == ["web_search"]
    assert tools[0]["arguments"]["query"] == "Ada Lovelace biography"
    assert tools[0]["result"]["sources"][0]["url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"
    assert scripted.requests[0]["include"] == ["web_search_call.action.sources"]


def test_chat_read_ats_returns_failed_checks(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    monkeypatch.setattr("app.services.typst_compile.compile_typst", lambda source, fmt: b"%PDF-ats")
    monkeypatch.setattr(
        "app.services.ats.analyze_pdf",
        lambda pdf, source: {
            "checks": [
                {"name": "text_extractable", "passed": True},
                {"name": "dates_machine_readable", "passed": False},
            ]
        },
    )
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_ats", {}, "ats-1")]),
            llm_message(content="Dates should use YYYY-MM or Present."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Why did the ATS check fail?"})
    assert result["status"] == 200
    body = result["json"]
    tools = [json.loads(row["content"]) for row in body["messages"] if row["role"] == "tool"]
    assert [row["name"] for row in tools] == ["read_ats"]
    ats = tools[0]["result"]
    assert ats["passed"] == 1
    assert ats["total"] == 2
    assert ats["failed"] == ["dates_machine_readable"]
    names = [c["name"] for c in ats["checks"]]
    assert names == ["text_extractable", "dates_machine_readable"]
    tool_names = [
        row.get("name") for row in scripted.requests[0].get("tools") or [] if row.get("type") == "function"
    ]
    assert "read_ats" in tool_names
    assert "read_ats" in _system_blobs(scripted.requests)
    assert "error" not in ats


def test_chat_read_ats_compile_error(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()

    def boom(source, fmt):
        raise HTTPException(status_code=400, detail="Typst compile failed: error: missing")

    monkeypatch.setattr("app.services.typst_compile.compile_typst", boom)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_ats", {}, "ats-1")]),
            llm_message(content="The PDF does not compile yet."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Check ATS"})
    assert result["status"] == 200
    tools = [
        json.loads(row["content"])
        for row in result["json"]["messages"]
        if row["role"] == "tool"
    ]
    assert tools[0]["name"] == "read_ats"
    assert "error" in tools[0]["result"]
    assert "compile" in tools[0]["result"]["error"].lower()


def test_chat_surfaces_provider_error_message(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()

    class FailingOpenAI:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            payload = {"error": {"message": "invalid model ID", "type": "invalid_request_error"}}
            return SimpleNamespace(status_code=400, json=lambda: payload, text="bad")

    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", FailingOpenAI)
    result = post_chat(client, created["id"], json={"message": "Rename me"})
    assert result["status"] == 200
    assert result["error"] is not None
    assert result["error"]["detail"] == "invalid model ID"
    assert result["error"]["status"] == 502
    listed = client.get(f"/v1/resumes/{created['id']}/messages").json()
    assert any(row["role"] == "user" for row in listed)


def _first_user_content(requests: list[dict]):
    for body in requests:
        for item in body.get("input") or []:
            if isinstance(item, dict) and item.get("role") == "user":
                return item.get("content")
    return None


def test_chat_attachment_txt_reaches_llm_and_is_stored(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI([llm_message(content="Noted the attached notes.")])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(
        client,
        created["id"],
        data={"message": "Fill from this file"},
        files={"file": ("notes.txt", b"Ada Lovelace\nada@example.com\n", "text/plain")},
    )
    assert result["status"] == 200
    body = result["json"]
    users = [row for row in body["messages"] if row["role"] == "user"]
    assert users[-1]["content"].startswith("Attached file: notes.txt")
    assert "Fill from this file" in users[-1]["content"]
    sent = _first_user_content(scripted.requests)
    assert isinstance(sent, str)
    assert "Ada Lovelace" in sent
    assert "ada@example.com" in sent
    assert "notes.txt" in sent
    assert "full `source`" in sent
    systems = _system_blobs(scripted.requests)
    assert "MUST change the #import" not in systems


FILL_PROMPT = (
    "Fill the basic-resume template from this uploaded resume. "
    "Call read_typst, then apply_typst_edit with the full source. "
    "Map name, email, education, work, projects, and skills. "
    "Keep the basic-resume package, A4 paper, and accent color."
)


def test_chat_fill_prompt_with_file_rewrites_source(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    filled = created["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": filled, "search": "", "replace": ""},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Filled the template from the upload."),
        ]
    )
    timeouts: list[float] = []

    def fake_client(*args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return scripted

    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", fake_client)
    result = post_chat(
        client,
        created["id"],
        data={"message": FILL_PROMPT, "prefer_full_source": "true"},
        files={"file": ("notes.txt", b"Ada Lovelace\nada@example.com\n", "text/plain")},
    )
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is True
    assert "Ada Lovelace" in body["typst_source"]
    users = [row for row in body["messages"] if row["role"] == "user"]
    assert users[-1]["content"].startswith("Attached file: notes.txt")
    assert "Fill the basic-resume template" in users[-1]["content"]
    assert timeouts == [120.0]


@pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")
def test_chat_fill_prompt_repairs_compile_error(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    broken = created["typst_source"] + "\n#not-a-function()\n"
    fixed = created["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": broken, "search": "", "replace": ""},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Filled the template from the upload."),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": fixed, "search": "", "replace": ""},
                        "edit-2",
                    )
                ]
            ),
            llm_message(content="Fixed the compile errors."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(
        client,
        created["id"],
        data={"message": FILL_PROMPT, "prefer_full_source": "true"},
        files={"file": ("notes.txt", b"Ada Lovelace\nada@example.com\n", "text/plain")},
    )
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is True
    assert "Ada Lovelace" in body["typst_source"]
    assert "#not-a-function()" not in body["typst_source"]
    blobs = json.dumps(scripted.requests)
    assert (
        "Typst compile failed after the edit" in blobs
        or "Escape special characters" in blobs
    )
    edits = _edit_results(body)
    assert edits[0]["result"]["compile"]["ok"] is False
    assert edits[-1]["result"]["compile"]["ok"] is True
    preview = client.get(f"/v1/resumes/{created['id']}/preview")
    assert preview.status_code == 200


@pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")
def test_chat_plain_edit_repairs_compile_error(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    broken = created["typst_source"] + "\n#not-a-function()\n"
    fixed = created["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": broken, "search": "", "replace": ""},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Updated the name."),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": fixed, "search": "", "replace": ""},
                        "edit-2",
                    )
                ]
            ),
            llm_message(content="Fixed the compile errors."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is True
    assert "Ada Lovelace" in body["typst_source"]
    assert "#not-a-function()" not in body["typst_source"]
    blobs = json.dumps(scripted.requests)
    assert (
        "Typst compile failed after the edit" in blobs
        or "Escape special characters" in blobs
    )
    edits = _edit_results(body)
    assert edits[0]["result"]["compile"]["ok"] is False
    assert edits[-1]["result"]["compile"]["ok"] is True
    preview = client.get(f"/v1/resumes/{created['id']}/preview")
    assert preview.status_code == 200


@pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")
def test_chat_edit_sanitizes_unescaped_quotes_so_it_compiles(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    messy = created["typst_source"].replace("Your Name", 'Wei "Eric" Chen', 1)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": messy, "search": "", "replace": ""},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Updated the name."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Set my name to Wei Eric Chen"})
    assert result["status"] == 200
    body = result["json"]
    assert r'Wei \"Eric\" Chen' in body["typst_source"]
    assert body["applied"] is True
    preview = client.get(f"/v1/resumes/{created['id']}/preview")
    assert preview.status_code == 200
    blobs = json.dumps(scripted.requests)
    assert "Typst compile failed after the edit" not in blobs


def test_chat_attachment_only_file_is_allowed(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI([llm_message(content="Using the file.")])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(
        client,
        created["id"],
        data={"message": ""},
        files={"file": ("jd.txt", b"Seeking a Python engineer.\n", "text/plain")},
    )
    assert result["status"] == 200
    users = [row for row in result["json"]["messages"] if row["role"] == "user"]
    assert users[-1]["content"].startswith("Attached file: jd.txt")
    sent = _first_user_content(scripted.requests)
    assert "Seeking a Python engineer." in sent


def test_chat_rejects_unsupported_attachment(client: TestClient, openai_enabled):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        data={"message": "here"},
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_chat_template_switch_rewrites_full_source_despite_unmatched_search(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    rewritten = (
        '#import "@preview/brilliant-cv:4.1.0": *\n'
        '#let name = "Ada Lovelace"\n'
    )
    prompt = apply_prompt("brilliant-cv", "4.1.0")
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {
                            "source": rewritten,
                            "search": "this-string-is-not-in-the-document",
                            "replace": "noop",
                        },
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Switched to brilliant-cv."),
        ]
    )
    timeouts: list[float] = []

    def fake_client(*args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return scripted

    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", fake_client)
    result = post_chat(client, created["id"], json={"message": prompt})
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is True
    assert "@preview/brilliant-cv:4.1.0" in body["typst_source"]
    assert "Ada Lovelace" in body["typst_source"]
    assert "@preview/basic-resume:0.2.9" not in body["typst_source"]
    systems = _system_blobs(scripted.requests)
    assert "MUST change the #import" in systems
    assert "Do not keep @preview/basic-resume unless that is the requested package" in systems
    assert timeouts == [120.0]


def test_chat_prefer_full_source_flag_writes_document(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    rewritten = (
        '#import "@preview/altacv:1.6.0": *\n'
        '#let name = "Ada Lovelace"\n'
    )
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {
                            "source": rewritten,
                            "search": "missing-needle",
                            "replace": "noop",
                        },
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Rewrote."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(
        client,
        created["id"],
        json={"message": "Rewrite the whole file", "prefer_full_source": True},
    )
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is True
    assert "@preview/altacv:1.6.0" in body["typst_source"]
    systems = _system_blobs(scripted.requests)
    assert "MUST change the #import" not in systems


def _edit_results(body: dict) -> list[dict]:
    out: list[dict] = []
    for row in body.get("messages") or []:
        if row.get("role") != "tool" or "apply_typst_edit" not in (row.get("content") or ""):
            continue
        out.append(json.loads(row["content"]))
    return out


ACORN_BROKEN = """#import "@preview/acorn-resume:0.1.0": *

#show: resume.with(author: "Ada Lovelace")
#section("Education")
"""

ACORN_FIXED = """#import "@preview/acorn-resume:0.1.0": *

#show: resume.with(author: "Ada Lovelace")

#header(
  name: "Ada Lovelace",
  contacts: (("mailto:ada@example.com", "ada@example.com"),),
)

== Education
#edu(
  degree: "Mathematics",
  date: "1830 – 1834",
  institution: "University of London",
  location: "London, UK",
)
"""


def test_chat_template_switch_includes_package_example(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    prompt = apply_prompt("acorn-resume", "0.1.0")
    scripted = ScriptedOpenAI([llm_message(content="ok")])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": prompt})
    assert result["status"] == 200
    systems = _system_blobs(scripted.requests)
    assert "#exp(" in systems
    assert "#header(" in systems
    assert "Do not invent helpers like #section or #entry" in systems


@pytest.mark.skipif(not typst_available(), reason="Typst CLI is required")
def test_chat_template_switch_repairs_compile_error(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    prompt = apply_prompt("acorn-resume", "0.1.0")
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": ACORN_BROKEN},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Switched to acorn-resume."),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"source": ACORN_FIXED},
                        "edit-2",
                    )
                ]
            ),
            llm_message(content="Fixed the compile errors."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": prompt})
    assert result["status"] == 200
    body = result["json"]
    assert body["applied"] is True
    assert "#section(" not in body["typst_source"]
    assert "#edu(" in body["typst_source"]
    edits = _edit_results(body)
    assert edits[0]["result"]["compile"]["ok"] is False
    assert "section" in edits[0]["result"]["compile"]["error"].lower()
    assert edits[-1]["result"]["compile"]["ok"] is True
    preview = client.get(f"/v1/resumes/{created['id']}/preview")
    assert preview.status_code == 200


def test_chat_noop_edit_retries_instead_of_asking_user(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Your Name"},
                        "edit-noop",
                    )
                ]
            ),
            llm_message(content="I could not apply the change. Please try again."),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada Lovelace"},
                        "edit-retry",
                    )
                ]
            ),
            llm_message(content="Updated the name."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    body = result["json"]
    assert "Ada Lovelace" in body["typst_source"]
    assert body["applied"] is True
    assert body["message"]["content"] == "Updated the name."
    assert "try again" not in body["message"]["content"].lower()

    listed = client.get(f"/v1/resumes/{created['id']}/messages").json()
    tools = [json.loads(row["content"]) for row in listed if row["role"] == "tool"]
    edits = [row for row in tools if row["name"] == "apply_typst_edit"]
    assert len(edits) == 2
    assert edits[0]["result"]["changed"] is False
    hint = edits[0]["result"].get("hint") or ""
    assert hint
    assert edits[1]["result"]["changed"] is True

    systems = _system_blobs(scripted.requests)
    assert "Do not ask the user to retry" in systems

    nudged = False
    for request in scripted.requests:
        for item in request.get("input") or []:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            text = content if isinstance(content, str) else ""
            if "did not change" in text.lower() or "not change the document" in text.lower():
                nudged = True
    assert nudged is True


def test_chat_search_not_found_retries_instead_of_stopping(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "this-string-is-not-in-the-document", "replace": "Ada"},
                        "edit-miss",
                    )
                ]
            ),
            llm_message(content="Please try again."),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada Lovelace"},
                        "edit-hit",
                    )
                ]
            ),
            llm_message(content="Updated the name."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    body = result["json"]
    assert "Ada Lovelace" in body["typst_source"]
    assert body["message"]["content"] == "Updated the name."
    edits = _edit_results(body)
    assert "error" in edits[0]["result"]
    assert edits[-1]["result"]["changed"] is True


def test_compile_status_runs_off_request_thread(
    client: TestClient, openai_enabled, monkeypatch
):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    main_ident = threading.get_ident()
    seen: list[int] = []

    def wrapped(source: str):
        seen.append(threading.get_ident())
        return {"ok": True}

    monkeypatch.setattr("app.services.llm.compile_status", wrapped)
    scripted = ScriptedOpenAI(
        [
            llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
            llm_message(
                tool_calls=[
                    tool_call(
                        "apply_typst_edit",
                        {"search": "Your Name", "replace": "Ada Lovelace"},
                        "edit-1",
                    )
                ]
            ),
            llm_message(content="Updated the name."),
        ]
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    result = post_chat(client, created["id"], json={"message": "Rename me to Ada Lovelace"})
    assert result["status"] == 200
    assert seen
    assert all(ident != main_ident for ident in seen)
