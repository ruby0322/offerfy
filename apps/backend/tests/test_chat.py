from fastapi.testclient import TestClient
from types import SimpleNamespace
import json

from app.services.llm import completions_tool_extras, normalize_openai_model
from tests.openai_script import ScriptedOpenAI, llm_message, tool_call, web_search_call


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
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)

    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "Rename me to Ada Lovelace"},
    )
    assert response.status_code == 200
    body = response.json()
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

    assert len(scripted.requests) >= 4
    second_read_result = None
    for item in scripted.requests[3].get("input") or []:
        if isinstance(item, dict) and item.get("type") == "function_call_output" and item.get("call_id") == "read-2":
            second_read_result = item.get("output") or ""
    assert second_read_result is not None
    assert "Ada Lovelace" in second_read_result
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
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "Rename me to Ada Lovelace"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Ada Lovelace" in body["typst_source"]
    assert "extra" not in body["typst_source"]
    assert body["applied"] is True
    edit = json.loads(
        next(row["content"] for row in body["messages"] if row["role"] == "tool"
             and "apply_typst_edit" in row["content"])
    )
    assert any(row["op"] == "add" and "Ada Lovelace" in row["text"] for row in edit["result"]["diff"])


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
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "Rename me to Ada Lovelace"},
    )
    assert response.status_code == 200
    body = response.json()
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
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "Fix the name"},
    )
    assert response.status_code == 200
    body = response.json()
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
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "What is on this resume?"},
    )
    assert response.status_code == 200
    body = response.json()
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
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "Who was Ada Lovelace?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is False
    assert "Analytical Engine" in body["message"]["content"]
    tools = [json.loads(row["content"]) for row in body["messages"] if row["role"] == "tool"]
    assert [row["name"] for row in tools] == ["web_search"]
    assert tools[0]["arguments"]["query"] == "Ada Lovelace biography"
    assert tools[0]["result"]["sources"][0]["url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"
    assert scripted.requests[0]["include"] == ["web_search_call.action.sources"]


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

        def post(self, url, headers=None, json=None):
            payload = {"error": {"message": "invalid model ID", "type": "invalid_request_error"}}
            return SimpleNamespace(status_code=400, json=lambda: payload, text="bad")

    monkeypatch.setattr("app.services.llm.httpx.Client", FailingOpenAI)
    response = client.post(
        f"/v1/resumes/{created['id']}/chat",
        json={"message": "Rename me"},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "invalid model ID"
