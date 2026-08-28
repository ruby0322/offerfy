import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import (
    clear_session_cookie,
    get_current_user,
    require_user,
    set_session_cookie,
)
from app.models import GuestSession, Resume, User
from app.schemas import AuthMe, ClaimResponse
from app.services.guest import GUEST_COOKIE, hash_guest_key

router = APIRouter()


def _google_picture(info: dict) -> str | None:
    raw = info.get("picture")
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    if url.startswith("https://") and len(url) <= 1024:
        return url
    return None


def _public_origin(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _is_loopback_uri(uri: str) -> bool:
    host = (urlparse(uri).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _redirect_uri(request: Request) -> str:
    settings = get_settings()
    if settings.google_redirect_uri:
        uri = settings.google_redirect_uri
    else:
        # Browser hits Next's /api rewrite, not the backend path.
        uri = f"{_public_origin(request)}/api/v1/auth/google/callback"
    if settings.app_env == "production" and _is_loopback_uri(uri):
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_REDIRECT_URI must be the public HTTPS origin in production",
        )
    return uri


_EDITOR_NEXT = re.compile(
    r"^/editor/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _safe_next(raw: str | None) -> str | None:
    if not raw:
        return None
    path = raw.strip()
    if "//" in path or "://" in path or "\\" in path:
        return None
    if path == "/admin" or path.startswith("/admin/"):
        return path
    if _EDITOR_NEXT.fullmatch(path):
        return path
    return None


@router.get("/v1/auth/google/start")
def google_start(request: Request):
    settings = get_settings()
    if not (settings.google_client_id or "").strip():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
    }
    nxt = _safe_next(request.query_params.get("next"))
    if nxt:
        params["state"] = nxt
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url, status_code=302)


@router.get("/v1/auth/google/callback")
def google_callback(request: Request, code: str | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    token_url = "https://oauth2.googleapis.com/token"
    try:
        with httpx.Client(timeout=20.0) as client:
            token_resp = client.post(
                token_url,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": _redirect_uri(request),
                    "grant_type": "authorization_code",
                },
            )
            if token_resp.status_code >= 400:
                raise HTTPException(status_code=400, detail="Google sign-in failed")
            access = token_resp.json().get("access_token")
            if not access:
                raise HTTPException(status_code=400, detail="Google sign-in failed")
            info_resp = client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access}"},
            )
            if info_resp.status_code >= 400:
                raise HTTPException(status_code=400, detail="Google sign-in failed")
            info = info_resp.json()
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Google sign-in failed") from exc

    sub = info.get("sub")
    email = info.get("email")
    if not sub or not email:
        raise HTTPException(status_code=400, detail="Google sign-in failed")

    picture = _google_picture(info)
    user = db.query(User).filter(User.google_sub == sub).one_or_none()
    if user is None:
        user = User(google_sub=sub, email=email, locale="zh-TW", picture=picture)
        db.add(user)
        db.flush()
    else:
        user.email = email
        user.picture = picture

    next_path = _safe_next(request.query_params.get("state"))
    redirect = RedirectResponse(next_path or "/dashboard", status_code=302)
    set_session_cookie(redirect, user.id)
    return redirect


@router.post("/v1/auth/claim", response_model=ClaimResponse)
def claim(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    raw = request.cookies.get(GUEST_COOKIE)
    if not raw:
        return ClaimResponse(claimed=0)
    guest = (
        db.query(GuestSession)
        .filter(GuestSession.key_hash == hash_guest_key(raw))
        .one_or_none()
    )
    if guest is None:
        return ClaimResponse(claimed=0)
    now = datetime.now(timezone.utc)
    resumes = db.query(Resume).filter(Resume.guest_session_id == guest.id).all()
    for resume in resumes:
        resume.user_id = user.id
        resume.guest_session_id = None
        resume.claimed_at = now
    db.flush()
    return ClaimResponse(claimed=len(resumes))


@router.get("/v1/auth/me", response_model=AuthMe)
def me(user: User | None = Depends(get_current_user)):
    if user is None:
        return AuthMe(user=None, guest=True)
    return AuthMe(
        user={
            "id": user.id,
            "email": user.email,
            "locale": user.locale,
            "picture": user.picture,
        },
        guest=False,
    )


@router.post("/v1/auth/logout")
def logout():
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response
