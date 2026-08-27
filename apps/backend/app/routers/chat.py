from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pathlib import Path
import json

from app.db import get_db
from app.deps import get_current_user, load_resume_for_owner, owner_context
from app.models import ChatMessage, User
from app.schemas import ChatMessageOut, ChatRequest
from app.services.attachment import llm_content_for_upload, stored_user_message
from app.services.extract import MAX_UPLOAD_BYTES, allowed_upload, extract_upload_text
from app.services.llm import TEMPLATE_SWITCH_EXTRA, iter_chat_edit, llm_configured
from app.services.rate_limit import enforce_guest_rate
from app.services.s3 import put_object
from app.services.templates import is_template_switch_message, parse_preview_spec, template_api_block
from app.services.typst_compile import compile_status

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
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


async def _parse_chat_input(request: Request) -> tuple[str, str | None, bytes | None, bool]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        message = str(form.get("message") or "")
        prefer_full = str(form.get("prefer_full_source") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        upload = form.get("file")
        filename: str | None = None
        data: bytes | None = None
        raw_name = getattr(upload, "filename", None)
        read = getattr(upload, "read", None)
        if isinstance(raw_name, str) and raw_name.strip() and callable(read):
            filename = Path(raw_name).name
            payload = await read()
            data = payload if isinstance(payload, (bytes, bytearray)) else None
        return message, filename, data, prefer_full
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid chat request") from exc
    body = ChatRequest.model_validate(payload)
    return body.message, None, None, body.prefer_full_source


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
    if user is None and guest is not None:
        enforce_guest_rate(db, guest.id, "chat")

    message, filename, data, prefer_full_source = await _parse_chat_input(request)
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
        put_object(
            f"uploads/{owner}/{resume.id}/chat/{filename}",
            data,
            "application/octet-stream",
        )

    user_msg = ChatMessage(resume_id=resume.id, role="user", content=stored)
    db.add(user_msg)
    db.commit()

    history = _history(db, resume.id)
    messages = [
        {"role": m.role, "content": m.content}
        for m in history
        if m.role in {"user", "assistant"}
    ]
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

    def generate():
        assistant_text = ""
        new_source = original_source
        try:
            for event in iter_chat_edit(
                messages,
                original_source,
                prefer_full_source=prefer_full_source,
                extra_system=extra,
                verify_compile=True,
            ):
                kind = event.get("kind")
                if kind == "tool":
                    row = _persist_tool(db, resume.id, event["trace"])
                    yield _sse(
                        {
                            "type": "tool",
                            "message": _message_out(row).model_dump(),
                        }
                    )
                elif kind == "source":
                    new_source = event["typst_source"]
                    resume.typst_source = new_source
                    db.commit()
                    yield _sse(
                        {
                            "type": "source",
                            "typst_source": new_source,
                            "applied": True,
                        }
                    )
                elif kind == "assistant":
                    assistant_text = event["content"] or "Updated the Typst source."
                    row = _persist_assistant(db, resume.id, assistant_text)
                    yield _sse(
                        {
                            "type": "assistant",
                            "message": _message_out(row).model_dump(),
                        }
                    )
            repairs = 0
            while new_source != original_source and repairs < 3:
                status = compile_status(new_source)
                if status.get("ok") is not False:
                    break
                repairs += 1
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
                for event in iter_chat_edit(
                    repair_messages,
                    new_source,
                    prefer_full_source=True,
                    extra_system=extra,
                    verify_compile=True,
                ):
                    kind = event.get("kind")
                    if kind == "tool":
                        row = _persist_tool(db, resume.id, event["trace"])
                        yield _sse(
                            {
                                "type": "tool",
                                "message": _message_out(row).model_dump(),
                            }
                        )
                    elif kind == "source":
                        new_source = event["typst_source"]
                        resume.typst_source = new_source
                        db.commit()
                        yield _sse(
                            {
                                "type": "source",
                                "typst_source": new_source,
                                "applied": True,
                            }
                        )
                    elif kind == "assistant":
                        assistant_text = event["content"] or assistant_text
                        row = _persist_assistant(db, resume.id, assistant_text)
                        yield _sse(
                            {
                                "type": "assistant",
                                "message": _message_out(row).model_dump(),
                            }
                        )
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
    return [ChatMessageOut(id=r.id, role=r.role, content=r.content) for r in rows]
