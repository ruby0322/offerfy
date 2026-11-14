import asyncio
import logging
from pathlib import Path

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import Response as RawResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db, get_session_factory
from app.deps import get_current_user, load_resume_for_owner, owner_context, require_user
from app.models import GuestSession, Job, Resume, ResumeImport, ResumeRevision, User
from app.schemas import (
    AtsReport,
    CompileBody,
    FamilyMember,
    LOCALES,
    PreviewPages,
    ResumeCreate,
    ResumeFamilyOut,
    ResumeListItem,
    ResumeOut,
    ResumeUpdate,
    RevisionDetail,
    RevisionListItem,
    ShareState,
    TailorCreate,
    TemplateSwitchApplied,
    TemplateSwitchOut,
    TemplateSwitchRequest,
)
from app.services.ats import analyze_pdf
from app.services.chat_messages import persist_template_switch
from app.services.emitters import emitter_for
from app.services.family import create_tailor, family_master, family_tailors
from app.services.jobs.applications import link_tailor
from app.services.extract import (
    MAX_UPLOAD_BYTES,
    allowed_upload,
)
from app.services.import_run import ImportAlreadyRunning, snapshot as import_snapshot, start_run
from app.services.import_runner import run_import
from app.services.metering import metered, preflight
from app.services.rate_limit import enforce_guest_rate
from app.services.resume_content import (
    extract_content,
    record_extract_spend,
    restamp_content,
    store_content,
    stored_content,
)
from app.services.revisions import (
    pin_current_draft,
    set_draft_source,
    source_hash,
    unpublished_changes,
    build_share_state,
)
from app.services.s3 import delete_object, put_object
from app.services.sse import SSE_HEADERS, sse_frame
from app.services.starter import default_title, generate_starter, title_from_filename
from app.services.templates import apply_prompt, is_supported
from app.services.typst_compile import (
    compile_preview_pages,
    compile_typst,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# How often the stream route polls for a new snapshot. See
# `_stream_import_snapshots` for why this must be a fresh session each poll
# rather than one held open for the connection's lifetime.
IMPORT_POLL_SECONDS = 0.4

# Strong references to in-flight import tasks. `asyncio.create_task` does not
# keep its task alive on its own -- a task with no reference anywhere can be
# garbage-collected mid-flight, silently abandoning the import. Each task
# removes itself here via a done-callback once it finishes.
_import_tasks: set[asyncio.Task] = set()


async def _run_import_detached(resume_id: str, run_id: str, model: str | None) -> None:
    """Run one import on its own session, independent of the request that started it.

    The route's own `Session` (from `Depends(get_db)`) is closed by FastAPI
    at request teardown, which happens long before an import finishes. Running
    `run_import` against that session would mean running it against a closed
    one -- a bug that can look like it works under `TestClient` (where
    teardown timing can differ) and then fail in production. So this opens
    its own session from `get_session_factory()`, re-loads the resume and the
    run by id inside it, and closes that session when done -- never touching
    the request's session at all.
    """
    factory = get_session_factory()
    db = factory()
    try:
        resume = db.get(Resume, resume_id)
        run = db.get(ResumeImport, run_id)
        if resume is None or run is None:
            # Both were just written by the route that scheduled this, so one
            # of them missing means the resume was deleted in between. Silence
            # here left a run stuck `running` with nothing explaining why.
            logger.warning(
                "import not started: resume_id=%s run_id=%s no longer exist",
                resume_id,
                run_id,
            )
            return
        await run_import(db, resume, run, model=model)
    finally:
        db.close()


def _schedule_import(resume_id: str, run_id: str, model: str | None = None) -> None:
    """Schedule `_run_import_detached` on the running loop and keep it alive.

    This is the seam route tests monkeypatch to a no-op so a POST test never
    kicks off a real import -- see `tests/test_import_api.py`.
    """
    task = asyncio.create_task(_run_import_detached(resume_id, run_id, model))
    _import_tasks.add(task)
    task.add_done_callback(_import_tasks.discard)


def _validate_locale(locale: str | None) -> str:
    value = locale or "zh-TW"
    if value not in LOCALES:
        raise HTTPException(status_code=400, detail="locale must be en, zh-TW, or zh-CN")
    return value


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _to_out(db: Session, resume: Resume) -> ResumeOut:
    return ResumeOut(
        id=resume.id,
        title=resume.title,
        typst_source=resume.typst_source,
        source=resume.source,
        locale=resume.locale,
        import_status=resume.import_status,
        upload_s3_key=resume.upload_s3_key,
        claimed_at=_iso(resume.claimed_at),
        created_at=_iso(resume.created_at) or "",
        updated_at=_iso(resume.updated_at),
        published_revision_id=resume.published_revision_id,
        unpublished_changes=unpublished_changes(db, resume),
        kind=resume.kind or "master",
        parent_resume_id=resume.parent_resume_id,
        target_label=resume.target_label,
        target_context=resume.target_context,
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
    set_draft_source(db, resume, typst_source)
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
    out = _to_out(db, resume)
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
        title=(title.strip() if title and title.strip() else title_from_filename(filename, locale_v)),
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
    return _to_out(db, resume)


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
        ResumeListItem(
            id=r.id,
            title=r.title,
            source=r.source,
            locale=r.locale,
            kind=r.kind or "master",
            parent_resume_id=r.parent_resume_id,
            target_label=r.target_label,
            updated_at=_iso(r.updated_at),
        )
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


def _member(r: Resume) -> FamilyMember:
    return FamilyMember(
        id=r.id,
        title=r.title,
        kind=r.kind or "master",
        target_label=r.target_label,
        updated_at=_iso(r.updated_at),
    )


@router.post("/v1/resumes/{resume_id}/tailors", response_model=ResumeOut, status_code=201)
def create_tailor_route(
    resume_id: str,
    body: TailorCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    member = _owned_resume(resume_id, request, response, db, user, ensure=True)
    # 422 for a reference carried in the body, 404 for one carried in the
    # path (see POST /v1/me/applications). Checked before anything is written.
    if body.job_id is not None and db.get(Job, body.job_id) is None:
        raise HTTPException(status_code=422, detail="Job not found")
    try:
        tailor = create_tailor(
            db,
            member,
            title=body.title,
            target_label=body.target_label,
            target_context=body.target_context,
            fork_from=body.fork_from,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Guests are dropped here on purpose, not by oversight: applications.user_id
    # is NOT NULL, so a guest has nowhere to hold the linkage. The tailor is the
    # point of the request and the linkage is not, so it still returns 201.
    if body.job_id is not None and user is not None:
        # create_tailor only flushes, and link_tailor commits: that one commit
        # lands the tailor and its application together, so neither exists
        # without the other. The commit below is then a no-op for this path.
        link_tailor(db, user=user, job_id=body.job_id, tailor_resume_id=tailor.id)
    out = _to_out(db, tailor)
    db.commit()
    return out


@router.get("/v1/resumes/{resume_id}/family", response_model=ResumeFamilyOut)
def get_family(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    member = _owned_resume(resume_id, request, response, db, user)
    master = family_master(db, member)
    return ResumeFamilyOut(
        master=_member(master),
        tailors=[_member(r) for r in family_tailors(db, master)],
    )


@router.get("/v1/resumes/{resume_id}", response_model=ResumeOut)
def get_resume(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    return _to_out(db, resume)


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
        set_draft_source(db, resume, body.typst_source)
    if body.title is not None:
        stripped = body.title.strip()
        if not stripped:
            raise HTTPException(status_code=400, detail="Title is required")
        if len(stripped) > 255:
            raise HTTPException(status_code=400, detail="Title is too long")
        resume.title = stripped
    db.flush()
    out = _to_out(db, resume)
    db.commit()
    return out


@router.delete("/v1/resumes/{resume_id}", status_code=204)
def delete_resume(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    # A master takes its tailored versions, revisions, chat, share and family
    # memory with it; the ORM cascades handle every one of those. The uploads
    # have no cascade, so collect their keys while the rows still exist.
    doomed = [resume, *family_tailors(db, resume)]
    upload_keys = [r.upload_s3_key for r in doomed if r.upload_s3_key]
    db.delete(resume)
    db.commit()
    for key in upload_keys:
        delete_object(key)
    return RawResponse(status_code=204)


def _revision_or_404(db: Session, resume: Resume, revision_id: str) -> ResumeRevision:
    row = db.get(ResumeRevision, revision_id)
    if row is None or row.resume_id != resume.id:
        raise HTTPException(status_code=404, detail="Revision not found")
    return row


def _revision_list_item(resume: Resume, row: ResumeRevision) -> RevisionListItem:
    return RevisionListItem(
        id=row.id,
        created_at=_iso(row.created_at) or "",
        is_published=row.id == resume.published_revision_id,
    )


@router.get("/v1/resumes/{resume_id}/revisions", response_model=list[RevisionListItem])
def list_revisions(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    rows = (
        db.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == resume.id)
        .order_by(ResumeRevision.created_at.desc(), ResumeRevision.id.desc())
        .all()
    )
    return [_revision_list_item(resume, row) for row in rows]


@router.get("/v1/resumes/{resume_id}/revisions/{revision_id}", response_model=RevisionDetail)
def get_revision(
    resume_id: str,
    revision_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    row = _revision_or_404(db, resume, revision_id)
    item = _revision_list_item(resume, row)
    return RevisionDetail(
        id=item.id,
        created_at=item.created_at,
        is_published=item.is_published,
        typst_source=row.typst_source,
    )


@router.post(
    "/v1/resumes/{resume_id}/revisions/{revision_id}/restore",
    response_model=ResumeOut,
)
def restore_revision(
    resume_id: str,
    revision_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user, ensure=True)
    row = _revision_or_404(db, resume, revision_id)
    set_draft_source(db, resume, row.typst_source, coalesce=False)
    out = _to_out(db, resume)
    db.commit()
    return out


@router.post(
    "/v1/resumes/{resume_id}/template",
    response_model=TemplateSwitchOut | TemplateSwitchApplied,
)
async def switch_template(
    resume_id: str,
    body: TemplateSwitchRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    # Checked before anything else, including ownership: a template a list
    # merely hides but this endpoint still accepted would make the support
    # list cosmetic -- see services.templates.is_supported.
    if not is_supported(body.name, body.version):
        raise HTTPException(status_code=400, detail="Template is not supported")
    owner, guest = owner_context(request, response, db, user, ensure=True)
    resume = load_resume_for_owner(resume_id, request, db, owner, guest)

    def _model_path() -> TemplateSwitchOut:
        return TemplateSwitchOut(mode="llm", prompt=apply_prompt(body.name, body.version))

    emitter = emitter_for(body.name, body.version)
    if emitter is None:
        # No emitter registered for this pair -- the model path remains
        # correct, if slower: this is the prompt that used to be typed into
        # chat under the person's name instead of sent as its own request.
        return _model_path()

    content = stored_content(db, resume)
    if content is not None:
        # The hash the snapshot was derived from, captured now while
        # `resume.typst_source` still holds the text that produced it.
        # Compared again below, right before the write, against whatever
        # is actually in the database by then. No `preflight`/`metered`
        # here: this is the zero-cost, no-model-call case -- the feature's
        # entire premise -- so it must never be gated behind quota a
        # provider call was never going to touch.
        derived_from = source_hash(resume.typst_source)
    else:
        # Captured *before* the extraction call, into a plain string --
        # not re-read off `resume` afterward. `record_extract_spend` below
        # commits `db`, and a Session's default `expire_on_commit` means
        # the next attribute access on `resume` re-reads from the
        # database: if an edit landed while extraction was in flight
        # (Review Focus 2), that re-read would silently adopt the edit as
        # this snapshot's own basis instead of ever catching it.
        source_for_extraction = resume.typst_source
        guard = preflight(db, user=owner, guest=guest, purpose="extract")
        async with metered(guard) as meter:
            result = await extract_content(source_for_extraction)
            if result.content is not None:
                content = store_content(
                    db, resume, result.content, model=result.model, source=source_for_extraction
                )
                derived_from = source_hash(source_for_extraction)
            # Telemetry fires regardless of outcome, and after
            # store_content so its own commit cannot be the thing that
            # expires `resume` before this task's read of it above.
            record_extract_spend(
                db, user=owner, guest=guest, resume_id=resume.id, result=result, meter=meter
            )
        if result.content is None:
            return _model_path()

    try:
        emit_result = emitter.emit(content)
        # Off the event loop: this handler is `async def`, so a sync call
        # here would otherwise block every concurrent request -- including
        # an in-flight chat SSE stream -- for the length of a subprocess
        # compile. Matches the convention at chat.py's and llm/turn.py's
        # own `asyncio.to_thread` call sites.
        await asyncio.to_thread(compile_typst, emit_result.source, "pdf")
    except HTTPException:
        # A deterministic path that can commit a broken document is worse
        # than the model path it replaces -- write nothing and fall back.
        logger.warning(
            "template switch emit failed to compile resume_id=%s name=%s version=%s",
            resume_id,
            body.name,
            body.version,
        )
        return _model_path()
    except Exception:
        # An emitter raising on an unusual-but-valid ResumeContent must
        # fall back the same way a failed compile does, not 500 -- the
        # brief's own rule ("any error returns the model path") does not
        # carve out an exception for the emitter itself.
        logger.exception(
            "template switch emit raised resume_id=%s name=%s version=%s",
            resume_id,
            body.name,
            body.version,
        )
        return _model_path()

    # Re-read rather than trust the in-session object: a chat edit can land
    # between the freshness check above and this write. Silently clobbering
    # what the person just typed is the worst outcome available here, so a
    # mismatch aborts the write entirely and falls back to the model path.
    db.refresh(resume)
    if source_hash(resume.typst_source) != derived_from:
        return _model_path()

    # coalesce=False, like restore_revision's own whole-document
    # replacement: the default would merge this write into a revision
    # still inside the 120s coalescing window, overwriting the pre-switch
    # Typst in place and leaving nothing in history to undo back to.
    set_draft_source(db, resume, emit_result.source, coalesce=False)
    # The snapshot's content did not change -- it is exactly what `emit()`
    # was built from -- only the resume's own source did. Re-keying it to
    # the document just written (rather than leaving it pointed at the
    # pre-switch text) is what lets a *second* switch, right after this
    # one, reuse the same snapshot instead of paying for another
    # extraction; see resume_content.restamp_content.
    restamp_content(db, resume, source=emit_result.source)
    persist_template_switch(
        db,
        resume.id,
        name=body.name,
        version=body.version,
        dropped=list(emit_result.dropped),
        degraded=list(emit_result.degraded),
    )
    out = _to_out(db, resume)
    db.commit()
    return TemplateSwitchApplied(
        mode="applied",
        dropped=list(emit_result.dropped),
        degraded=list(emit_result.degraded),
        resume=out,
    )


def _require_user_owned(resume: Resume, user: User) -> None:
    if resume.user_id != user.id:
        raise HTTPException(status_code=403, detail="Sign in required to share")


@router.post("/v1/resumes/{resume_id}/publish", response_model=ShareState)
def publish_resume(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    _require_user_owned(resume, user)
    pin_current_draft(db, resume)
    out = build_share_state(db, resume)
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
    return PreviewPages(**compile_preview_pages(source))


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


@router.post("/v1/resumes/{resume_id}/import", status_code=201)
async def start_import(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user, ensure=True)
    if not resume.upload_s3_key:
        raise HTTPException(status_code=400, detail="Resume has no uploaded file to import")
    try:
        run = start_run(db, resume)
    except ImportAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _schedule_import(resume.id, run.id)
    return import_snapshot(db, resume.id)


@router.get("/v1/resumes/{resume_id}/import")
def get_import(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    data = import_snapshot(db, resume.id)
    if data is None:
        raise HTTPException(status_code=404, detail="No import for this resume")
    return data


async def _stream_import_snapshots(resume_id: str):
    """Poll `import_snapshot` on a fresh session every tick, until the run ends.

    `offerfy-api` runs uvicorn with two workers sharing only Postgres, so a
    reconnecting client can land on the worker that isn't running the
    import -- the database is the only shared state. A single session held
    open for the whole connection would also sit in one transaction and keep
    serving its first snapshot forever, which looks exactly like a frozen
    import. So every poll opens, reads, and closes its own session.

    Emits a frame immediately, then one each time `version` changes, and
    always emits one final frame and returns once `status` is `done` or
    `failed` -- a stream that never terminates leaks a connection per reload.

    **A stale run terminates the stream too.** Nothing reaps a run left
    `running` by an API restart mid-import, so its row stays `running` in the
    database indefinitely: without this the loop would poll Postgres every
    400ms forever while the card showed "Import was interrupted", and every
    reload would add another immortal stream. That restart is exactly the case
    the `stale` state exists for, so it is a common path, not an edge one.
    Resume still works: the client re-attaches after `startImport`.

    A stale snapshot is emitted even when `version` has not moved, because a
    dead runner is precisely one that stops bumping it -- the staleness *is*
    the new information, and it is the client's cue to offer Resume.
    """
    factory = get_session_factory()
    last_version: int | None = None
    while True:
        session = factory()
        try:
            data = import_snapshot(session, resume_id)
        finally:
            session.close()
        if data is None:
            return
        if data["version"] != last_version or data["stale"]:
            last_version = data["version"]
            yield sse_frame(data)
        if data["status"] in ("done", "failed") or data["stale"]:
            return
        await asyncio.sleep(IMPORT_POLL_SECONDS)


@router.get("/v1/resumes/{resume_id}/import/stream")
def stream_import(
    resume_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    resume = _owned_resume(resume_id, request, response, db, user)
    if import_snapshot(db, resume.id) is None:
        raise HTTPException(status_code=404, detail="No import for this resume")
    return StreamingResponse(
        _stream_import_snapshots(resume.id), media_type="text/event-stream", headers=SSE_HEADERS
    )
