"""Both MCP eras on the one endpoint (Task 4b).

Modern (2026-07-28): no handshake, version in every request. Legacy
(2025-11-25 and earlier): `initialize`. One registry and one audit wrapper
serve both; these tests pin the era-specific edges.
"""

import json

import pytest

from app.services.agent_keys import mint_agent_key
from app.services.mcp_protocol import (
    LEGACY_VERSION,
    MODERN_VERSION,
    SUPPORTED,
    check_version,
    discover_result,
    era_of,
)
from app.services.mcp_tools import specs

MCP = "/mcp"
VKEY = "io.modelcontextprotocol/protocolVersion"


@pytest.fixture
def raw_key(db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    return raw


def _raw_rpc(
    client, raw, method, *, header_version, body_version, params=None, extra=None
):
    params = dict(params or {})
    if body_version is not None:
        params["_meta"] = {VKEY: body_version}
    headers = {"Authorization": f"Bearer {raw}"}
    if header_version is not None:
        headers["MCP-Protocol-Version"] = header_version
    headers.update(extra or {})
    return client.post(
        MCP,
        headers=headers,
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
    )


def _rpc(client, raw, method, version, params=None, extra=None):
    """One request carrying `version` in both the header and the body's _meta
    (neither when `version` is None)."""
    return _raw_rpc(client, raw, method, header_version=version, body_version=version,
                    params=params, extra=extra)


def test_server_discover_names_both_supported_versions(client, raw_key):
    r = _rpc(client, raw_key, "server/discover", MODERN_VERSION)
    assert r.status_code == 200
    out = r.json()
    assert out["id"] == 7
    assert out["result"]["resultType"] == "complete"
    assert out["result"]["supportedVersions"] == list(SUPPORTED)
    assert out["result"]["capabilities"]["tools"] is not None
    assert out["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]
    assert out["result"]["ttlMs"] >= 0
    assert out["result"]["cacheScope"] in ("public", "private")


def test_a_modern_tools_list_works_without_any_handshake(client, raw_key):
    out = _rpc(client, raw_key, "tools/list", MODERN_VERSION).json()
    assert {t["name"] for t in out["result"]["tools"]} == {s["name"] for s in specs()}
    assert out["result"]["resultType"] == "complete"
    assert out["result"]["ttlMs"] >= 0
    assert out["result"]["cacheScope"] in ("public", "private")


def test_the_legacy_handshake_still_works(client, raw_key):
    r = client.post(
        MCP,
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert r.json()["result"]["protocolVersion"] == LEGACY_VERSION
    assert "resultType" not in r.json()["result"]


def test_a_legacy_tools_list_carries_no_modern_decoration(client, raw_key):
    out = _rpc(client, raw_key, "tools/list", LEGACY_VERSION).json()
    assert "tools" in out["result"]
    assert "resultType" not in out["result"]


def test_an_unsupported_version_is_a_recognized_modern_error(client, raw_key):
    r = _rpc(client, raw_key, "tools/list", "1999-01-01")
    assert r.status_code == 400
    out = r.json()
    assert out["id"] == 7
    assert out["error"]["code"] == -32022
    assert out["error"]["message"] == "Unsupported protocol version"
    assert out["error"]["data"]["supported"] == list(SUPPORTED)
    assert out["error"]["data"]["requested"] == "1999-01-01"


def test_an_unsupported_header_alone_is_the_same_modern_error(client, raw_key):
    r = _raw_rpc(
        client, raw_key, "tools/list", header_version="1999-01-01", body_version=None
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32022


def test_a_header_body_version_mismatch_is_rejected(client, raw_key):
    r = _raw_rpc(client, raw_key, "tools/list",
                 header_version=MODERN_VERSION, body_version=LEGACY_VERSION)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020
    assert "HeaderMismatch" in json.dumps(r.json())


def test_a_modern_body_without_the_header_is_rejected(client, raw_key):
    r = _raw_rpc(
        client, raw_key, "tools/list", header_version=None, body_version=MODERN_VERSION
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


def test_a_modern_header_without_a_body_version_is_rejected(client, raw_key):
    r = _raw_rpc(
        client, raw_key, "tools/list", header_version=MODERN_VERSION, body_version=None
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


def test_a_disagreeing_mcp_method_header_is_a_mismatch(client, raw_key):
    wrong = {"Mcp-Method": "tools/call"}
    r = _rpc(client, raw_key, "tools/list", MODERN_VERSION, extra=wrong)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


def test_a_matching_or_absent_mcp_method_header_is_accepted(client, raw_key):
    right = {"Mcp-Method": "tools/list"}
    ok = _rpc(client, raw_key, "tools/list", MODERN_VERSION, extra=right)
    assert ok.status_code == 200
    assert _rpc(client, raw_key, "tools/list", MODERN_VERSION).status_code == 200


def test_initialize_is_legacy_only_and_names_the_supported_versions(client, raw_key):
    r = _rpc(client, raw_key, "initialize", MODERN_VERSION)
    assert r.status_code == 404
    out = r.json()
    assert out["error"]["code"] == -32601
    assert out["error"]["data"]["supported"] == list(SUPPORTED)


def test_server_discover_is_modern_only(client, raw_key):
    out = _rpc(client, raw_key, "server/discover", LEGACY_VERSION).json()
    assert out["error"]["code"] == -32601
    bare = client.post(
        MCP,
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
    )
    # No version anywhere: nothing was offered, so it is not an unsupported
    # version (-32022 with a `requested`), it is a missing required header.
    assert bare.status_code == 400
    err = bare.json()["error"]
    assert err["code"] == -32020
    assert "header is required" in err["message"]
    assert "data" not in err


def test_an_unknown_modern_method_is_404_with_a_jsonrpc_error(client, raw_key):
    r = _rpc(client, raw_key, "nope/nothing", MODERN_VERSION)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == -32601


def test_an_unknown_legacy_method_is_still_a_200_jsonrpc_error(client, raw_key):
    r = _rpc(client, raw_key, "nope/nothing", None)
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601
    r = _rpc(client, raw_key, "nope/nothing", LEGACY_VERSION)
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


def _audit(db_session):
    from app.models import AgentToolCall

    db_session.expire_all()
    return db_session.query(AgentToolCall).order_by(AgentToolCall.created_at).all()


def test_a_modern_tool_call_is_audited_like_a_legacy_one(client, db_session, raw_key):
    params = {"name": "list_resumes", "arguments": {}}
    modern = _rpc(client, raw_key, "tools/call", MODERN_VERSION, params=params).json()
    legacy = _rpc(client, raw_key, "tools/call", None, params=params).json()
    assert modern["result"]["isError"] is False
    assert modern["result"]["resultType"] == "complete"
    assert legacy["result"]["isError"] is False
    assert "resultType" not in legacy["result"]
    rows = _audit(db_session)
    assert [r.tool for r in rows] == ["list_resumes", "list_resumes"]
    assert {r.outcome for r in rows} == {rows[0].outcome}


def test_a_modern_unknown_tool_is_still_audited_as_refused(client, db_session, raw_key):
    out = _rpc(client, raw_key, "tools/call", MODERN_VERSION,
               params={"name": "drop_database", "arguments": {}}).json()
    assert out["error"]["code"] == -32602
    rows = _audit(db_session)
    assert [(r.tool, r.outcome) for r in rows] == [("drop_database", "refused")]


def test_era_of_unit():
    class R:
        def __init__(self, h):
            self.headers = {"mcp-protocol-version": h} if h else {}

    def body(method, v=None):
        return {"method": method, "params": {"_meta": {VKEY: v}} if v else {}}

    assert era_of(R(None), body("initialize")) == "legacy"
    assert era_of(R(None), body("tools/list")) == "legacy"
    assert era_of(R(LEGACY_VERSION), body("tools/list")) == "legacy"
    assert era_of(R(MODERN_VERSION), body("tools/list", MODERN_VERSION)) == "modern"
    assert era_of(R("1999-01-01"), body("tools/list", "1999-01-01")) == "modern"
    assert era_of(R(None), body("server/discover")) == "modern"
    check_version(R(None), body("tools/list"))  # no raise


def test_discover_result_is_static():
    assert discover_result() == discover_result()
