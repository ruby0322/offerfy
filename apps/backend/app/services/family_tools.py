from __future__ import annotations

import json
from typing import Callable

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.family import create_tailor, family_master
from app.services.memory import propose_memory

READ_MASTER_TOOL = "read_master"
CREATE_TAILOR_TOOL = "create_tailor"
PROPOSE_MEMORY_TOOL = "propose_memory"

_READ_MASTER = {
    "type": "function",
    "name": READ_MASTER_TOOL,
    "description": "Return the master resume's current saved Typst source (read-only).",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

_CREATE_TAILOR = {
    "type": "function",
    "name": CREATE_TAILOR_TOOL,
    "description": (
        "Create a new tailor resume. It copies the master's saved draft, or this "
        "tailor's own current draft when fork_from is 'current'. The new tailor "
        "always belongs to the master. At most once per turn. The new tailor "
        "opens after this turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "target_label": {"type": "string"},
            "target_context": {"type": "string"},
            "fork_from": {
                "type": "string",
                "enum": ["master", "current"],
                "description": (
                    "Which saved draft to copy. 'master' (the default) starts from the "
                    "master resume. 'current' starts from the tailor open in this chat, "
                    "for when the user wants a new version of what they are looking at."
                ),
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

_PROPOSE_MEMORY = {
    "type": "function",
    "name": PROPOSE_MEMORY_TOOL,
    "description": (
        "Suggest one durable fact or preference about the person (max 280 characters). "
        "The user must accept it before it is remembered."
    ),
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
}


def family_tool_specs(resume: Resume) -> list[dict]:
    specs = [_CREATE_TAILOR, _PROPOSE_MEMORY]
    if resume.kind == "tailor":
        specs.insert(0, _READ_MASTER)
    return specs


def family_tool_handlers(db: Session, resume: Resume) -> dict[str, Callable[[dict], str]]:
    created: list[str] = []

    def read_master(_args: dict) -> str:
        master = family_master(db, resume)
        return json.dumps(
            {"title": master.title, "source": master.typst_source}, ensure_ascii=False
        )

    def make_tailor(args: dict) -> str:
        if created:
            return json.dumps(
                {"error": "Only one tailor per turn. Tell the user to ask again for another."}
            )
        try:
            tailor = create_tailor(
                db,
                resume,
                title=args.get("title"),
                target_label=args.get("target_label"),
                target_context=args.get("target_context"),
                fork_from=str(args.get("fork_from") or "master"),
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        db.commit()
        created.append(tailor.id)
        return json.dumps(
            {
                "ok": True,
                "tailor_id": tailor.id,
                "title": tailor.title,
                "target_label": tailor.target_label,
                "forked_from": "current" if args.get("fork_from") == "current" else "master",
            },
            ensure_ascii=False,
        )

    def remember(args: dict) -> str:
        master = family_master(db, resume)
        try:
            item, is_new = propose_memory(db, master, resume.id, str(args.get("text") or ""))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        db.commit()
        requires_login = resume.user_id is None
        note = "Pending until the user accepts it."
        if requires_login:
            note += " The user must sign in first."
        return json.dumps(
            {
                "ok": True,
                "item_id": item.id,
                "text": item.text,
                "status": item.status,
                "created": is_new,
                "requires_login": requires_login,
                "note": note,
            },
            ensure_ascii=False,
        )

    handlers: dict[str, Callable[[dict], str]] = {
        CREATE_TAILOR_TOOL: make_tailor,
        PROPOSE_MEMORY_TOOL: remember,
    }
    if resume.kind == "tailor":
        handlers[READ_MASTER_TOOL] = read_master
    return handlers
