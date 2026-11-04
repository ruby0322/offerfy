"""Persisting one chat turn's rows: `tool`, `assistant`, and what it billed.

Extracted from `app.routers.chat` (`_persist_tool`, `_persist_assistant`,
`_persist_usage`) so that a service outside the router --
`app.services.import_runner` is the first one -- can reuse the exact same
insert logic without importing private functions from a router, which is the
wrong direction. The bodies are unchanged: this is a move, not a rewrite.

`persist_usage` is the `LlmCall` half, and it is not optional for a new
billable path: `app.services.usage.reconcile` compares `UsageCounter.uusd`
against the `LlmCall`-derived total per subject per window and reports any
disagreement as an admin gap. A path that moves the counter (through
`app.services.metering`) without writing the row leaves a permanent false
gap in that window, which is the alarm `metering` relies on being quiet.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.models import LLM_CALL_PURPOSES, ChatMessage, GuestSession, LlmCall, User
from app.services.usage import event_cost_uusd

logger = logging.getLogger(__name__)


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


def persist_usage(
    db: Session,
    resume_id: str,
    event: dict,
    purpose: str,
    *,
    user: User | None,
    guest: GuestSession | None,
) -> None:
    """Record what one model call billed, and who it billed to.

    Nothing here may fail a turn: a shape we did not expect or a database that
    will not take the row is a warning and a lost metric, never a lost chat
    response. The rollback matters as much as the log -- the same Session goes
    on to persist tool traces and the draft source after this.

    The owner is written in rather than left to be joined through resume_id
    later. Both routes from that column to an owner lose the attribution --
    resume deletion nulls it, and /v1/auth/claim reassigns it -- and the quota
    this feeds would reset with them. See models.LlmCall.user_id.
    """
    try:
        kind = event.get("purpose") or purpose
        if kind not in LLM_CALL_PURPOSES:
            kind = purpose
        # Priced here, at today's rates, and stored on the row -- so that
        # correcting a price later moves what new rounds cost and not what old
        # ones did. The meter computes the same figure from the same event
        # through the same function, so the two writes agree by construction
        # rather than by being handed a shared value; they stay separate
        # because they have different durabilities. See models.LlmCall.
        cost, _ = event_cost_uusd(event)
        db.add(
            LlmCall(
                resume_id=resume_id,
                user_id=user.id if user is not None else None,
                guest_session_id=guest.id if user is None and guest is not None else None,
                purpose=kind,
                model=str(event.get("model") or "")[:128],
                round_index=int(event.get("round_index") or 0),
                prefer_full_source=bool(event.get("prefer_full_source")),
                input_tokens=event.get("input_tokens"),
                cached_tokens=event.get("cached_tokens"),
                output_tokens=event.get("output_tokens"),
                reasoning_tokens=event.get("reasoning_tokens"),
                cache_write_tokens=event.get("cache_write_tokens"),
                source_chars=int(event.get("source_chars") or 0),
                cost_uusd=cost,
            )
        )
        db.commit()
    except Exception:
        logger.warning("llm usage telemetry failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            logger.warning("llm usage telemetry rollback failed", exc_info=True)
