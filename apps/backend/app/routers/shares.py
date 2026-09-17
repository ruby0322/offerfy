import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import Response as RawResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import (
    load_resume_for_owner,
    owner_context,
    require_user,
)
from app.models import Resume, ResumeShare, User
from app.schemas import PreviewPages, PublicShareOut, ShareState, ShareUpdate
from app.services.og_image import compose_og_png, og_cache_get, og_cache_put, og_etag
from app.services.revisions import build_share_state, pin_current_draft, published_source
from app.services.typst_compile import compile_typst, compile_typst_pages

router = APIRouter()


def _owned_resume(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session,
    user: User | None,
) -> Resume:
    user, guest = owner_context(request, response, db, user, ensure=False)
    return load_resume_for_owner(resume_id, request, db, user, guest)


def _require_user_owned(resume: Resume, user: User) -> None:
    if resume.user_id != user.id:
        raise HTTPException(status_code=403, detail="Sign in required to share")


def _share_state(db: Session, resume: Resume) -> ShareState:
    return build_share_state(db, resume)


def _new_token(db: Session) -> str:
    for _ in range(8):
        token = secrets.token_urlsafe(16)
        exists = db.query(ResumeShare.id).filter(ResumeShare.token == token).one_or_none()
        if exists is None:
            return token
    raise HTTPException(status_code=500, detail="Could not allocate share token")


def _resume_for_token(token: str, db: Session) -> Resume:
    share = db.query(ResumeShare).filter(ResumeShare.token == token).one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Not found")
    resume = db.get(Resume, share.resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Not found")
    return resume


def _pdf_disposition(title: str) -> str:
    raw = "".join(
        ch if ch.isascii() and (ch.isalnum() or ch in " -_") else "_" for ch in title
    ).strip("._ ") or "resume"
    return f'attachment; filename="{raw}.pdf"'


@router.get("/v1/resumes/{resume_id}/share", response_model=ShareState)
def get_share(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    _require_user_owned(resume, user)
    return _share_state(db, resume)


@router.put("/v1/resumes/{resume_id}/share", response_model=ShareState)
def put_share(
    resume_id: str,
    body: ShareUpdate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    _require_user_owned(resume, user)
    share = db.query(ResumeShare).filter(ResumeShare.resume_id == resume.id).one_or_none()
    if body.public:
        if share is None:
            share = ResumeShare(resume_id=resume.id, token=_new_token(db))
            db.add(share)
            db.flush()
        if resume.published_revision_id is None:
            pin_current_draft(db, resume)
        out = _share_state(db, resume)
        db.commit()
        return out
    if share is not None:
        db.delete(share)
        db.flush()
    out = _share_state(db, resume)
    db.commit()
    return out


def _published_or_404(token: str, db: Session) -> tuple[Resume, str]:
    resume = _resume_for_token(token, db)
    source = published_source(db, resume)
    if source is None:
        raise HTTPException(status_code=404, detail="Not found")
    return resume, source


@router.get("/v1/shares/{token}", response_model=PublicShareOut)
def public_share(token: str, db: Session = Depends(get_db)):
    resume, _source = _published_or_404(token, db)
    return PublicShareOut(title=resume.title, locale=resume.locale)


@router.get("/v1/shares/{token}/preview", response_model=PreviewPages)
def public_preview(token: str, db: Session = Depends(get_db)):
    _resume, source = _published_or_404(token, db)
    blobs = compile_typst_pages(source, "svg")
    return PreviewPages(pages=[blob.decode("utf-8") for blob in blobs])


@router.get("/v1/shares/{token}/export")
def public_export(token: str, db: Session = Depends(get_db)):
    resume, source = _published_or_404(token, db)
    data = compile_typst(source, "pdf")
    return RawResponse(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": _pdf_disposition(resume.title)},
    )


def _og_cache_headers(etag: str) -> dict[str, str]:
    return {
        "ETag": etag,
        "Cache-Control": "public, max-age=300",
    }


def _png_response(body: bytes, etag: str) -> RawResponse:
    return RawResponse(
        content=body,
        media_type="image/png",
        headers=_og_cache_headers(etag),
    )


@router.get("/v1/shares/{token}/og.png")
def public_og(token: str, request: Request, db: Session = Depends(get_db)):
    _resume, source = _published_or_404(token, db)
    etag = og_etag(source)
    if request.headers.get("if-none-match") == etag:
        return RawResponse(status_code=304, headers=_og_cache_headers(etag))
    cached = og_cache_get(token, etag)
    if cached is not None:
        return _png_response(cached, etag)
    pages = compile_typst_pages(source, "png", pages="1", ppi=144)
    body = compose_og_png(pages[0])
    og_cache_put(token, etag, body)
    return _png_response(body, etag)
