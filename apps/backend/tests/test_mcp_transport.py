"""The agent surface's door, and (from Task 4) the transport behind it.

The auth cases run against a standalone probe app, not app.main.app: a route
registered on the shared module-level app outlives the test that added it and
pollutes every later one. The `client` fixture is used only to stand up the
test database.
"""

import json
from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_db, get_session_factory
from app.deps import SESSION_COOKIE, _sign, require_agent_caller
from app.services.agent_keys import mint_agent_key
from app.services.guest import GUEST_COOKIE
from app.services.talent_keys import mint_talent_key

PROBE = "/probe"


def _bearer(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture
def probe(client):
    """A throwaway app whose one route sits behind the door."""
    app = FastAPI()

    @app.post(PROBE)
    def _probe(caller=Depends(require_agent_caller)):
        user, key = caller
        return {"email": user.email, "key_id": key.id}

    factory = get_session_factory()

    def _db():
        db = factory()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c


def test_no_authorization_header_is_401(probe):
    r = probe.post(PROBE)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_a_session_cookie_is_401(probe, a_user):
    probe.cookies.set(SESSION_COOKIE, _sign(a_user.id, get_settings().auth_token_secret))
    assert probe.post(PROBE).status_code == 401


def test_a_guest_cookie_is_401(probe):
    probe.cookies.set(GUEST_COOKIE, "whatever")
    assert probe.post(PROBE).status_code == 401


def test_a_talent_token_is_401(probe, db_session):
    _, raw = mint_talent_key(
        db_session, label="Acme", contact="a@b.example", created_by="ops@example.com"
    )
    db_session.commit()
    assert probe.post(PROBE, headers=_bearer(raw)).status_code == 401


def test_every_refusal_says_the_same_thing(probe, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    key, dead_raw = mint_agent_key(db_session, user=a_user, label="dead")
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    responses = [
        probe.post(PROBE),
        probe.post(PROBE, headers=_bearer("ofa_invented")),
        probe.post(PROBE, headers=_bearer(dead_raw)),
        probe.post(PROBE, headers={"Authorization": raw}),
    ]
    assert {r.status_code for r in responses} == {401}
    assert {r.json()["detail"] for r in responses} == {"Credential required"}
    assert {r.headers["www-authenticate"] for r in responses} == {"Bearer"}


def test_a_live_token_resolves_its_own_user(probe, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    r = probe.post(PROBE, headers=_bearer(raw))
    assert r.status_code == 200
    assert r.json()["email"] == a_user.email


def test_revoking_between_two_calls_closes_the_door(probe, db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    assert probe.post(PROBE, headers=_bearer(raw)).status_code == 200
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert probe.post(PROBE, headers=_bearer(raw)).status_code == 401


def test_a_key_whose_user_vanished_is_the_same_401(probe, db_session, a_user, monkeypatch):
    """The guard is defensive: users.id cascades to the key row (and SQLite
    runs with foreign_keys=ON), so a live key with no user is nearly
    unreachable. Deleting the user therefore removes the key and would only
    hit the unknown-token path. Force the lookup instead, so the `user is
    None` branch itself is what answers, on any backend."""
    from sqlalchemy.orm import Session

    from app.models import User

    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    real_get = Session.get

    def get(self, entity, ident, *args, **kwargs):
        if entity is User:
            return None
        return real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(Session, "get", get)
    r = probe.post(PROBE, headers=_bearer(raw))
    assert r.status_code == 401
    assert r.json()["detail"] == "Credential required"
    assert r.headers["www-authenticate"] == "Bearer"


# ---------------------------------------------------------------------------
# The endpoint itself (Task 4). These run against the real app.
# ---------------------------------------------------------------------------

MCP = "/mcp"


def _rpc(client, raw, body):
    return client.post(MCP, headers=_bearer(raw), json=body)


def _call(client, raw, name, arguments=None, id=1):
    return _rpc(
        client,
        raw,
        {
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )


@pytest.fixture
def raw_key(db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    return raw


def test_initialize_returns_the_protocol_version_and_server_info(client, raw_key):
    from app.services.mcp_tools import PROTOCOL_VERSION

    r = _rpc(client, raw_key, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in r.headers
    body = r.json()
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert body["result"]["serverInfo"]["name"]
    assert body["result"]["serverInfo"]["version"]
    assert "tools" in body["result"]["capabilities"]


def test_tools_list_comes_from_the_registry(client, raw_key):
    from app.services.mcp_tools import specs

    r = _rpc(client, raw_key, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = r.json()["result"]["tools"]
    assert {t["name"] for t in tools} == {spec["name"] for spec in specs()}
    for t in tools:
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"
        assert "parameters" not in t


def test_a_tool_call_runs_the_handler(client, db_session, a_user, raw_key):
    from app.models import Resume

    db_session.add(
        Resume(title="Mine", locale="zh-TW", source="create", typst_source="= Mine",
               user_id=a_user.id)
    )
    db_session.commit()
    r = _call(client, raw_key, "list_resumes", id=3)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert "Mine" in json.dumps(result)


def test_list_resumes_never_shows_another_users_or_a_guests(
    client, db_session, a_user, other_user, raw_key
):
    from tests.conftest import _resume_for

    _resume_for(db_session, other_user, title="Not yours")
    r = _call(client, raw_key, "list_resumes")
    assert "Not yours" not in json.dumps(r.json())


def test_an_unknown_tool_is_a_jsonrpc_error_and_is_audited(client, db_session, raw_key):
    r = _call(client, raw_key, "drop_database", id=4)
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == -32602
    assert "result" not in body
    assert [(x.tool, x.outcome) for x in _audit_rows(db_session)] == [
        ("drop_database", "refused")
    ]


def test_an_overlong_tool_name_does_not_break_the_audit_write(client, db_session, raw_key):
    r = _call(client, raw_key, "t" * 500)
    assert r.json()["error"]["code"] == -32602
    rows = _audit_rows(db_session)
    assert [(x.tool, x.outcome) for x in rows] == [("t" * 64, "refused")]


def test_an_allowlisted_origin_passes(client, raw_key):
    for origin in ("https://claude.ai", "https://offerfy.cc"):
        r = client.post(MCP, headers={**_bearer(raw_key), "Origin": origin},
                        json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 200, origin


def test_an_absent_origin_passes(client, raw_key):
    r = _rpc(client, raw_key, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 200


def test_an_unlisted_origin_is_403_and_logged(client, raw_key, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="app.routers.mcp"):
        r = client.post(MCP, headers={**_bearer(raw_key), "Origin": "https://evil.example"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 403
    assert "https://evil.example" in caplog.text


def test_a_malformed_body_is_a_jsonrpc_error(client, raw_key):
    for body in (
        {"jsonrpc": "2.0", "id": 5},
        {"jsonrpc": "2.0", "id": 6, "method": "nope"},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": []},
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"arguments": {}}},
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": "list_resumes", "arguments": []}},
        [{"jsonrpc": "2.0", "id": 10, "method": "ping"}],
    ):
        r = _rpc(client, raw_key, body)
        assert r.status_code == 200, body
        assert "error" in r.json(), body
        assert "result" not in r.json(), body


def test_unparseable_json_is_a_parse_error(client, raw_key):
    r = client.post(MCP, headers={**_bearer(raw_key), "Content-Type": "application/json"},
                    content=b"{nope")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32700


def test_a_notification_is_accepted_with_no_body(client, raw_key):
    r = _rpc(client, raw_key, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    assert r.content == b""


def test_ping_answers_empty(client, raw_key):
    r = _rpc(client, raw_key, {"jsonrpc": "2.0", "id": 11, "method": "ping"})
    assert r.json() == {"jsonrpc": "2.0", "id": 11, "result": {}}


def test_the_endpoint_needs_a_token_and_has_no_get(client, raw_key):
    assert client.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert client.get(MCP, headers=_bearer(raw_key)).status_code == 405


def test_the_endpoint_is_not_in_the_openapi_document():
    from app.main import app

    assert "/mcp" not in app.openapi()["paths"]


def _audit_rows(db_session):
    from app.models import AgentToolCall

    db_session.expire_all()
    return db_session.query(AgentToolCall).order_by(AgentToolCall.created_at).all()


def test_a_valid_resume_id_reaches_the_audit_row(client, db_session, my_resume, raw_key):
    _call(client, raw_key, "list_resumes", {"resume_id": my_resume.id})
    rows = _audit_rows(db_session)
    assert [(r.tool, r.resume_id, r.outcome) for r in rows] == [
        ("list_resumes", my_resume.id, "ok")
    ]


@pytest.mark.parametrize("bad", ["x" * 37, 12345, ["a"], {"a": 1}, True])
def test_a_bad_resume_id_records_none_and_does_not_crash(client, db_session, raw_key, bad):
    r = _call(client, raw_key, "list_resumes", {"resume_id": bad})
    assert r.status_code == 200
    assert "error" not in r.json()
    rows = _audit_rows(db_session)
    assert [(r.resume_id, r.outcome) for r in rows] == [(None, "ok")]


def test_a_refused_call_is_recorded_with_its_resume_id(client, db_session, raw_key):
    r = _call(client, raw_key, "drop_database", {"resume_id": "abc"})
    assert r.json()["error"]["code"] == -32602
    rows = _audit_rows(db_session)
    assert [(r.tool, r.resume_id, r.outcome) for r in rows] == [("drop_database", "abc", "refused")]


def test_a_tools_http_exception_is_a_readable_tool_error(client, raw_key, monkeypatch):
    from fastapi import HTTPException

    from app.services import mcp_tools

    def boom(db, user, args):
        raise HTTPException(409, "The document changed, reload it")

    monkeypatch.setitem(
        mcp_tools.REGISTRY, "boom",
        mcp_tools.Tool(spec={"name": "boom", "description": "d",
                             "parameters": {"type": "object", "properties": {}}},
                       handler=boom),
    )
    r = _call(client, raw_key, "boom")
    result = r.json()["result"]
    assert result["isError"] is True
    assert "reload it" in result["content"][0]["text"]


def test_an_async_handler_is_awaited(client, raw_key, monkeypatch):
    from app.services import mcp_tools

    async def aio(db, user, args):
        return {"hello": "async"}

    monkeypatch.setitem(
        mcp_tools.REGISTRY, "aio",
        mcp_tools.Tool(spec={"name": "aio", "description": "d",
                             "parameters": {"type": "object", "properties": {}}},
                       handler=aio),
    )
    r = _call(client, raw_key, "aio")
    assert "async" in json.dumps(r.json()["result"])


def test_a_throttled_call_is_a_tool_error_with_the_reason(client, raw_key, monkeypatch):
    from app.services import mcp_audit

    monkeypatch.setattr(mcp_audit, "QUOTA_PER_HOUR", 0)
    r = _call(client, raw_key, "list_resumes")
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True
    assert "Too many calls" in json.dumps(r.json())


def test_an_unsupported_protocol_version_header_is_400(client, raw_key):
    from app.services.mcp_tools import PROTOCOL_VERSION

    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    bad = client.post(MCP, headers={**_bearer(raw_key), "MCP-Protocol-Version": "2026-07-28"},
                      json=body)
    assert bad.status_code == 400
    assert PROTOCOL_VERSION in bad.json()["error"]["data"]["supported"]
    ok = client.post(MCP, headers={**_bearer(raw_key), "MCP-Protocol-Version": PROTOCOL_VERSION},
                     json=body)
    assert ok.status_code == 200
