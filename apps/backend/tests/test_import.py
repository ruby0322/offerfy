from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.openai_script import ScriptedOpenAI, llm_message, tool_call

FILLED = """#import "@preview/basic-resume:0.2.9": *

#let name = "Ada Lovelace"
#let location = "London, UK"
#let email = "ada@example.com"
#let github = ""
#let linkedin = ""
#let phone = ""
#let personal-site = ""

#show: resume.with(
  author: name,
  location: location,
  email: email,
  accent-color: "#f4be82",
  font: "New Computer Modern",
  paper: "a4",
  lang: "en",
  author-position: left,
  personal-info-position: left,
)

== Education

#edu(
  institution: "University of London",
  location: "London, UK",
  dates: dates-helper(start-date: "1830-09", end-date: "1834-06"),
  degree: "Mathematics",
)

== Experience

#work(
  title: "Analyst",
  location: "London, UK",
  company: "Analytical Engine",
  dates: dates-helper(start-date: "1842-01", end-date: "Present"),
)
- Wrote notes on the Analytical Engine
"""


def _upload(client: TestClient, body: bytes = b"Hello from upload\ncontact@example.com"):
    return client.post(
        "/v1/resumes/upload",
        data={"title": "From file", "locale": "en"},
        files={"file": ("notes.txt", body, "text/plain")},
    )


def _import_script(source: str = FILLED) -> list[dict]:
    return [
        llm_message(tool_calls=[tool_call("read_typst", {}, "read-1")]),
        llm_message(
            tool_calls=[tool_call("apply_typst_edit", {"source": source}, "edit-1")]
        ),
        llm_message(content="Filled."),
    ]


def test_upload_without_key_is_idle(client: TestClient):
    response = _upload(client)
    assert response.status_code == 201
    assert response.json()["import_status"] == "idle"


def test_upload_201_is_pending_before_fill(
    client: TestClient, openai_enabled, monkeypatch
):
    scripted = ScriptedOpenAI(_import_script())
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    monkeypatch.setattr(
        "app.services.import_resume.compile_typst",
        lambda *a, **k: b"%PDF-1.4",
    )
    response = _upload(client)
    assert response.status_code == 201
    data = response.json()
    assert data["import_status"] == "pending"
    assert '#let name = "Ada Lovelace"' not in data["typst_source"]
    assert "Hello from upload" in data["typst_source"]


def test_import_fills_name_email_and_invariants(
    client: TestClient, openai_enabled, monkeypatch
):
    scripted = ScriptedOpenAI(_import_script())
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    monkeypatch.setattr(
        "app.services.import_resume.compile_typst",
        lambda *a, **k: b"%PDF-1.4",
    )
    created = _upload(client).json()
    got = client.get(f"/v1/resumes/{created['id']}").json()
    assert got["import_status"] == "done"
    source = got["typst_source"]
    assert "Ada Lovelace" in source
    assert "ada@example.com" in source
    assert '@preview/basic-resume:0.2.9' in source
    assert 'paper: "a4"' in source
    assert 'accent-color: "#f4be82"' in source
    messages = client.get(f"/v1/resumes/{created['id']}/messages").json()
    assert any("Filled the template from the upload." in row["content"] for row in messages)


def test_import_compile_fail_sets_failed(
    client: TestClient, openai_enabled, monkeypatch
):
    scripted = ScriptedOpenAI(_import_script())
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)

    def boom(*_a, **_k):
        raise HTTPException(status_code=400, detail="Typst compile failed: error")

    monkeypatch.setattr("app.services.import_resume.compile_typst", boom)
    created = _upload(client).json()
    got = client.get(f"/v1/resumes/{created['id']}").json()
    assert got["import_status"] == "failed"
    assert '#let name = "Ada Lovelace"' not in got["typst_source"]
    assert "Hello from upload" in got["typst_source"]
    messages = client.get(f"/v1/resumes/{created['id']}/messages").json()
    assert any(row["role"] == "assistant" for row in messages)
