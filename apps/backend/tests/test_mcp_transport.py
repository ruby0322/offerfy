"""The agent surface's door, and (from Task 4) the transport behind it.

The auth cases run against a standalone probe app, not app.main.app: a route
registered on the shared module-level app outlives the test that added it and
pollutes every later one. The `client` fixture is used only to stand up the
test database.
"""

import json
import sys
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

    from app.models import GuestSession, Resume

    _resume_for(db_session, other_user, title="Not yours")
    guest = GuestSession(key_hash="g" * 64)
    db_session.add(guest)
    db_session.commit()
    db_session.add(
        Resume(title="A guest's", locale="zh-TW", source="create", typst_source="= G",
               guest_session_id=guest.id)
    )
    db_session.commit()
    _resume_for(db_session, a_user, title="Mine")
    r = _call(client, raw_key, "list_resumes")
    text = json.dumps(r.json())
    assert "Mine" in text
    assert "Not yours" not in text
    assert "A guest's" not in text


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


_VKEY = "io.modelcontextprotocol/protocolVersion"


def _versioned_ping(client, raw_key, *, header, meta):
    return client.post(
        MCP,
        headers={**_bearer(raw_key), "MCP-Protocol-Version": header},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping",
              "params": {"_meta": {_VKEY: meta}}},
    )


def test_an_unknown_protocol_version_is_the_modern_unsupported_error(client, raw_key):
    """Header and _meta agree, so only the unknown value can be what trips."""
    from app.services.mcp_protocol import SUPPORTED

    r = _versioned_ping(client, raw_key, header="1999-01-01", meta="1999-01-01")
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == -32022
    assert err["data"]["supported"] == list(SUPPORTED)
    assert err["data"]["requested"] == "1999-01-01"


def test_a_header_disagreeing_with_the_body_version_is_a_header_mismatch(
    client, raw_key
):
    """Both values are supported, so only the disagreement can be what trips."""
    from app.services.mcp_protocol import LEGACY_VERSION, MODERN_VERSION

    r = _versioned_ping(client, raw_key, header=MODERN_VERSION, meta=LEGACY_VERSION)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == -32020
    assert "HeaderMismatch" in err["message"]
    # data.supported is specified only for -32022 (versioning page).
    assert "data" not in err
    ok = _versioned_ping(client, raw_key, header=LEGACY_VERSION, meta=LEGACY_VERSION)
    assert ok.status_code == 200


def _register(monkeypatch, name, handler):
    from app.services import mcp_tools

    monkeypatch.setitem(
        mcp_tools.REGISTRY, name,
        mcp_tools.Tool(spec={"name": name, "description": "d",
                             "parameters": {"type": "object", "properties": {}}},
                       handler=handler),
    )


def _titles(db_session):
    from app.models import Resume

    db_session.expire_all()
    return {r.title for r in db_session.query(Resume).all()}


def _flush_then(exc):
    def handler(db, user, args):
        from app.models import Resume

        db.add(Resume(title="Half edit", locale="zh-TW", source="create",
                      typst_source="= H", user_id=user.id))
        db.flush()
        raise exc

    return handler


def test_a_refused_tools_flushed_writes_are_rolled_back(
    client, db_session, raw_key, monkeypatch
):
    from fastapi import HTTPException

    _register(monkeypatch, "half", _flush_then(HTTPException(409, "document changed")))
    r = _call(client, raw_key, "half")
    assert r.json()["result"]["isError"] is True
    assert "Half edit" not in _titles(db_session)
    assert [x.outcome for x in _audit_rows(db_session)] == ["refused"]


def test_a_crashing_tool_is_a_tool_error_rolled_back_and_audited(
    client, db_session, raw_key, monkeypatch
):
    _register(monkeypatch, "crash", _flush_then(RuntimeError("secret internal detail")))
    r = _call(client, raw_key, "crash")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert "secret internal detail" not in json.dumps(body)
    assert "Half edit" not in _titles(db_session)
    rows = _audit_rows(db_session)
    assert [(x.tool, x.outcome) for x in rows] == [("crash", "error")]


def test_an_audit_layer_failure_is_a_jsonrpc_internal_error(client, raw_key, monkeypatch):
    from app.services import mcp_audit

    def broken(*a, **k):
        raise RuntimeError("audit db down")

    monkeypatch.setattr(mcp_audit, "_stamp_last_used", broken)
    r = _call(client, raw_key, "list_resumes")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32603


def _record_that_fails_on(outcome: str, monkeypatch):
    """Make the audit write fail for one outcome and work for the others."""
    from app.services import mcp_audit

    real = mcp_audit.record

    def record(key_id, user_id, tool, resume_id, written):
        if written == outcome:
            raise RuntimeError("audit db down")
        return real(key_id, user_id, tool, resume_id, written)

    monkeypatch.setattr(mcp_audit, "record", record)


