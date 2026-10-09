from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import MemoryItem, Resume

MAX_TEXT = 280
MAX_ACCEPTED = 20
LIVE = ("pending", "accepted")


class MemoryFullError(Exception):
    pass


def normalize_memory_text(text: str) -> str:
    return " ".join((text or "").split())


def propose_memory(
    db: Session, master: Resume, source_resume_id: str | None, text: str
) -> tuple[MemoryItem, bool]:
    clean = normalize_memory_text(text)
    if not clean:
        raise ValueError("Memory text is required")
    if len(clean) > MAX_TEXT:
        raise ValueError(f"Memory text must be at most {MAX_TEXT} characters")
    folded = clean.casefold()
    for item in list_live_memory(db, master.id):
        if item.text.casefold() == folded:
            return item, False
    item = MemoryItem(master_resume_id=master.id, source_resume_id=source_resume_id, text=clean)
    db.add(item)
    db.flush()
    return item, True


def accepted_count(db: Session, master_id: str) -> int:
    return (
        db.query(MemoryItem)
        .filter(MemoryItem.master_resume_id == master_id, MemoryItem.status == "accepted")
        .count()
    )


def accept_memory(db: Session, item: MemoryItem) -> MemoryItem:
    if item.status == "accepted":
        return item
    if item.status != "pending":
        raise ValueError("Only pending memory can be accepted")
    if accepted_count(db, item.master_resume_id) >= MAX_ACCEPTED:
        raise MemoryFullError
    item.status = "accepted"
    item.decided_at = datetime.now(timezone.utc)
    db.flush()
    return item


def dismiss_memory(db: Session, item: MemoryItem) -> MemoryItem:
    if item.status == "pending":
        item.status = "dismissed"
        item.decided_at = datetime.now(timezone.utc)
        db.flush()
    return item


def list_live_memory(db: Session, master_id: str) -> list[MemoryItem]:
    return (
        db.query(MemoryItem)
        .filter(MemoryItem.master_resume_id == master_id, MemoryItem.status.in_(LIVE))
        .order_by(MemoryItem.created_at.asc(), MemoryItem.id.asc())
        .all()
    )


def accepted_memory_texts(db: Session, master_id: str) -> list[str]:
    return [item.text for item in list_live_memory(db, master_id) if item.status == "accepted"]
