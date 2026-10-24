from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RateEvent

# Exports only. Chat moved to app.services.usage, which meters cost over a
# weekly window and keeps its own hourly burst guard -- counted off the LlmCall
# rows a turn writes anyway, so no RateEvent row is needed for it. An export is
# a Typst compile: CPU, not tokens, so it does not belong in a cost meter and
# stays here as a plain abuse guard.
#
# 'chat' is still permitted by RateEvent's check constraint on purpose: rows
# written before the move exist and must stay readable.
LIMITS = {"export": 20}
WINDOW = timedelta(hours=1)


def guest_rate_limits_enabled() -> bool:
    return get_settings().app_env == "production"


def try_consume_guest_rate(db: Session, guest_session_id: str, kind: str) -> bool:
    if not guest_rate_limits_enabled():
        return True
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
