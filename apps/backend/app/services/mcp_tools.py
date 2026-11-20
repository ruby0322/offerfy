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
import json
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Job, JobScore, Resume, User
from app.services import jobs_tools

# The legacy MCP revision (2025-11-25, an `initialize` handshake), which the
# `initialize` result reports. The modern revision and the full supported set
# live in app/services/mcp_protocol.py.
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


def _catalogue_tool(shared: dict, handler_key: str, name: str) -> Tool:
    """A catalogue tool, borrowed whole from the chat surface.

    The spec is the shared one (only the name may differ, and the descriptions
    follow it), so there is exactly one definition of what a catalogue search
    accepts. The handler runs the shared handler and turns its `{"error": ...}`
    answer into a tool error, which is how this transport reports failure.
    """
    description = shared["description"].replace(
        jobs_tools.READ_JOB_TOOL, "get_job"
    )

    def handler(db: Session, user: User, args: dict) -> dict:
        payload = json.loads(jobs_tools.catalogue_handlers(db)[handler_key](args))
        if "error" in payload:
            missing = payload["error"].startswith("No posting")
            raise HTTPException(status_code=404 if missing else 400, detail=payload["error"])
        return payload

    return Tool(
        spec={**shared, "name": name, "description": description}, handler=handler
    )


def _list_matches(db: Session, user: User, args: dict) -> dict:
    """The caller's own scored postings, best first, straight from the cache.

    Reads `job_scores` and nothing else: no sweep, no scorer, no model call and
    never `digest.mark_opened`, whose stamp an agent must not be able to set.
    """
    try:
        limit = int(args.get("limit") or jobs_tools.SEARCH_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = jobs_tools.SEARCH_LIMIT_DEFAULT
    limit = max(1, min(jobs_tools.SEARCH_LIMIT_MAX, limit))
    rows = (
        db.query(JobScore, Job)
        .join(Job, Job.id == JobScore.job_id)
        .filter(JobScore.user_id == user.id, JobScore.outcome == "scored")
        .order_by(JobScore.fit_score.desc(), JobScore.scored_at.desc())
        .limit(limit)
        .all()
    )
    facets = jobs_tools._facets_for(db, [job.id for _, job in rows])
    return {
        "returned": len(rows),
        "items": [
            {
                **jobs_tools._row(job, facets.get(job.id)),
                "tier": score.tier,
                "fit_score": score.fit_score,
                "reason": score.reason,
                "gap": score.gap,
            }
            for score, job in rows
        ],
    }


_search = _catalogue_tool(jobs_tools.catalogue_specs()[0], jobs_tools.SEARCH_JOBS_TOOL,
                          "search_jobs")
_get_job = _catalogue_tool(jobs_tools.catalogue_specs()[1], jobs_tools.READ_JOB_TOOL,
                           "get_job")

REGISTRY: dict[str, Tool] = {
    "search_jobs": _search,
    "get_job": _get_job,
    "list_matches": Tool(
        spec={
            "name": "list_matches",
            "description": "List the signed-in user's best-scoring job matches, "
            "highest fit first, from scores Offerfy has already computed. Each "
            "row carries the posting plus tier, fit_score, reason and gap. "
            "Returns nothing for postings not yet scored; it never triggers "
            "scoring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": jobs_tools.SEARCH_LIMIT_MAX,
                        "description": f"Default {jobs_tools.SEARCH_LIMIT_DEFAULT}.",
                    }
                },
                "additionalProperties": False,
            },
        },
        handler=_list_matches,
    ),
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
