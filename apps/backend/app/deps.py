import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AgentApiKey, GuestSession, Resume, TalentApiKey, User
from app.services.agent_keys import key_for_token as agent_key_for_token
from app.services.guest import GUEST_COOKIE, ensure_guest, hash_guest_key
from app.services.talent_keys import key_for_token

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


def resolve_user(request: Request, db: Session) -> User | None:
    """The cookie -> User lookup, on a session the caller owns.

    Split out of `get_current_user` so an endpoint can authorize *without*
    taking `Depends(get_db)`. SSE endpoints need that: FastAPI holds a `yield`
    dependency's session until the response finishes, which for a stream means
    for the stream's whole life, pinning one connection pool slot per open
    stream. Taking `Depends(get_current_user)` alone was enough to do it,
    because that dependency pulls `get_db` in itself.
    """
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    user_id = _unsign(raw, get_settings().auth_token_secret)
    if not user_id:
        return None
    return db.get(User, user_id)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return resolve_user(request, db)


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


def is_admin_user(user: User | None) -> bool:
    if user is None:
        return False
    allowed = admin_email_set()
    if not allowed:
        return False
    return (user.email or "").strip().casefold() in allowed


def require_admin(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    if not is_admin_user(user):
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


def require_talent_caller(
    request: Request, db: Session = Depends(get_db)
) -> TalentApiKey:
    """An operator-issued credential, or nothing at all.

    There is deliberately no fallthrough: a session cookie, a guest cookie and
    an admin account all get 401 here. The only thing that opens this door is
    a token minted by an operator, and no route mints one except the admin
    endpoint. That is still true of this credential (``ofk_``, TalentApiKey).
    The self-serve MCP credential a user generates in settings (``ofa_``) is
    a different table, a different prefix and a different door, and does not
    open this one. agent-markdown.ts says there is a self-serve MCP server but
    no machine OAuth server; keep both halves of that accurate.

    Every failure is the same 401 with the same message. Which of missing,
    malformed, unknown, revoked or expired it was is not the caller's
    business, and saying would let someone sort valid-but-dead tokens from
    invented ones.
    """
    unauthorized = HTTPException(
        status_code=401,
        detail="Credential required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise unauthorized
    key = key_for_token(db, header[7:].strip())
    if key is None:
        raise unauthorized
    # A key nobody can see being used is a key nobody thinks to revoke. This
    # makes every talent read a write, which it already was: the disclosure
    # row is the point of the endpoint.
    key.last_used_at = datetime.now(timezone.utc)
    return key


def require_agent_caller(
    request: Request, db: Session = Depends(get_db)
) -> tuple[User, AgentApiKey]:
    """A user's own agent token, or nothing at all.

    Same shape as require_talent_caller, for a credential that IS self-serve:
    a signed-in user mints it for their own assistant and it acts as them.
    There is deliberately no fallthrough. A session cookie, a guest cookie and
    an admin account all get 401 here; the only thing that opens this door is
    an `ofa_` bearer token. Tools downstream receive an already-resolved User
    and never see how it was authenticated, which is what lets another
    credential type (OAuth) be added later without touching any tool.

    Every failure is the same 401 with the same message. Which of missing,
    malformed, unknown, revoked, expired or orphaned it was is not the
    caller's business, and saying would let someone sort valid-but-dead
    tokens from invented ones.

    last_used_at is not stamped here. A write in this request's transaction is
    rolled back by any HTTPException a tool raises, so the stamp belongs to
    the transport, on its own session.
    """
    unauthorized = HTTPException(
        status_code=401,
        detail="Credential required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise unauthorized
    key = agent_key_for_token(db, header[7:].strip())
    if key is None:
        raise unauthorized
    user = db.get(User, key.user_id)
    # Defensive: users.id cascades to the key, so this is nearly unreachable.
    if user is None:
        raise unauthorized
    return user, key
