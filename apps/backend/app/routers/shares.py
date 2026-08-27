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
    share = db.query(ResumeShare).filter(ResumeShare.resume_id == resume.id).one_or_none()
    if share is None:
        return ShareState(public=False, token=None)
    return ShareState(public=True, token=share.token)


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
        token = share.token
        db.commit()
        return ShareState(public=True, token=token)
    if share is not None:
        db.delete(share)
        db.flush()
    db.commit()
    return ShareState(public=False, token=None)


@router.get("/v1/shares/{token}", response_model=PublicShareOut)
def public_share(token: str, db: Session = Depends(get_db)):
    resume = _resume_for_token(token, db)
    return PublicShareOut(title=resume.title, locale=resume.locale)


@router.get("/v1/shares/{token}/preview", response_model=PreviewPages)
def public_preview(token: str, db: Session = Depends(get_db)):
    resume = _resume_for_token(token, db)
    blobs = compile_typst_pages(resume.typst_source, "svg")
    return PreviewPages(pages=[blob.decode("utf-8") for blob in blobs])


@router.get("/v1/shares/{token}/export")
def public_export(token: str, db: Session = Depends(get_db)):
    resume = _resume_for_token(token, db)
    data = compile_typst(resume.typst_source, "pdf")
    return RawResponse(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": _pdf_disposition(resume.title)},
    )