def test_a_lost_ok_row_does_not_report_an_error_for_a_write_that_landed(
    client, db_session, raw_key, monkeypatch
):
    """`create_resume` commits, so once the handler returns the resume exists.
    Answering -32603 because the audit row could not be written tells the agent
    the opposite, and an agent that reads an error retries -- which is how one
    create becomes two. The audit gap goes to the application log instead."""
    from app.models import Resume

    _record_that_fails_on("ok", monkeypatch)
    r = _call(client, raw_key, "create_resume", {"locale": "en"})
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body, body
    assert body["result"]["isError"] is False
    made = body["result"]["structuredContent"]["resume_id"]
    db_session.expire_all()
    assert db_session.get(Resume, made) is not None
    # The row really is missing -- the test would pass vacuously otherwise.
    assert _audit_rows(db_session) == []


def test_a_lost_refusal_row_is_still_an_internal_error(
    client, db_session, raw_key, monkeypatch
):
    """The other half of the asymmetry: on a refusal nothing landed, so an
    error is the truth and the audit failure must still propagate."""
    _record_that_fails_on("refused", monkeypatch)
    r = _call(client, raw_key, "get_resume", {"resume_id": "no-such-resume"})
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32603


def test_a_lost_error_row_is_still_an_internal_error(client, raw_key, monkeypatch):
    _record_that_fails_on("error", monkeypatch)
    _register(monkeypatch, "crash2", _flush_then(RuntimeError("boom")))
    r = _call(client, raw_key, "crash2")
    assert r.json()["error"]["code"] == -32603


def test_an_internal_error_survives_a_rollback_that_fails(client, raw_key, monkeypatch):
    """The generic branch exists for pool and database failures, and on exactly
    those its own `db.rollback()` is what raises. Unguarded, it turned the
    -32603 the branch promises into an HTTP 500."""
    from sqlalchemy.orm import Session

    from app.services import mcp_audit

    def broken(*a, **k):
        raise RuntimeError("audit db down")

    def no_rollback(self, *a, **k):
        raise RuntimeError("the connection is gone")

    monkeypatch.setattr(mcp_audit, "_stamp_last_used", broken)
    monkeypatch.setattr(Session, "rollback", no_rollback)
    r = _call(client, raw_key, "list_resumes")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32603


def test_deeply_nested_json_is_a_parse_error_not_a_500(client, raw_key):
    """Python's JSON parser raises RecursionError, not ValueError, past the
    recursion limit, so this reached the endpoint as an unhandled exception."""
    # Well past where the C scanner gives up: it has headroom of its own, so a
    # few thousand levels still parse (and would answer "Invalid request").
    nesting = sys.getrecursionlimit() * 20
    content = b"[" * nesting + b"]" * nesting
    r = client.post(
        MCP,
        headers={**_bearer(raw_key), "Content-Type": "application/json"},
        content=content,
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32700


def _checkout_watch(request) -> dict:
    """Counts connections checked out of the pool, and the peak."""
    from sqlalchemy import event

    from app.db import get_engine

    engine = get_engine()
    state = {"open": 0, "peak": 0}

    def out(*_a):
        state["open"] += 1
        state["peak"] = max(state["peak"], state["open"])

    def back(*_a):
        state["open"] -= 1

    event.listen(engine, "checkout", out)
    event.listen(engine, "checkin", back)
    request.addfinalizer(lambda: event.remove(engine, "checkout", out))
    request.addfinalizer(lambda: event.remove(engine, "checkin", back))
    return state


@pytest.mark.parametrize(
    "tool,args",
    [
        ("list_resumes", {}),
        ("get_resume", {"resume_id": "@resume"}),
        ("create_resume", {"locale": "en"}),
        ("set_resume_title", {"resume_id": "@resume", "title": "Renamed"}),
        ("publish_resume", {"resume_id": "@resume"}),
    ],
)
def test_a_tools_call_never_holds_two_pool_connections(
    client, db_session, my_resume, raw_key, request, tool, args
):
    """app/concurrency.py caps in-flight requests at 32 against a 40-slot pool
    *on the assumption that a request holds one connection*. This endpoint used
    to hold the request session's connection across `check_quota`,
    `_stamp_last_used` and `record`, each of which opens its own -- so around
    ten concurrent /mcp calls with the gate otherwise full exceeded the pool,
    and pool_timeout=3 turned that into 500s for the whole worker.
    """
    args = {k: (my_resume.id if v == "@resume" else v) for k, v in args.items()}
    # Release whatever the fixtures left open on the test's own session, so the
    # only connections counted below are the request's.
    db_session.commit()
    state = _checkout_watch(request)
    r = _call(client, raw_key, tool, args)
    assert "error" not in r.json(), r.text
    assert r.json()["result"]["isError"] is False, r.text
    assert state["peak"] == 1, f"{tool} peaked at {state['peak']} connections"
