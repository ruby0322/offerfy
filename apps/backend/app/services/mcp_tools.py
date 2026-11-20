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
import dataclasses
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import Job, JobScore, User
from app.schemas_editing import IntentIn
from app.services import jobs_tools, resume_access, resume_write
from app.services.revisions import source_hash
from app.services.typst_projection import parse_projection

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
    """The caller's own resumes. Never a guest's: `resumes_of` is user-only."""
    rows = sorted(resume_access.resumes_of(db, user), key=lambda r: r.id)
    # Newest first; sorted() is stable, so ties keep the id order above.
    rows.sort(key=lambda r: r.updated_at.timestamp() if r.updated_at else 0, reverse=True)
    return {
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "kind": r.kind or "master",
                "locale": r.locale,
                "parent_resume_id": r.parent_resume_id,
                "target_label": r.target_label,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    }


def _get_resume(db: Session, user: User, args: dict) -> dict:
    """One of the caller's resumes as the structured field view.

    Returns the unit ids and current values an edit intent needs, and the
    `hash` of the source they were read from, which is how a later edit proves
    it is editing what it read. A resume that is missing, someone else's, or a
    guest's is the same 404.
    """
    resume_id = args.get("resume_id")
    if not isinstance(resume_id, str) or not resume_id:
        raise HTTPException(status_code=400, detail="resume_id is required")
    resume = resume_access.owned_by_user(db, user, resume_id)
    projection = dataclasses.asdict(parse_projection(resume.typst_source))
    return {
        "resume_id": resume.id,
        "title": resume.title,
        "locale": resume.locale,
        "kind": resume.kind or "master",
        "parent_resume_id": resume.parent_resume_id,
        "target_label": resume.target_label,
        "hash": source_hash(resume.typst_source),
        "projection": projection,
    }


def _required_str(args: dict, name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=400, detail=f"{name} is required")
    return value


# What `edit_resume` may do: set a field, or add something. `apply_intent` also
# knows `delete` and `move_section`; the agent surface deliberately does not offer
# them, so the only way an assistant removes or reorders content is
# `put_resume_source`, which is guarded the same way and shows the whole document.
_EDIT_KINDS = ("set_arg", "set_line", "insert_bullet", "insert_item", "insert_section")
_EDIT_FIELDS = ("hash", "kind", "unit", "expect", "value", "text", "marks", "target",
                "dir", "placeholder", "title")


async def _edit_resume(db: Session, user: User, args: dict) -> dict:
    """One structured field edit. Makes no model call."""
    resume = resume_access.owned_by_user(db, user, _required_str(args, "resume_id"))
    if args.get("kind") not in _EDIT_KINDS:
        raise HTTPException(
            status_code=400, detail=f"kind must be one of: {', '.join(_EDIT_KINDS)}"
        )
    try:
        intent = IntentIn(**{k: args[k] for k in _EDIT_FIELDS if k in args})
    except ValidationError as exc:
        fields = ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]}))
        raise HTTPException(status_code=400, detail=f"Invalid edit arguments: {fields}")
    out = await resume_write.apply_edit(db, resume, intent)
    # No rendered pages: they are megabytes of SVG an assistant cannot use. The new
    # projection carries the unit ids a follow-up edit needs.
    return {"resume_id": resume.id, "hash": out["hash"], "projection": out["projection"]}


async def _put_resume_source(db: Session, user: User, args: dict) -> dict:
    """Replace one resume's whole Typst source. Makes no model call."""
    resume = resume_access.owned_by_user(db, user, _required_str(args, "resume_id"))
    expected = _required_str(args, "hash")
    source = args.get("typst_source")
    if not isinstance(source, str):
        raise HTTPException(status_code=400, detail="typst_source is required")
    out = await resume_write.write_source(
        db, resume, source=source, expected_hash=expected
    )
    return {"resume_id": resume.id, "hash": out["hash"]}


