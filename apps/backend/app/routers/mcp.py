"""POST /mcp: a signed-in user's own AI assistant, over MCP Streamable HTTP.

**Serves both protocol eras off this one endpoint**: the legacy revision
2025-11-25 (an `initialize` handshake) and the modern 2026-07-28 (stateless:
no handshake, `server/discover`, the version in every request's `_meta` and in
the `MCP-Protocol-Version` header). Version validation, era detection and the
`server/discover` payload live in app/services/mcp_protocol.py, which also
records why both are needed; dispatch is shared. Read on 2026-11-20 from:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
  https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
  https://modelcontextprotocol.io/specification/2026-07-28/server/tools
The tools/list and tools/call shapes (`name`, `description`, `inputSchema`;
`content` blocks and `isError`) are the same in both; a modern result
additionally carries `resultType: "complete"`.

Wire facts this module relies on:
  * initialize result: `protocolVersion`, `capabilities`, `serverInfo`
    ({name, version, optional title}). If the client asks for a version the
    server lacks, the server answers with the one it has.
  * tools/list result: `tools`, each `{name, description, inputSchema}`.
  * tools/call params `{name, arguments}`; result `{content: [{type: "text",
    text}], isError}` (and optional `structuredContent`).
  * A JSON-RPC notification (no `id`) or response is answered `202 Accepted`
    with no body.
  * Errors: -32700 parse, -32600 invalid request, -32601 method not found,
    -32602 invalid params, -32603 internal.
  * Two kinds of error. A tool that runs and fails is a tool *result* with
    `isError: true`, so the model can read it and adapt. A malformed envelope
    or an unknown tool name is a JSON-RPC `error` (-32602 for the name, which
    is a parameter of a method that exists). Both ride HTTP 200. An unknown
    name is still dispatched inside `audited`, so it is recorded as `refused`
    in the user's audit list even though it is rendered as a protocol error.

**Pool connections: this endpoint holds two, not one.** `get_db` keeps the
request session (one connection) for the request's whole life, and `audited`
opens a second, short-lived session each time it runs `check_quota`,
`_stamp_last_used` or `record` (see app/services/mcp_audit.py). app/concurrency.py
sizes MAX_IN_FLIGHT = 32 against a 40-slot pool on the assumption that a
request holds one connection; this is an exception to it. The audit sessions
are brief, so the overlap is small and no number was changed. Look here first
in a pool incident that involves /mcp.

**Deliberately absent: SSE, `GET /mcp`, `MCP-Session-Id`, resumption.** The
specification lets a server answer a POST with one `application/json` body and
answer GET with 405, and this server pushes nothing: every reply is the
response to the request that asked. A stream would also hold a request's
database session for the stream's whole life, pinning a pool slot per open
stream, the bug app/deps.py::resolve_user's docstring describes and that chat
and the snapshot streams were just fixed for. A session id would add server
state for nothing, since the bearer token already identifies the caller on
every request.

**Origin.** The credential is a bearer token in the Authorization header,
never an ambient cookie, so the DNS-rebinding threat the specification has in
mind has nothing to ride here. The MUST is honoured regardless: a present
`Origin` must be in ALLOWED_ORIGINS or the answer is 403. An absent `Origin`
is allowed, which is every desktop, CLI and server-side connector client. A
rejected value is logged at warning level so an unanticipated client is one
grep away.

include_in_schema=False on the router: the Next rewrite publishes
/api/openapi.json, and a route in that document is a route described on the
open web. MCP has tools/list for discovery.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_agent_caller
from app.models import AgentApiKey, User
from app.services import mcp_protocol, mcp_tools
from app.services.mcp_audit import audited

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="",
    include_in_schema=False,
    dependencies=[Depends(require_agent_caller)],
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Browser origins allowed to POST here: the hosted assistant and the site
# itself. Deliberately a constant, not an env var, so it stays greppable.
ALLOWED_ORIGINS = frozenset({"https://claude.ai", "https://offerfy.cc"})

_RESUME_ID_MAX = 36


class _ToolCrash(Exception):
    """A handler raised something that is not an HTTPException."""


class _RpcError(Exception):
    def __init__(
        self, code: int, message: str, data: dict | None = None, status: int = 200
    ):
        self.code = code
        self.message = message
        self.data = data
        self.status = status


def _error(
    id_: Any, code: int, message: str, data: dict | None = None, status: int = 200
) -> JSONResponse:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": err}, status_code=status)


def _result(id_: Any, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _text_result(payload: Any, *, is_error: bool, structured: bool = False) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    out: dict = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if structured and isinstance(payload, dict):
        out["structuredContent"] = payload
    return out


def _valid_id(value: Any) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool)


def _tools_list() -> dict:
    return {
        "tools": [
            {
                "name": s["name"],
                "description": s["description"],
                "inputSchema": s["parameters"],
            }
            for s in mcp_tools.specs()
        ]
    }


def _initialize() -> dict:
    return {
        "protocolVersion": mcp_tools.PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": mcp_tools.SERVER_INFO,
    }


def _tools_call(db: Session, user: User, key: AgentApiKey, params: Any) -> dict:
    if not isinstance(params, dict):
        raise _RpcError(INVALID_PARAMS, "params must be an object")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _RpcError(INVALID_PARAMS, "params.name must be a tool name")
    args = params.get("arguments", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise _RpcError(INVALID_PARAMS, "params.arguments must be an object")

    try:
        with audited(key, name) as call:
            # Validated here, where the raw argument is read: a string of at
            # most 36 characters, or None. mcp_audit.record coerces again as a
            # backstop, but this is the check. Set before dispatch so a
            # refusal still records which resume it was about.
            rid = args.get("resume_id")
            call.resume_id = rid if isinstance(rid, str) and len(rid) <= _RESUME_ID_MAX else None
            try:
                payload = mcp_tools.call(db, user, name, args)
            except Exception as exc:
                # Roll back BEFORE `audited` records the outcome (it does so
                # as this exception leaves the block). get_db commits when no
                # exception reaches it, and this one is about to be turned
                # into a 200, so without this a failed tool's flushed writes
                # would be committed, or a failed flush would break teardown.
                db.rollback()
                if isinstance(exc, HTTPException):
                    raise
                logger.exception("mcp tool %r crashed", name[:64])
                raise _ToolCrash() from exc
    except _ToolCrash:
        # A handler that ran and blew up: a tool error with generic text, the
        # real cause logged above. `audited` has recorded it as `error`.
        return _text_result("The tool failed unexpectedly.", is_error=True)
    except mcp_tools.UnknownTool as exc:
        # Audited as `refused` above; rendered as a protocol error.
        raise _RpcError(INVALID_PARAMS, str(exc.detail))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
        return _text_result(detail, is_error=True)
    return _text_result(payload, is_error=False, structured=True)


def _route(
    db: Session, user: User, key: AgentApiKey, modern: bool, method: str, params: Any
) -> dict:
    """One dispatch for both eras. The era only decides which lifecycle
    method exists (`initialize` legacy, `server/discover` modern), that a
    modern result carries `resultType`, and that tools/list carries cache
    hints. The registry and the `audited` wrapper are shared."""
    if method == "initialize" and not modern:
        return _initialize()
    if method == "server/discover" and modern:
        return mcp_protocol.discover_result()
    if method == "ping":
        return {}
    if method == "tools/list":
        out = _tools_list()
        if modern:
            # Same registry, same reasoning as server/discover's TTL.
            out["ttlMs"] = mcp_protocol.DISCOVER_TTL_MS
            out["cacheScope"] = mcp_protocol.CACHE_SCOPE
        return out
    if method == "tools/call":
        return _tools_call(db, user, key, params)
    status = 404 if modern else 200  # the spec's 404 is a modern-era rule
    if method in ("initialize", "server/discover"):
        # Real, but belongs to the other era. Name the versions we serve (the
        # spec asks that of an error answering `initialize`).
        raise _RpcError(
            METHOD_NOT_FOUND,
            f"Method not available in this protocol era: {method}",
            {"supported": list(mcp_protocol.SUPPORTED)},
            status,
        )
    raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method[:64]}", None, status)


def _dispatch(
    db: Session,
    user: User,
    key: AgentApiKey,
    id_: Any,
    method: str,
    params: Any,
    modern: bool,
) -> JSONResponse:
    """Runs on a worker thread: every database call in here blocks."""
    try:
        result = _route(db, user, key, modern, method, params)
        if modern:
            result.setdefault("resultType", "complete")
        return _result(id_, result)
    except _RpcError as exc:
        return _error(id_, exc.code, exc.message, exc.data, exc.status)
    except Exception:
        # The transport or audit layer failed (a handler crash is caught in
        # _tools_call). Nothing of the cause goes to the caller.
        db.rollback()
        logger.exception("mcp %s failed", method)
        return _error(id_, INTERNAL_ERROR, "Internal error")


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    caller: tuple[User, AgentApiKey] = Depends(require_agent_caller),
    db: Session = Depends(get_db),
):
    user, key = caller

    origin = request.headers.get("origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        logger.warning("mcp: rejected Origin %r", origin[:200])
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": INVALID_REQUEST, "message": "Origin not allowed"},
            },
            status_code=403,
        )

    try:
        body = await request.json()
    except ValueError:
        return _error(None, PARSE_ERROR, "Parse error")

    # One message per POST (batching was removed from the protocol).
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Invalid request")

    method = body.get("method")
    if method is None and ("result" in body or "error" in body):
        return Response(status_code=202)  # a response to nothing we sent
    has_id = "id" in body
    id_ = body.get("id")
    if has_id and not _valid_id(id_):
        return _error(None, INVALID_REQUEST, "Invalid request id")
    if not isinstance(method, str) or not method:
        return _error(id_ if has_id else None, INVALID_REQUEST, "Invalid request")

    # Version and header validation before any dispatch; this one check
    # replaces Task 4's "header must equal 2025-11-25". It runs after the body
    # parses on purpose: the header is compared with the body's _meta, so an
    # unparseable body is a Parse error, and a notification with a bad version
    # is a 400 (the rejection is mandatory and HTTP must answer something).
    try:
        mcp_protocol.check_version(request, body)
    except mcp_protocol.ProtocolError as exc:
        return _error(
            id_ if has_id else None, exc.code, exc.message, exc.data, exc.status
        )
    modern = mcp_protocol.era_of(request, body) == "modern"

    if not has_id:
        return Response(status_code=202)  # a notification: accepted, no reply

    return await run_in_threadpool(
        _dispatch, db, user, key, id_, method, body.get("params"), modern
    )
