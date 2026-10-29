from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pathlib import Path
import asyncio
import json
import logging

from app.db import get_db
from app.deps import get_current_user, load_resume_for_owner, owner_context
from app.models import LLM_CALL_PURPOSES, ChatMessage, GuestSession, LlmCall, User
from app.schemas import ChatMessageOut, ChatRequest
from app.services.attachment import llm_content_for_upload, stored_user_message
from app.services.chat_window import window_history
from app.services.extract import MAX_UPLOAD_BYTES, allowed_upload, extract_upload_text
from app.services.family_tools import (
    CREATE_TAILOR_TOOL,
    family_tool_handlers,
    family_tool_specs,
)
from app.services.llm import TEMPLATE_SWITCH_EXTRA, aiter_chat_edit, llm_configured
from app.services.revisions import set_draft_source
from app.services.role_card import build_role_card
from app.services.s3 import put_object
from app.services.templates import is_template_switch_message, parse_preview_spec, template_api_block
from app.services.typst_compile import compile_status
from app.services.metering import metered, preflight
from app.services import model_choice
from app.services.usage import (
    approx_edits_left,
    balance,
    event_cost_uusd,
    quota_event,
    tier_of,
)

logger = logging.getLogger(__name__)

router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _message_out(row: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(id=row.id, role=row.role, content=row.content)


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _history(db: Session, resume_id: str) -> list[ChatMessage]:
    """The rows that can go to the model, oldest first.

    `tool` rows are excluded in SQL rather than in the caller. They were always
    filtered out before being sent -- that is load-bearing, one production
    resume holds 1.44M characters of tool traces against 55,000 of history --
    but they were still read out of the database and discarded on every turn.
    `GET /messages` is the endpoint that returns the whole transcript, and it
    builds its own query.

    What is sent is bounded again further down, at the provider boundary: see
    `app.services.chat_window`.
    """
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume_id)
        .filter(ChatMessage.role.in_(("user", "assistant")))
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


def _context_notice(rows: list[ChatMessage]) -> dict | None:
    """Where the model's view of this conversation starts, for the UI.

    `rows` must be the user/assistant rows in order -- what `_history`
    returns. The window is recomputed here rather than reported back from
    `app.services.llm`, which has no Session and no message ids and keeps it
    that way; it is a list slice over data already in hand, so computing it
    twice costs nothing.

    This exists because the bound is otherwise invisible. A turn that no longer
    remembers something from a hundred messages ago looks like a broken
    assistant unless the product says plainly that it was not sent. None of it
    is a deletion: every row is still stored and still listed.
    """
    window = window_history([{"role": row.role, "content": row.content} for row in rows])
    if not window.folded:
        return None
    return {
        "type": "context",
        "dropped": window.dropped,
        "from_message_id": rows[window.cut].id,
    }