def _catalogue_tool(shared: dict) -> Tool:
    """A catalogue tool, borrowed whole from the chat surface.

    The spec is the shared one, unchanged, so there is exactly one definition
    (and one description to keep honest) of what a catalogue tool accepts. The
    handler runs the shared handler and turns its `{"error": ...}`
    answer into a tool error, which is how this transport reports failure.
    """
    handler_key = shared["name"]

    def handler(db: Session, user: User, args: dict) -> dict:
        payload = json.loads(jobs_tools.catalogue_handlers(db)[handler_key](args))
        if "error" in payload:
            missing = payload["error"].startswith("No posting")
            raise HTTPException(status_code=404 if missing else 400, detail=payload["error"])
        return payload

    return Tool(spec=shared, handler=handler)


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
        .filter(
            JobScore.user_id == user.id,
            JobScore.outcome == "scored",
            Job.is_active.is_(True),
        )
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


_CATALOGUE = {spec["name"]: _catalogue_tool(spec) for spec in jobs_tools.catalogue_specs()}

REGISTRY: dict[str, Tool] = {
    **_CATALOGUE,
    "list_matches": Tool(
        spec={
            "name": "list_matches",
            "description": "List the signed-in user's best-scoring job matches, "
            "highest fit first, from scores Offerfy has already computed. Each "
            "row carries the posting plus tier, fit_score, reason and gap. "
            "Only postings that are still open are listed, so a match that "
            "has since closed does not appear. Returns nothing for postings "
            "not yet scored; it never triggers scoring.",
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
    "edit_resume": Tool(
        spec={
            "name": "edit_resume",
            "description": "Change one field of one of the signed-in user's "
            "resumes: set an argument or a line, or add a bullet, an entry or a section. "
            "Call get_resume first and pass its `hash` and a unit `id` (and "
            "`expect`, that unit's current value, for set_arg and set_line). "
            "It is refused if the document changed since you read it, and if "
            "the result would not compile; nothing is written then. Returns the "
            "new `hash` and projection. It cannot delete or reorder anything.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."},
                    "hash": {"type": "string", "description": "From get_resume."},
                    "kind": {"type": "string", "enum": list(_EDIT_KINDS)},
                    "unit": {"type": "string", "description": "set_arg, set_line: a unit id."},
                    "expect": {
                        "type": "string",
                        "description": "set_arg, set_line: the unit's current value.",
                    },
                    "value": {"type": "string", "description": "set_arg: the new value."},
                    "text": {"type": "string", "description": "set_line: the new text."},
                    "marks": {
                        "type": "array",
                        "description": "set_line: link/emphasis marks over `text`.",
                        "items": {"type": "object"},
                    },
                    "target": {
                        "type": "string",
                        "description": "insert_bullet, insert_item, insert_section: the id to insert into or after.",
                    },
                    "placeholder": {"type": "string"},
                    "title": {"type": "string", "description": "insert_section: its heading."},
                },
                "required": ["resume_id", "hash", "kind"],
                "additionalProperties": False,
            },
        },
        handler=_edit_resume,
    ),
    "put_resume_source": Tool(
        spec={
            "name": "put_resume_source",
            "description": "Replace the WHOLE Typst source of one of the "
            "signed-in user's resumes. This is the only way to delete or "
            "reorder content, and it overwrites everything not in your "
            "`typst_source`, so call get_resume first for the current `hash` "
            "and write the full document. Refused if the document changed "
            "since that hash, if the source is empty, or if Typst cannot "
            "compile it; nothing is written then. Returns the new `hash`.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."},
                    "hash": {"type": "string", "description": "From get_resume."},
                    "typst_source": {"type": "string", "description": "The full new document."},
                },
                "required": ["resume_id", "hash", "typst_source"],
                "additionalProperties": False,
            },
        },
        handler=_put_resume_source,
    ),
    "list_resumes": Tool(
        spec={
            "name": "list_resumes",
            "description": "List the signed-in user's resumes: id, title, kind "
            "(master or tailor), locale, target label and last update.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        handler=_list_resumes,
    ),
    "get_resume": Tool(
        spec={
            "name": "get_resume",
            "description": "Read one of the signed-in user's resumes as its "
            "structured fields: every editable unit with its id and current "
            "value, plus a `hash` of the source they were read from. Use the "
            "ids to describe an edit and the hash to show you are editing what "
            "you read.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."}
                },
                "required": ["resume_id"],
                "additionalProperties": False,
            },
        },
        handler=_get_resume,
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
