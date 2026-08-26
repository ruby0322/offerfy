from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import RateEvent

LIMITS = {"chat": 10, "export": 20}
WINDOW = timedelta(hours=1)


def try_consume_guest_rate(db: Session, guest_session_id: str, kind: str) -> bool:
    limit = LIMITS[kind]
    since = datetime.now(timezone.utc) - WINDOW
    count = (
        db.query(RateEvent)
        .filter(
            RateEvent.guest_session_id == guest_session_id,
            RateEvent.kind == kind,
            RateEvent.created_at >= since,
        )
        .count()
    )
    if count >= limit:
        return False
    db.add(RateEvent(guest_session_id=guest_session_id, kind=kind))
    db.flush()
    return True


def enforce_guest_rate(db: Session, guest_session_id: str, kind: str) -> None:
    if not try_consume_guest_rate(db, guest_session_id, kind):
        raise HTTPException(status_code=429, detail="Too many requests")
