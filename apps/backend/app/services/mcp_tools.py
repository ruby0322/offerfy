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
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models import Job, JobScore, User
from app.schemas_editing import IntentIn
from app.schemas_jobs import JobPreferenceIn
from app.services import (
    export_link,
    family,
    jobs_tools,
    preferences_write,
    resume_access,
    resume_write,
    sharing,
)
from app.services.jobs.preferences import current_or_seeded, preference_out
from app.services.revisions import pin_current_draft, source_hash
from app.services.starter import default_title, generate_starter, validate_locale
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
    """One of the caller's resumes: the whole Typst source and the field view.

    `typst_source` is the document itself, and it is not optional. The
    projection is the unit ids and current values an edit intent needs -- unit
    values and byte spans *into* the source, never a substitute for it: the
    first line and the `#show: resume.with(...)` preamble appear in no unit. The
    only tool that can delete or reorder anything is `put_resume_source`, which
    replaces the whole document, so an agent that cannot read the document has
    to invent one, and both guards in front of that write pass on the
    invention: the `hash` is the one it was handed, and plain Typst compiles.
    `hash` is of the source these were read from, which is how a later edit
    proves it is editing what it read. A resume that is missing, someone
    else's, or a guest's is the same 404.
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
        "typst_source": resume.typst_source,
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


def _clean_title(value: Any) -> str:
    """A title as the router's rename accepts it: stripped, non-empty, <= TITLE_MAX."""
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="Title is required")
    clean = value.strip()
    if len(clean) > family.TITLE_MAX:
        raise HTTPException(status_code=400, detail="Title is too long")
    return clean


def _create_resume(db: Session, user: User, args: dict) -> dict:
    """A new blank resume from the static starter. Makes no model call."""
    locale = validate_locale(args.get("locale"))
    title = _clean_title(args["title"]) if "title" in args else default_title(locale)
    resume = resume_access.create_for_user(
        db,
        user,
        title=title,
        locale=locale,
        source="create",
        typst_source=generate_starter(locale),
    )
    db.commit()
    return {"resume_id": resume.id, "title": resume.title, "locale": resume.locale}


def _create_tailor(db: Session, user: User, args: dict) -> dict:
    """Fork a tailor from one of the caller's resumes. A pure copy: no model call."""
    resume = resume_access.owned_by_user(db, user, _required_str(args, "resume_id"))
    label = args.get("target_label")
    if label is not None and not isinstance(label, str):
        raise HTTPException(status_code=400, detail="target_label must be a string")
    # The same checker as every other title; family.create_tailor re-checks it.
    tailor = family.create_tailor(
        db, resume, title=_clean_title(args.get("title")), target_label=label
    )
    db.commit()
    return {
        "resume_id": tailor.id,
        "title": tailor.title,
        "parent_resume_id": tailor.parent_resume_id,
        "target_label": tailor.target_label,
    }


def _set_resume_title(db: Session, user: User, args: dict) -> dict:
    """Rename one of the caller's resumes. Touches the title and nothing else."""
    resume = resume_access.owned_by_user(db, user, _required_str(args, "resume_id"))
    resume.title = _clean_title(args.get("title"))
    db.commit()
    return {"resume_id": resume.id, "title": resume.title}


def _set_public(db: Session, user: User, args: dict, public: bool) -> dict:
    resume = resume_access.owned_by_user(db, user, _required_str(args, "resume_id"))
    if public:
        # Both website doors, because "publish" means "make the current version
        # visible": the publish button pins the draft unconditionally, but only
        # the share toggle creates the link, and it pins just when nothing is
        # pinned, so on its own it would publish a stale revision. Pin first, so
        # the share row can never exist without a pin; one commit covers both.
        pin_current_draft(db, resume)
    state = sharing.set_public(db, resume, public)
    db.commit()
    return state.model_dump()


def _publish_resume(db: Session, user: User, args: dict) -> dict:
    """Pin the current draft and make sure the link exists. The link comes from
    the SHARE door, not `POST .../publish`, which creates none."""
    return _set_public(db, user, args, True)


def _unpublish_resume(db: Session, user: User, args: dict) -> dict:
    """Delete the share row; leave the pinned revision, as the website does."""
    return _set_public(db, user, args, False)


def _export_resume(db: Session, user: User, args: dict) -> dict:
    """A ten-minute signed link to the PDF. Compiles nothing here."""
    resume = resume_access.owned_by_user(db, user, _required_str(args, "resume_id"))
    token, expiry = export_link.mint(resume.id)
    return {
        # The public path, behind the site's /api rewrite; resolve it against the
        # origin this MCP endpoint was reached on.
        "url": f"/api/v1/resumes/export/{quote(token, safe='')}",
        "expires_at": datetime.fromtimestamp(expiry, timezone.utc).isoformat(),
        "filename": export_link.pdf_filename(resume.title),
    }


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


