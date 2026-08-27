from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import Response as RawResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, load_resume_for_owner, owner_context
from app.models import GuestSession, Resume, User
from app.schemas import (
    AtsReport,
    CompileBody,
    LOCALES,
    PreviewPages,
    ResumeCreate,
    ResumeListItem,
    ResumeOut,
    ResumeUpdate,
)
from app.services.ats import analyze_pdf
from app.services.extract import (
    MAX_UPLOAD_BYTES,
    allowed_upload,
)
from app.services.rate_limit import enforce_guest_rate
from app.services.s3 import put_object
from app.services.starter import default_title, generate_starter
from app.services.typst_compile import compile_typst, compile_typst_pages

router = APIRouter()


def _validate_locale(locale: str | None) -> str:
    value = locale or "zh-TW"
    if value not in LOCALES:
        raise HTTPException(status_code=400, detail="locale must be en, zh-TW, or zh-CN")
    return value


def _to_out(resume: Resume) -> ResumeOut:
    claimed = resume.claimed_at.isoformat() if resume.claimed_at else None
    return ResumeOut(
        id=resume.id,
        title=resume.title,
        typst_source=resume.typst_source,
        source=resume.source,
        locale=resume.locale,
        import_status=resume.import_status,
        upload_s3_key=resume.upload_s3_key,
        claimed_at=claimed,
    )


def _new_resume(
    db: Session,
    user: User | None,
    guest: GuestSession | None,
    *,
    title: str,
    locale: str,
    source: str,
    typst_source: str,
    upload_s3_key: str | None = None,
    import_status: str = "idle",
) -> Resume:
    resume = Resume(
        title=title,
        locale=locale,
        source=source,
        typst_source=typst_source,
        upload_s3_key=upload_s3_key,
        import_status=import_status,
        user_id=user.id if user is not None else None,
        guest_session_id=None if user is not None else (guest.id if guest else None),
    )
    if resume.user_id is None and resume.guest_session_id is None:
        raise HTTPException(status_code=500, detail="Missing owner")
    db.add(resume)
    db.flush()
    return resume


@router.post("/v1/resumes", response_model=ResumeOut, status_code=201)
def create_resume(
    body: ResumeCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    locale = _validate_locale(body.locale)
    _, guest = owner_context(request, response, db, user, ensure=True)
    if user is None and guest is None:
        raise HTTPException(status_code=401, detail="Guest session required")
    title = body.title or default_title(locale)
    resume = _new_resume(
        db,
        user,
        guest if user is None else None,
        title=title,
        locale=locale,
        source="create",
        typst_source=generate_starter(locale),
    )
    out = _to_out(resume)
    db.commit()
    return out


@router.post("/v1/resumes/upload", response_model=ResumeOut, status_code=201)
async def upload_resume(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    locale: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    locale_v = _validate_locale(locale)
    filename = file.filename or "upload.bin"
    if not allowed_upload(filename):
        raise HTTPException(status_code=400, detail="Unsupported file type")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    _, guest = owner_context(request, response, db, user, ensure=True)
    if user is None and guest is None:
        raise HTTPException(status_code=401, detail="Guest session required")

    resume = _new_resume(
        db,
        user,
        guest if user is None else None,
        title=title or default_title(locale_v),
        locale=locale_v,
        source="upload",
        typst_source=generate_starter(locale_v),
        import_status="idle",
    )
    owner = user.id if user is not None else (guest.id if guest else "anon")
    key = f"uploads/{owner}/{resume.id}/{Path(filename).name}"
    stored = put_object(key, data, file.content_type or "application/octet-stream")
    resume.upload_s3_key = stored
    db.commit()
    db.refresh(resume)
    return _to_out(resume)


@router.get("/v1/resumes", response_model=list[ResumeListItem])
def list_resumes(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    user, guest = owner_context(request, response, db, user, ensure=False)
    query = db.query(Resume)
    if user is not None:
        query = query.filter(Resume.user_id == user.id)
    elif guest is not None:
        query = query.filter(Resume.guest_session_id == guest.id)
    else:
        return []
    rows = query.all()
    return [
        ResumeListItem(id=r.id, title=r.title, source=r.source, locale=r.locale)
        for r in rows
    ]


def _owned_resume(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session,
    user: User | None,
    *,
    ensure: bool = False,
) -> Resume:
    user, guest = owner_context(request, response, db, user, ensure=ensure)
    return load_resume_for_owner(resume_id, request, db, user, guest)


@router.get("/v1/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    return _to_out(resume)


@router.put("/v1/resumes/{resume_id}", response_model=ResumeOut)
def put_resume(
    resume_id: str,
    body: ResumeUpdate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user, ensure=True)
    if body.typst_source is not None:
        resume.typst_source = body.typst_source
    if body.title is not None:
        resume.title = body.title
    db.flush()
    out = _to_out(resume)
    db.commit()
    return out


@router.post("/v1/resumes/{resume_id}/compile")
def compile_resume(
    resume_id: str,
    body: CompileBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    if body.format == "svg":
        return _preview_pages(resume.typst_source)
    data = compile_typst(resume.typst_source, "pdf")
    return RawResponse(content=data, media_type="application/pdf")


def _preview_pages(source: str) -> PreviewPages:
    blobs = compile_typst_pages(source, "svg")
    return PreviewPages(pages=[blob.decode("utf-8") for blob in blobs])


@router.get("/v1/resumes/{resume_id}/preview", response_model=PreviewPages)
def preview(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    return _preview_pages(resume.typst_source)


@router.get("/v1/resumes/{resume_id}/preview.svg", response_model=PreviewPages)
def preview_svg(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    return _preview_pages(resume.typst_source)


@router.get("/v1/resumes/{resume_id}/ats", response_model=AtsReport)
def ats_report(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    pdf = compile_typst(resume.typst_source, "pdf")
    return analyze_pdf(pdf, resume.typst_source)


@router.get("/v1/resumes/{resume_id}/export")
def export_pdf(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    user, guest = owner_context(request, response, db, user, ensure=False)
    resume = load_resume_for_owner(resume_id, request, db, user, guest)
    if user is None and guest is not None:
        enforce_guest_rate(db, guest.id, "export")
    data = compile_typst(resume.typst_source, "pdf")
    return RawResponse(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="resume.pdf"'},
    )
