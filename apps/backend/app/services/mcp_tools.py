"""The tool registry behind POST /mcp.

A tool is a provider-neutral spec (`{name, description, parameters}`, the same
shape the chat tool-calling code uses, so one definition can feed any model
provider) plus a handler `(db, user, args) -> dict`. The transport translates
`parameters` to MCP's `inputSchema` on the way out; nothing here knows about
JSON-RPC.

A handler may be `def` or `async def` and may commit mid-call. `call` is
synchronous because the transport runs it on a worker thread (the audit and
quota writes are blocking database calls); a coroutine handler is driven to
completion with `asyncio.run` on that same thread, so the Session is never
touched from two threads.

**No handler may sleep, poll or retry.** The call holds an InFlightGate slot
(MAX_IN_FLIGHT = 32 per worker) from the first byte to the last; a tool that
waits starves every other request on the worker. Do the work once, or refuse.

Tool errors are raised as HTTPException. The transport turns that into an MCP
tool result with `isError: true` and the detail as text, which the agent can
read and act on ("the document changed, reload it").
"""

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Resume, User

# The MCP revision this surface was written against: the 2025-11-25 revision,
# which opens with an `initialize` handshake. See app/routers/mcp.py for the
# sources read and why the newer stateless revision is not what is built.
PROTOCOL_VERSION = "2025-11-25"

SERVER_INFO = {"name": "offerfy", "title": "Offerfy", "version": "1.0.0"}


class UnknownTool(HTTPException):
    """No such tool. An HTTPException so `audited` records it as refused; the
    transport renders it as a JSON-RPC protocol error rather than a tool error."""


@dataclass(frozen=True)
class Tool:
    spec: dict
    handler: Callable[..., Any]


def _list_resumes(db: Session, user: User, args: dict) -> dict:
    # PROVISIONAL: Task 6 replaces this with the resume_access service, which
    # owns "which resumes may this caller see". Until then it is a plain query
    # on the caller's own rows. It never falls back to guest_session_id: an
    # agent key belongs to a signed-in user and has no guest identity.
    rows = (
        db.query(Resume)
        .filter(Resume.user_id == user.id)
        .order_by(Resume.updated_at.desc(), Resume.id)
        .all()
    )
    return {
        "resumes": [
            {
                "id": r.id,
                "title": r.title,
                "kind": r.kind,
                "locale": r.locale,
                "parent_resume_id": r.parent_resume_id,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    }


REGISTRY: dict[str, Tool] = {
    "list_resumes": Tool(
        spec={
            "name": "list_resumes",
            "description": "List the signed-in user's resumes: id, title, kind "
            "(master or tailor), locale and last update.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        handler=_list_resumes,
    ),
}


def specs() -> list[dict]:
    return [tool.spec for tool in REGISTRY.values()]


def call(db: Session, user: User, name: str, args: dict) -> dict:
    tool = REGISTRY.get(name)
    if tool is None:
        raise UnknownTool(status_code=400, detail=f"Unknown tool: {name[:64]}")
    if inspect.iscoroutinefunction(tool.handler):
        return asyncio.run(tool.handler(db, user, args))
    return tool.handler(db, user, args)