def _preferences_out(db: Session, user: User, row, first_save: bool = False) -> dict:
    return preference_out(db, user, row, first_save=first_save).model_dump()


def _get_job_preferences(db: Session, user: User, args: dict) -> dict:
    """The saved preferences, or the computed seed. Never stores: a row is the
    "the user has edited this" marker, so a read must not create one."""
    return _preferences_out(db, user, current_or_seeded(db, user))


_PREFERENCE_FIELDS = ("target_functions", "target_locations", "target_seniority",
                      "remote_required", "activity")


def _set_job_preferences(db: Session, user: User, args: dict) -> dict:
    """Replace the preferences wholesale. An absent field is cleared, not kept."""
    unknown = sorted(set(args) - set(_PREFERENCE_FIELDS))
    if unknown:
        # A typo'd name would otherwise read as "omitted" and clear that field.
        raise HTTPException(
            status_code=400, detail=f"Unknown argument: {', '.join(k[:64] for k in unknown)}"
        )
    # Two validations on purpose, in this order: these checks run FIRST and give
    # an agent a plain, argument-named message ("target_functions must be a list
    # of strings"); JobPreferenceIn then rejects what is well-typed but not in the
    # taxonomy ("Invalid preferences: <field>"). Pydantic would also reject every
    # wrong type here and also name the field, so deleting these checks changes
    # only the error text, not the outcome. That is why the tests assert the
    # EXACT text: a substring check passes under either path, which is how five
    # mutants of these checks once survived. Do not drop one of the two.
    for name in ("target_functions", "target_locations"):
        value = args.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise HTTPException(
                status_code=400, detail=f"{name} must be a list of strings"
            )
    seniority = args.get("target_seniority")
    if seniority is not None and not isinstance(seniority, str):
        raise HTTPException(status_code=400, detail="target_seniority must be a string")
    remote = args.get("remote_required")
    if remote is not None and not isinstance(remote, bool):
        raise HTTPException(status_code=400, detail="remote_required must be a boolean")
    if "activity" in args and not isinstance(args["activity"], str):
        raise HTTPException(status_code=400, detail="activity must be a string")
    try:
        body = JobPreferenceIn(**args)
    except ValidationError as exc:
        fields = ", ".join(sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]}))
        raise HTTPException(status_code=400, detail=f"Invalid preferences: {fields}")
    row, first_save = preferences_write.save(db, user, body)
    return _preferences_out(db, user, row, first_save)


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
            "not yet scored; it never triggers scoring. An empty list does "
            "NOT mean the user has no matches: it usually means nothing has "
            "been scored yet, which is expected right after they first saved "
            "job preferences (scoring happens in Offerfy's next scheduled "
            "pass). Do not present an empty result as a finding; say matches "
            "are not ready yet.",
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
            "Call get_resume first and pass its `hash` and a unit `id` from "
            "its `projection` (and "
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
                        "description": "insert_bullet, insert_item, "
                        "insert_section: the id to insert into or after.",
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
            "`typst_source`. Call get_resume first and edit the "
            "`typst_source` it returns, keeping its preamble and everything "
            "you were not asked to change; send that back with its `hash`. "
            "Never compose a document from the projection alone, and never "
            "from memory: anything you leave out is deleted, and neither "
            "guard below can tell an edited document from an invented one. "
            "Refused if the document changed since that hash, if the source "
            "is empty, or if Typst cannot compile it; nothing is written "
            "then. Returns the new `hash`.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."},
                    "hash": {"type": "string", "description": "From get_resume."},
                    "typst_source": {
                        "type": "string",
                        "description": "The full new document: get_resume's "
                        "`typst_source` with your change applied.",
                    },
                },
                "required": ["resume_id", "hash", "typst_source"],
                "additionalProperties": False,
            },
        },
        handler=_put_resume_source,
    ),
    "create_resume": Tool(
        spec={
            "name": "create_resume",
            "description": "Create a new blank resume for the signed-in user, "
            "from Offerfy's starter template in the given language. Makes no "
            "model call. Returns its `resume_id`; read it with get_resume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Default: a name for the locale."},
                    "locale": {
                        "type": "string",
                        "enum": ["en", "zh-TW", "zh-CN"],
                        "description": "Default zh-TW.",
                    },
                },
                "additionalProperties": False,
            },
        },
        handler=_create_resume,
    ),
    "create_tailor": Tool(
        spec={
            "name": "create_tailor",
            "description": "Fork a tailored copy of one of the signed-in user's "
            "resumes, for a specific role. It is a plain copy of the master's "
            "current content, so edit it afterwards with edit_resume. Makes "
            "no model call. Returns the new `resume_id`.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."},
                    "title": {"type": "string", "description": "Name of the tailor."},
                    "target_label": {
                        "type": "string",
                        "description": "What it is aimed at, e.g. a company or role.",
                    },
                },
                "required": ["resume_id", "title"],
                "additionalProperties": False,
            },
        },
        handler=_create_tailor,
    ),
    "set_resume_title": Tool(
        spec={
            "name": "set_resume_title",
            "description": "Rename one of the signed-in user's resumes. Changes "
            "the title only; the content is untouched.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."},
                    "title": {"type": "string"},
                },
                "required": ["resume_id", "title"],
                "additionalProperties": False,
            },
        },
        handler=_set_resume_title,
    ),
    "publish_resume": Tool(
        spec={
            "name": "publish_resume",
            "description": "Make one of the signed-in user's resumes public: "
            "creates a link that anyone who has it can open, with no sign-in, "
            "and returns it as `token` (the page is /s/<token> on the site). It "
            "publishes the CURRENT draft, including edits made since any earlier "
            "publish, so what the link shows is what you just edited. Also: "
            "if the user has \"open to matching\" on and their "
            "talent profile points at this resume, publishing re-opens that "
            "profile to recruiters with credentials. Only call this when the "
            "user has asked to publish.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."}
                },
                "required": ["resume_id"],
                "additionalProperties": False,
            },
        },
        handler=_publish_resume,
    ),
    "unpublish_resume": Tool(
        spec={
            "name": "unpublish_resume",
            "description": "Withdraw the public link of one of the signed-in "
            "user's resumes; the old link stops working. It does NOT close the "
            "recruiter-facing talent profile: that stays readable to "
            "credentialed recruiters for as long as the user has \"open to "
            "matching\" on, and only the user can turn that off, on the "
            "website. Do not tell the user their resume is fully private after "
            "this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."}
                },
                "required": ["resume_id"],
                "additionalProperties": False,
            },
        },
        handler=_unpublish_resume,
    ),
    "export_resume": Tool(
        spec={
            "name": "export_resume",
            "description": "Get a download link for one of the signed-in user's "
            "resumes as a PDF of its current draft. Returns `url`, a PATH, not a "
            "full URL: resolve it against the origin you reached this MCP "
            "endpoint on (if that is https://example.com/api/mcp, fetch "
            "https://example.com/api/v1/resumes/export/<token>). Also returns "
            "`expires_at` (the link works for 10 minutes) and `filename`. "
            "Anyone holding the link can "
            "download that PDF until it expires, so do not post it anywhere. "
            "It does not publish the resume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resume_id": {"type": "string", "description": "From list_resumes."}
                },
                "required": ["resume_id"],
                "additionalProperties": False,
            },
        },
        handler=_export_resume,
    ),
    "get_job_preferences": Tool(
        spec={
            "name": "get_job_preferences",
            "description": "Read the signed-in user's saved job preferences: the "
            "functions, countries, seniority, remote requirement and activity "
            "that select which postings are scored for them. Before they have "
            "ever saved, this returns a suggestion computed from their resume "
            "(`updated_at` is null) and stores nothing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        handler=_get_job_preferences,
    ),
    "set_job_preferences": Tool(
        spec={
            "name": "set_job_preferences",
            "description": "Replaces ALL of the signed-in user's job preferences "
            "and clears every field you do not supply; it is not a patch. "
            "Call get_job_preferences first and send back the fields you want "
            "to keep. A missing list becomes empty, a missing target_seniority "
            "or remote_required becomes unset, and a missing activity becomes "
            "actively_looking. `first_save` in the result is true when this was "
            "their first ever save. Saving does NOT score anything: scored "
            "matches appear only after Offerfy's next scheduled scoring pass, "
            "so list_matches will be empty right after a save. Do not tell the "
            "user they have no matches because of that. Only call this when "
            "the user has asked to change their preferences. Makes no model call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_functions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Job functions, e.g. backend, design.",
                    },
                    "target_locations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lowercase two-letter country codes, e.g. tw.",
                    },
                    "target_seniority": {
                        "type": ["string", "null"],
                        "description": "One seniority level, e.g. senior.",
                    },
                    "remote_required": {"type": ["boolean", "null"]},
                    "activity": {
                        "type": "string",
                        "enum": ["actively_looking", "browsing", "paused"],
                    },
                },
                "additionalProperties": False,
            },
        },
        handler=_set_job_preferences,
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
            "description": "Read one of the signed-in user's resumes: "
            "`typst_source` is the complete Typst document, `projection` is "
            "every editable unit with its id and current value, and `hash` is "
            "of the source both were read from. Use the ids to describe an "
            "edit with edit_resume, the whole `typst_source` as the starting "
            "point for put_resume_source, and the hash to show you are "
            "editing what you read.",
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
