from fastapi.testclient import TestClient

from tests.openai_script import ScriptedOpenAI, llm_message


def _upload(client: TestClient, body: bytes = b"Hello from upload\ncontact@example.com"):
    return client.post(
        "/v1/resumes/upload",
        data={"title": "From file", "locale": "en"},
        files={"file": ("notes.txt", body, "text/plain")},
    )


def test_upload_is_idle_starter_without_extracted_comments(client: TestClient):
    response = _upload(client)
    assert response.status_code == 201
    data = response.json()
    assert data["import_status"] == "idle"
    assert data["source"] == "upload"
    assert "@preview/basic-resume:0.2.9" in data["typst_source"]
    assert "extracted from upload" not in data["typst_source"]
    assert "Hello from upload" not in data["typst_source"]
    assert "contact@example.com" not in data["typst_source"]
    messages = client.get(f"/v1/resumes/{data['id']}/messages").json()
    assert messages == []


def test_upload_does_not_call_llm(client: TestClient, openai_enabled, monkeypatch):
    scripted = ScriptedOpenAI([llm_message(content="should not run")])
    monkeypatch.setattr("app.services.llm.httpx.Client", lambda *a, **k: scripted)
    response = _upload(client)
    assert response.status_code == 201
    assert response.json()["import_status"] == "idle"
    assert scripted.requests == []
    got = client.get(f"/v1/resumes/{response.json()['id']}").json()
    assert "Ada Lovelace" not in got["typst_source"]
    assert "Hello from upload" not in got["typst_source"]
