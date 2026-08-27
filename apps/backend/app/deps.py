import hashlib
import hmac

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import GuestSession, Resume, User
from app.services.guest import GUEST_COOKIE, ensure_guest, hash_guest_key

SESSION_COOKIE = "offerfy_session"


def _sign(value: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def _unsign(token: str, secret: str) -> str | None:
    if "." not in token:
        return None
    value, sig = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return value


def set_session_cookie(response: Response, user_id: str) -> None:
    settings = get_settings()
    token = _sign(user_id, settings.auth_token_secret)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.app_env == "production",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    user_id = _unsign(raw, get_settings().auth_token_secret)
    if not user_id:
        return None
    return db.get(User, user_id)


def get_guest(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> GuestSession:
    return ensure_guest(request, response, db)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user


def admin_email_set() -> set[str]:
    raw = get_settings().admin_emails or ""
    return {part.strip().casefold() for part in raw.split(",") if part.strip()}


def require_admin(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    allowed = admin_email_set()
    if not allowed or (user.email or "").strip().casefold() not in allowed:
        raise HTTPException(status_code=404, detail="Not found")
    return user


def load_resume_for_admin(resume_id: str, db: Session) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Not found")
    return resume


def load_resume_for_owner(
    resume_id: str,
    request: Request,
    db: Session,
    user: User | None,
    guest: GuestSession | None,
) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    if user is not None and resume.user_id == user.id:
        return resume
    if guest is not None and resume.guest_session_id == guest.id:
        return resume
    raise HTTPException(status_code=404, detail="Resume not found")


def owner_context(
    request: Request,
    response: Response,
    db: Session,
    user: User | None,
    *,
    ensure: bool = False,
) -> tuple[User | None, GuestSession | None]:
    raw = request.cookies.get(GUEST_COOKIE)
    guest = None
    if raw:
        guest = (
            db.query(GuestSession)
            .filter(GuestSession.key_hash == hash_guest_key(raw))
            .one_or_none()
        )
    if user is not None:
        return user, guest
    if guest is not None:
        return None, guest
    if ensure:
        return None, ensure_guest(request, response, db)
    return None, None
