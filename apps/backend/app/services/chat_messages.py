"""Persisting a chat turn's `tool` and `assistant` rows.

Extracted from `app.routers.chat` (`_persist_tool` / `_persist_assistant`) so
that a service outside the router -- `app.services.import_runner` is the
first one -- can reuse the exact same insert logic without importing private
functions from a router, which is the wrong direction. The bodies are
unchanged: this is a move, not a rewrite.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import ChatMessage


def persist_tool_message(db: Session, resume_id: str, trace: dict) -> ChatMessage:
    row = ChatMessage(
        resume_id=resume_id,
        role="tool",
        content=json.dumps(trace, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def persist_assistant_message(db: Session, resume_id: str, content: str) -> ChatMessage:
    row = ChatMessage(resume_id=resume_id, role="assistant", content=content)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
