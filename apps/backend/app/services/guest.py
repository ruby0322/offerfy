import hashlib
import secrets

from fastapi import Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GuestSession

GUEST_COOKIE = "offerfy_guest"


def hash_guest_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_guest(request: Request, response: Response, db: Session) -> GuestSession:
    raw = request.cookies.get(GUEST_COOKIE)
    if raw:
        session = (
            db.query(GuestSession)
            .filter(GuestSession.key_hash == hash_guest_key(raw))
            .one_or_none()
        )
        if session is not None:
            return session
    raw = secrets.token_urlsafe(32)
    session = GuestSession(key_hash=hash_guest_key(raw), locale="zh-TW")
    db.add(session)
    db.flush()
    settings = get_settings()
    response.set_cookie(
        GUEST_COOKIE,
        raw,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.app_env == "production",
    )
    return session
