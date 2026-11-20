"""POST /mcp: a signed-in user's own AI assistant, over MCP Streamable HTTP.

**Built against MCP revision 2025-11-25** (services/mcp_tools.PROTOCOL_VERSION),
read on 2026-11-20 from the published specification:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
  https://modelcontextprotocol.io/specification/2026-07-28/server/tools
The tools page is the 2026-07-28 text, which is the one the site serves as
"latest"; the tools/list and tools/call shapes used here (`name`,
`description`, `inputSchema`; `content` blocks and `isError`) are the same in
both revisions.

Why 2025-11-25 and not 2026-07-28: the newer revision is stateless (no
`initialize`, a `server/discover` method, per-request `_meta`, `resultType` on
every result). It was published after the clients this is meant for were built,
and says a modern-only server cannot be reached by a client that opens with
`initialize`. A client that tries the modern form first gets this server's 400
on the unsupported `MCP-Protocol-Version` header, which the newer revision
defines as the signal to fall back to `initialize`. Supporting the newer
revision as well is additive: one more method and one more header value.

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
from app.services import mcp_tools
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


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


def _error(id_: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}
    )


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
            payload = mcp_tools.call(db, user, name, args)
    except mcp_tools.UnknownTool as exc:
        # Audited as `refused` above; rendered as a protocol error.
        raise _RpcError(INVALID_PARAMS, str(exc.detail))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail)
        return _text_result(detail, is_error=True)
    return _text_result(payload, is_error=False, structured=True)


def _dispatch(
    db: Session, user: User, key: AgentApiKey, id_: Any, method: str, params: Any
) -> JSONResponse:
    """Runs on a worker thread: every database call in here blocks."""
    try:
        if method == "initialize":
            return _result(id_, _initialize())
        if method == "ping":
            return _result(id_, {})
        if method == "tools/list":
            return _result(id_, _tools_list())
        if method == "tools/call":
            return _result(id_, _tools_call(db, user, key, params))
        return _error(id_, METHOD_NOT_FOUND, f"Method not found: {method[:64]}")
    except _RpcError as exc:
        return _error(id_, exc.code, exc.message)
    except Exception:
        # audited has already recorded it as `error`. Say nothing of the cause.
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

    # Required by the transport specification: an unsupported version header
    # is a 400. A modern-only client probing this server relies on it to fall
    # back to `initialize`. Absent header is accepted.
    version = request.headers.get("mcp-protocol-version")
    if version is not None and version != mcp_tools.PROTOCOL_VERSION:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": INVALID_REQUEST,
                    "message": "Unsupported protocol version",
                    "data": {
                        "supported": [mcp_tools.PROTOCOL_VERSION],
                        "requested": version[:32],
                    },
                },
            },
            status_code=400,
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
    if not has_id:
        return Response(status_code=202)  # a notification: accepted, no reply

    return await run_in_threadpool(
        _dispatch, db, user, key, id_, method, body.get("params")
    )
