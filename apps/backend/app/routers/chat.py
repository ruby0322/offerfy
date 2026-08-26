from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
import json

from app.db import get_db
from app.deps import get_current_user, load_resume_for_owner, owner_context
from app.models import ChatMessage, User
from app.schemas import ChatMessageOut, ChatRequest, ChatResponse
from app.services.llm import chat_edit
from app.services.rate_limit import enforce_guest_rate

router = APIRouter()


def _message_out(row: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(id=row.id, role=row.role, content=row.content)


def _history(db: Session, resume_id: str) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


@router.post("/v1/resumes/{resume_id}/chat", response_model=ChatResponse)
def chat(
    resume_id: str,
    body: ChatRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    user, guest = owner_context(request, response, db, user, ensure=True)
    resume = load_resume_for_owner(resume_id, request, db, user, guest)
    if user is None and guest is not None:
        enforce_guest_rate(db, guest.id, "chat")

    user_msg = ChatMessage(resume_id=resume.id, role="user", content=body.message)
    db.add(user_msg)
    db.flush()

    history = _history(db, resume.id)
    messages = [{"role": m.role, "content": m.content} for m in history if m.role in {"user", "assistant"}]
    original_source = resume.typst_source
    assistant_text, new_source, traces = chat_edit(messages, original_source)
    resume.typst_source = new_source
    for trace in traces:
        db.add(
            ChatMessage(
                resume_id=resume.id,
                role="tool",
                content=json.dumps(trace, ensure_ascii=False),
            )
        )
    assistant_msg = ChatMessage(resume_id=resume.id, role="assistant", content=assistant_text)
    db.add(assistant_msg)
    db.flush()
    applied = new_source != original_source
    rows = _history(db, resume.id)
    payload = ChatResponse(
        message=_message_out(assistant_msg),
        messages=[_message_out(row) for row in rows],
        typst_source=new_source,
        applied=applied,
    )
    # Commit before the HTTP response so a follow-up preview GET sees the new source.
    db.commit()
    return payload


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
    return [
        ChatMessageOut(id=r.id, role=r.role, content=r.content) for r in rows
    ]