async def _parse_chat_input(
    request: Request,
) -> tuple[str, str | None, bytes | None, bool, str | None]:
    """The message, the attachment, and the two per-turn options.

    `model` is returned as whatever arrived, unvalidated: this function reads a
    request and nothing more. services.model_choice decides whether the caller
    may have it, and raises 400 if not.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        message = str(form.get("message") or "")
        prefer_full = str(form.get("prefer_full_source") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        chosen = str(form.get("model") or "").strip() or None
        upload = form.get("file")
        filename: str | None = None
        data: bytes | None = None
        raw_name = getattr(upload, "filename", None)
        read = getattr(upload, "read", None)
        if isinstance(raw_name, str) and raw_name.strip() and callable(read):
            filename = Path(raw_name).name
            payload = await read()
            data = payload if isinstance(payload, (bytes, bytearray)) else None
        return message, filename, data, prefer_full, chosen
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid chat request") from exc
    body = ChatRequest.model_validate(payload)
    return body.message, None, None, body.prefer_full_source, body.model


def _persist_tool(db: Session, resume_id: str, trace: dict) -> ChatMessage:
    row = ChatMessage(
        resume_id=resume_id,
        role="tool",
        content=json.dumps(trace, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _tailor_created(trace: dict) -> dict | None:
    if trace.get("name") != CREATE_TAILOR_TOOL:
        return None
    result = trace.get("result")
    if isinstance(result, dict) and result.get("tailor_id"):
        return {
            "type": "tailor_created",
            "resume_id": result["tailor_id"],
            "title": result.get("title") or "",
        }
    return None


def _persist_usage(
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


def _persist_assistant(db: Session, resume_id: str, content: str) -> ChatMessage:
    row = ChatMessage(resume_id=resume_id, role="assistant", content=content)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/v1/resumes/{resume_id}/chat")
async def chat(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    user, guest = owner_context(request, response, db, user, ensure=True)
    resume = load_resume_for_owner(resume_id, request, db, user, guest)
    # Once, before the turn starts, and never again inside it. Here rather
    # than inside generate() so the refusal is an ordinary 429 JSON response:
    # entering the meter inside the generator would turn it into an error frame
    # on a stream that then has to end. The Guard it returns is the evidence
    # the check passed, and the only thing `metered` accepts.
    guard = preflight(db, user=user, guest=guest, purpose="chat")

    message, filename, data, prefer_full_source, requested_model = await _parse_chat_input(
        request
    )
    message = (message or "").strip()
    if data is not None and filename:
        if not allowed_upload(filename):
            raise HTTPException(status_code=400, detail="Unsupported file type")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large")
    else:
        filename, data = None, None
    if not message and not data:
        raise HTTPException(status_code=400, detail="Message or file required")
    if not llm_configured():
        raise HTTPException(status_code=503, detail="Chat is not configured")

    # Resolved before the stream opens, for the same reason the quota is: a
    # model this caller may not have is an ordinary 400 rather than an error
    # frame on a stream that then has to end. An explicit choice is also
    # *stored* here, so the picker on the next page load agrees with the model
    # this turn ran on -- the alternative is a client that thinks it switched
    # and a server that did not.
    tier = tier_of(db, user, guest)
    if requested_model:
        model_choice.choose(db, user, guest, tier=tier, name=requested_model)
    model = model_choice.selected(
        db, user, guest, tier=tier, requested=requested_model
    )

    stored = stored_user_message(message, filename)
    llm_content: str | list[dict] = stored
    if filename and data is not None:
        extracted = extract_upload_text(filename, data)
        instruction = (
            (message or "Please use this attached file.")
            + f"\n\nThe user attached {filename}. "
            "Call read_typst, then apply_typst_edit with the full `source` "
            "to rewrite the resume from the attachment."
        )
        llm_content = llm_content_for_upload(
            filename, data, instruction=instruction, extracted=extracted
        )
        owner = user.id if user is not None else (guest.id if guest else "anon")
        # Archival copy of the attachment: the reply is built from `data` and
        # `extracted` above, so a storage failure must not fail the chat turn.
        put_object(
            f"uploads/{owner}/{resume.id}/chat/{filename}",
            data,
            "application/octet-stream",
            required=False,
        )

    user_msg = ChatMessage(resume_id=resume.id, role="user", content=stored)
    db.add(user_msg)
    db.commit()

    history = _history(db, resume.id)
    messages = [{"role": m.role, "content": m.content} for m in history]
    context_notice = _context_notice(history)
    if messages:
        messages[-1]["content"] = llm_content
    original_source = resume.typst_source
    template_switch = is_template_switch_message(message)
    if template_switch or filename:
        prefer_full_source = True
    extra = TEMPLATE_SWITCH_EXTRA if template_switch else ""
    spec = parse_preview_spec(message)
    if spec:
        extra += template_api_block(*spec)
    extra += build_role_card(db, resume)
    family_specs = family_tool_specs(resume)
    family_handlers = family_tool_handlers(db, resume)

    async def generate():
        # The whole generator is the billable region, so `relay` closes over
        # `meter` the way it already closes over the session. Entering here
        # rather than in the handler keeps the refusal above a plain 429.
        async with metered(guard) as meter:
            assistant_text = ""
            new_source = original_source
            # Which half of the turn the rounds below belong to. The repair loop
            # flips it, so its rounds are billed to `repair` rather than hiding in
            # the chat total.
            purpose = "chat"

            async def relay(event: dict):
                """Turn one LLM event into SSE frames. Shared by the turn and the repair loop."""
                nonlocal assistant_text, new_source
                kind = event.get("kind")
                if kind == "usage":
                    # Persisted and never relayed. The frontend dispatches on known
                    # `type` values only, so a usage frame would be inert there --
                    # but telemetry is not the client's business either way, and
                    # emitting nothing keeps the stream contract exactly as it was.
                    # The separate `quota` frame at the end of the turn is a
                    # different thing with a different audience: what this person
                    # has left, carrying none of these numbers.
                    #
                    # Two writes, two durabilities, on purpose: the LlmCall row is
                    # telemetry and may be lost, while meter.record increments the
                    # counter the limit is enforced against. Per round rather than
                    # per turn, because the whole cost model is built on the
                    # per-round token mix.
                    _persist_usage(db, resume.id, event, purpose, user=user, guest=guest)
                    meter.record(event)
                elif kind == "delta":
                    # Display only: nothing is persisted until the assistant event lands.
                    yield _sse({"type": "delta", "text": event["text"]})
                elif kind == "reasoning":
                    # Thinking, not an answer. Never persisted: it would come back as
                    # an assistant row on the next turn and be fed to the model as
                    # though it had said it out loud.
                    yield _sse({"type": "reasoning", "text": event["text"]})
                elif kind == "tool":
                    row = _persist_tool(db, resume.id, event["trace"])
                    yield _sse(
                        {
                            "type": "tool",
                            "message": _message_out(row).model_dump(),
                        }
                    )
                    created = _tailor_created(event["trace"])
                    if created:
                        yield _sse(created)
                elif kind == "source":
                    new_source = event["typst_source"]
                    set_draft_source(db, resume, new_source)
                    db.commit()
                    yield _sse(
                        {
                            "type": "source",
                            "typst_source": new_source,
                            "applied": True,
                        }
                    )
                elif kind == "assistant":
                    assistant_text = (
                        event["content"] or assistant_text or "Updated the Typst source."
                    )
                    row = _persist_assistant(db, resume.id, assistant_text)
                    yield _sse(
                        {
                            "type": "assistant",
                            "message": _message_out(row).model_dump(),
                        }
                    )

            try:
                # First frame when it applies, so the trimming is part of the
                # turn the user is watching rather than something they infer
                # later from the assistant forgetting things.
                if context_notice is not None:
                    yield _sse(context_notice)
                async for event in aiter_chat_edit(
                    messages,
                    original_source,
                    model=model,
                    prefer_full_source=prefer_full_source,
                    extra_system=extra,
                    verify_compile=True,
                    extra_tools=family_specs,
                    tool_handlers=family_handlers,
                ):
                    async for frame in relay(event):
                        yield frame
                repairs = 0
                while new_source != original_source and repairs < 3:
                    status = await asyncio.to_thread(compile_status, new_source)
                    if status.get("ok") is not False:
                        break
                    repairs += 1
                    purpose = "repair"
                    failed_after = (
                        "Typst compile failed after the template rewrite:\n"
                        if template_switch
                        else "Typst compile failed after the edit:\n"
                    )
                    fix_how = (
                        "Fix it using the package example's real API. "
                        "Call apply_typst_edit with only the full source."
                        if template_switch
                        else (
                            "Fix the Typst source so it compiles. "
                            "Escape special characters: in strings \\\" and \\\\ ; "
                            "in markup \\$ \\@ \\* . "
                            "Call read_typst first, then apply_typst_edit with only the full source."
                        )
                    )
                    repair_messages = [
                        *messages,
                        {"role": "assistant", "content": assistant_text},
                        {
                            "role": "user",
                            "content": (
                                failed_after
                                + f"{status.get('error') or 'compile error'}\n"
                                + fix_how
                            ),
                        },
                    ]
                    async for event in aiter_chat_edit(
                        repair_messages,
                        new_source,
                        model=model,
                        prefer_full_source=True,
                        extra_system=extra,
                        verify_compile=True,
                        extra_tools=family_specs,
                        tool_handlers=family_handlers,
                    ):
                        async for frame in relay(event):
                            yield frame
                # After the last _persist_usage has committed, so the number the
                # client is handed is the number in the database. Telemetry may be
                # lost without costing an answer, and so may this.
                try:
                    bal = balance(db, user, guest)
                    yield _sse(
                        quota_event(
                            bal, approx_edits_left(db, user, guest, bal, model=model)
                        )
                    )
                except Exception:
                    logger.warning("quota frame failed", exc_info=True)
                    db.rollback()
                yield _sse(
                    {
                        "type": "done",
                        "typst_source": new_source,
                        "applied": new_source != original_source,
                    }
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else "Chat request failed"
                yield _sse({"type": "error", "detail": detail, "status": exc.status_code})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/v1/resumes/{resume_id}/messages", response_model=list[ChatMessageOut])
def list_messages(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    user, guest = owner_context(request, response, db, user)
    resume = load_resume_for_owner(resume_id, request, db, user, guest)
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    # Headers, not the body: this still returns the whole transcript, every
    # role included, in the shape it always has. The pair says where the
    # model's view of it begins, so a reload redraws the marker the chat
    # stream drew. Absent means nothing is being left out.
    notice = _context_notice([r for r in rows if r.role in ("user", "assistant")])
    if notice is not None:
        response.headers["X-Context-Dropped"] = str(notice["dropped"])
        response.headers["X-Context-From"] = notice["from_message_id"]
    return [ChatMessageOut(id=r.id, role=r.role, content=r.content) for r in rows]
