import json

from app.models import MemoryItem
from tests.openai_script import ScriptedOpenAI, llm_message, tool_call
from tests.test_chat import _system_blobs, post_chat


def _patch(monkeypatch, script):
    scripted = ScriptedOpenAI(script)
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    return scripted


def _tool_rows(client, resume_id):
    return [
        json.loads(row["content"])
        for row in client.get(f"/v1/resumes/{resume_id}/messages").json()
        if row["role"] == "tool"
    ]


def _tool_names(request: dict) -> set:
    return {tool.get("name") for tool in request.get("tools") or []}


def test_create_tailor_from_tailor_chat_emits_event_and_keeps_chats_apart(
    client, openai_enabled, monkeypatch
):
    master = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    first = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "create_tailor",
                        {
                            "title": "Beta PM",
                            "target_label": "PM @ Beta",
                            "target_context": "Roadmaps",
                        },
                        "ct-1",
                    )
                ]
            ),
            llm_message(content="Created the Beta tailor."),
        ],
    )
    result = post_chat(client, first["id"], json={"message": "Make one for the Beta PM role"})
    types = [event["type"] for event in result["events"]]
    assert types.index("tailor_created") < types.index("done")
    created = next(e for e in result["events"] if e["type"] == "tailor_created")
    assert created["title"] == "Beta PM"
    new = client.get(f"/v1/resumes/{created['resume_id']}").json()
    assert new["parent_resume_id"] == master["id"]
    assert new["target_context"] == "Roadmaps"
    assert client.get(f"/v1/resumes/{new['id']}/messages").json() == []
    first_rows = client.get(f"/v1/resumes/{first['id']}/messages").json()
    assert any(row["role"] == "assistant" for row in first_rows)


def test_create_tailor_tool_can_fork_the_open_tailor(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    first = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    client.put(
        f"/v1/resumes/{first['id']}",
        json={"typst_source": first["typst_source"] + "\n// tailor-only"},
    )
    _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[
                    tool_call(
                        "create_tailor",
                        {"title": "Beta PM", "fork_from": "current"},
                        "ct-1",
                    )
                ]
            ),
            llm_message(content="Created it from this version."),
        ],
    )
    result = post_chat(client, first["id"], json={"message": "another one from this version"})
    created = next(e for e in result["events"] if e["type"] == "tailor_created")
    new = client.get(f"/v1/resumes/{created['resume_id']}").json()
    assert "tailor-only" in new["typst_source"]
    assert new["parent_resume_id"] == master["id"]
    assert _tool_rows(client, first["id"])[0]["result"]["forked_from"] == "current"


def test_create_tailor_tool_defaults_to_the_master(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    first = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    client.put(
        f"/v1/resumes/{first['id']}",
        json={"typst_source": first["typst_source"] + "\n// tailor-only"},
    )
    _patch(
        monkeypatch,
        [
            llm_message(tool_calls=[tool_call("create_tailor", {"title": "Beta PM"}, "ct-1")]),
            llm_message(content="Created it."),
        ],
    )
    result = post_chat(client, first["id"], json={"message": "one for Beta"})
    created = next(e for e in result["events"] if e["type"] == "tailor_created")
    new = client.get(f"/v1/resumes/{created['resume_id']}").json()
    assert "tailor-only" not in new["typst_source"]
    assert _tool_rows(client, first["id"])[0]["result"]["forked_from"] == "master"


def test_create_tailor_rejects_unknown_fork_source(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[
                    tool_call("create_tailor", {"title": "X", "fork_from": "sibling"}, "ct-1")
                ]
            ),
            llm_message(content="Could not."),
        ],
    )
    result = post_chat(client, master["id"], json={"message": "make one"})
    assert "tailor_created" not in [e["type"] for e in result["events"]]
    assert "fork_from" in _tool_rows(client, master["id"])[0]["result"]["error"]


def test_create_tailor_once_per_turn(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    _patch(
        monkeypatch,
        [
            llm_message(
                tool_calls=[
                    tool_call("create_tailor", {"title": "One"}, "a"),
                    tool_call("create_tailor", {"title": "Two"}, "b"),
                ]
            ),
            llm_message(content="Done."),
        ],
    )
    result = post_chat(client, master["id"], json={"message": "two tailors"})
    assert [e["type"] for e in result["events"]].count("tailor_created") == 1
    family = client.get(f"/v1/resumes/{master['id']}/family").json()
    assert [row["title"] for row in family["tailors"]] == ["One"]


def test_role_card_and_accepted_memory_in_instructions_only(
    client, openai_enabled, monkeypatch, db_session
):
    master = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    tailor = client.post(
        f"/v1/resumes/{master['id']}/tailors",
        json={"title": "T", "target_label": "SWE @ Acme", "target_context": "Kafka"},
    ).json()
    db_session.add(
        MemoryItem(master_resume_id=master["id"], text="Never list GPA", status="accepted")
    )
    db_session.add(
        MemoryItem(master_resume_id=master["id"], text="Pending thing", status="pending")
    )
    db_session.commit()
    scripted = _patch(monkeypatch, [llm_message(content="ok")])
    post_chat(client, tailor["id"], json={"message": "hi"})
    systems = _system_blobs(scripted.requests)
    assert "TAILOR" in systems
    assert "SWE @ Acme" in systems
    assert "Kafka" in systems
    assert "Never list GPA" in systems
    assert "Pending thing" not in systems
    assert {"read_master", "create_tailor", "propose_memory"} <= _tool_names(scripted.requests[0])
    for row in client.get(f"/v1/resumes/{tailor['id']}/messages").json():
        assert "Never list GPA" not in row["content"]
        assert "Kafka" not in row["content"]


def test_master_chat_has_no_read_master(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = _patch(monkeypatch, [llm_message(content="ok")])
    post_chat(client, master["id"], json={"message": "hi"})
    names = _tool_names(scripted.requests[0])
    assert "read_master" not in names
    assert {"create_tailor", "propose_memory", "read_typst"} <= names
    assert "MASTER" in _system_blobs(scripted.requests)


def test_read_master_returns_master_source(client, openai_enabled, monkeypatch):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "T"}).json()
    client.put(
        f"/v1/resumes/{master['id']}",
        json={"typst_source": master["typst_source"] + "\n// master-v2"},
    )
    _patch(
        monkeypatch,
        [
            llm_message(tool_calls=[tool_call("read_master", {}, "rm")]),
            llm_message(content="read"),
        ],
    )
    post_chat(client, tailor["id"], json={"message": "what is in master"})
    rows = _tool_rows(client, tailor["id"])
    assert rows[0]["name"] == "read_master"
    assert "master-v2" in rows[0]["result"]["source"]
    tailor_now = client.get(f"/v1/resumes/{tailor['id']}").json()
    assert "master-v2" not in tailor_now["typst_source"]


def test_guest_propose_memory_says_login_required(
    client, openai_enabled, monkeypatch, db_session
):
    master = client.post("/v1/resumes", json={"locale": "en"}).json()
    _patch(
        monkeypatch,
        [
            llm_message(tool_calls=[tool_call("propose_memory", {"text": "Prefers one page"}, "pm")]),
            llm_message(content="Suggested."),
        ],
    )
    post_chat(client, master["id"], json={"message": "remember I like one page"})
    item = db_session.query(MemoryItem).one()
    assert item.status == "pending"
    rows = _tool_rows(client, master["id"])
    assert rows[0]["result"]["requires_login"] is True
    assert rows[0]["result"]["item_id"] == item.id
