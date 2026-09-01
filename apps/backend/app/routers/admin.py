from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.deps import load_resume_for_admin, require_admin
from app.models import ChatMessage, GuestSession, JobIngestRun, RateEvent, Resume, User
from app.schemas import AtsReport, PreviewPages
from app.schemas_admin import (
    AdminChatMessage,
    AdminChatMessageList,
    AdminCounts,
    AdminDayPoint,
    AdminHealth,
    AdminOverview,
    AdminRecentResume,
    AdminRecentUser,
    AdminResumeDetail,
    AdminResumeList,
    AdminResumeListItem,
    AdminUserDetail,
    AdminUserList,
    AdminUserListItem,
    AdminUserResume,
)
from app.schemas_jobs import AdminIngestRun, AdminIngestRunList
from app.services.ats import analyze_pdf
from app.services.typst_compile import compile_typst, compile_typst_pages

router = APIRouter()

SERIES_DAYS = 14


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = 50 if limit is None else limit
    off = 0 if offset is None else offset
    if lim < 1 or lim > 100 or off < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    return lim, off


def _owner_kind_label(resume: Resume) -> tuple[str, str, str]:
    if resume.user_id:
        label = resume.user.email if resume.user is not None else resume.user_id
        return "user", resume.user_id, label
    gid = resume.guest_session_id or ""
    return "guest", gid, f"guest:{gid[:8]}"


def _message_counts(db: Session, resume_ids: list[str]) -> dict[str, int]:
    if not resume_ids:
        return {}
    rows = (
        db.query(ChatMessage.resume_id, func.count(ChatMessage.id))
        .filter(ChatMessage.resume_id.in_(resume_ids))
        .group_by(ChatMessage.resume_id)
        .all()
    )
    return {rid: int(n) for rid, n in rows}


def _count(db: Session, model, *filters) -> int:
    q = db.query(func.count()).select_from(model)
    for item in filters:
        q = q.filter(item)
    return int(q.scalar() or 0)


def _utc_day(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def _day_points(db: Session, now: datetime) -> list[AdminDayPoint]:
    today = now.astimezone(timezone.utc).date()
    start_date = today - timedelta(days=SERIES_DAYS - 1)
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    keys = [(start_date + timedelta(days=i)).isoformat() for i in range(SERIES_DAYS)]
    buckets = {
        key: {
            "users": 0,
            "resumes_create": 0,
            "resumes_upload": 0,
            "chats": 0,
            "guest_rate_chat": 0,
            "guest_rate_export": 0,
        }
        for key in keys
    }

    def bump(when: datetime | None, field: str) -> None:
        if when is None:
            return
        key = _utc_day(when)
        if key in buckets:
            buckets[key][field] += 1

    for (created,) in db.query(User.created_at).filter(User.created_at >= start):
        bump(created, "users")
    for created, source in db.query(Resume.created_at, Resume.source).filter(
        Resume.created_at >= start
    ):
        bump(created, "resumes_create" if source == "create" else "resumes_upload")
    for (created,) in db.query(ChatMessage.created_at).filter(ChatMessage.created_at >= start):
        bump(created, "chats")
    for created, kind in db.query(RateEvent.created_at, RateEvent.kind).filter(
        RateEvent.created_at >= start
    ):
        bump(created, "guest_rate_chat" if kind == "chat" else "guest_rate_export")

    return [AdminDayPoint(date=key, **buckets[key]) for key in keys]


def _resume_list_item(resume: Resume, message_count: int) -> AdminResumeListItem:
    kind, owner_id, label = _owner_kind_label(resume)
    created = _iso(resume.created_at) or ""
    return AdminResumeListItem(
        id=resume.id,
        title=resume.title,
        source=resume.source,
        import_status=resume.import_status,
        locale=resume.locale,
        owner_kind=kind,
        owner_id=owner_id,
        owner_label=label,
        claimed_at=_iso(resume.claimed_at),
        created_at=created,
        message_count=message_count,
    )


@router.get("/v1/admin/overview", response_model=AdminOverview)
def admin_overview(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    database = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    user_resume_counts = dict(
        db.query(Resume.user_id, func.count(Resume.id))
        .filter(Resume.user_id.isnot(None))
        .group_by(Resume.user_id)
        .all()
    )
    recent_users = (
        db.query(User).order_by(User.created_at.desc()).limit(10).all()
        if database == "ok"
        else []
    )
    recent_resumes = (
        db.query(Resume)
        .options(joinedload(Resume.user))
        .order_by(Resume.created_at.desc())
        .limit(10)
        .all()
        if database == "ok"
        else []
    )
    resume_msg = _message_counts(db, [row.id for row in recent_resumes])

    counts = AdminCounts(
        users=_count(db, User),
        guest_sessions=_count(db, GuestSession),
        resumes=_count(db, Resume),
        resumes_create=_count(db, Resume, Resume.source == "create"),
        resumes_upload=_count(db, Resume, Resume.source == "upload"),
        resumes_guest=_count(db, Resume, Resume.guest_session_id.isnot(None)),
        resumes_user=_count(db, Resume, Resume.user_id.isnot(None)),
        chat_messages_24h=_count(db, ChatMessage, ChatMessage.created_at >= cutoff_24h),
        chat_messages_7d=_count(db, ChatMessage, ChatMessage.created_at >= cutoff_7d),
        guest_rate_chat_24h=_count(
            db, RateEvent, RateEvent.kind == "chat", RateEvent.created_at >= cutoff_24h
        ),
        guest_rate_export_24h=_count(
            db, RateEvent, RateEvent.kind == "export", RateEvent.created_at >= cutoff_24h
        ),
    )
    return AdminOverview(
        health=AdminHealth(
            api="ok",
            database=database,
            s3_configured=get_settings().s3_configured(),
        ),
        counts=counts,
        series=_day_points(db, now) if database == "ok" else [],
        recent_users=[
            AdminRecentUser(
                id=user.id,
                email=user.email,
                locale=user.locale,
                created_at=_iso(user.created_at) or "",
                resume_count=int(user_resume_counts.get(user.id) or 0),
            )
            for user in recent_users
        ],
        recent_resumes=[
            AdminRecentResume(
                id=row.id,
                title=row.title,
                source=row.source,
                import_status=row.import_status,
                owner_kind=_owner_kind_label(row)[0],
                owner_label=_owner_kind_label(row)[2],
                created_at=_iso(row.created_at) or "",
                message_count=resume_msg.get(row.id, 0),
            )
            for row in recent_resumes
        ],
    )


@router.get("/v1/admin/users", response_model=AdminUserList)
def admin_users(
    q: str | None = None,
    limit: int | None = 50,
    offset: int | None = 0,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    lim, off = _page(limit, offset)
    query = db.query(User)
    if q:
        query = query.filter(User.email.ilike(f"%{q}%"))
    total = query.count()
    rows = query.order_by(User.created_at.desc()).offset(off).limit(lim).all()
    counts = dict(
        db.query(Resume.user_id, func.count(Resume.id))
        .filter(Resume.user_id.in_([row.id for row in rows] or [""]))
        .group_by(Resume.user_id)
        .all()
    )
    return AdminUserList(
        items=[
            AdminUserListItem(
                id=row.id,
                email=row.email,
                locale=row.locale,
                picture=row.picture,
                created_at=_iso(row.created_at) or "",
                resume_count=int(counts.get(row.id) or 0),
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/v1/admin/users/{user_id}", response_model=AdminUserDetail)
def admin_user_detail(
    user_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")
    resumes = (
        db.query(Resume)
        .filter(Resume.user_id == user.id)
        .order_by(Resume.created_at.desc())
        .all()
    )
    msg = _message_counts(db, [row.id for row in resumes])
    return AdminUserDetail(
        id=user.id,
        email=user.email,
        google_sub=user.google_sub,
        locale=user.locale,
        picture=user.picture,
        created_at=_iso(user.created_at) or "",
        resumes=[
            AdminUserResume(
                id=row.id,
                title=row.title,
                source=row.source,
                import_status=row.import_status,
                created_at=_iso(row.created_at) or "",
                message_count=msg.get(row.id, 0),
            )
            for row in resumes
        ],
    )


@router.get("/v1/admin/resumes", response_model=AdminResumeList)
def admin_resumes(
    q: str | None = None,
    owner: str | None = None,
    source: str | None = None,
    limit: int | None = 50,
    offset: int | None = 0,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    if owner is not None and owner not in {"user", "guest"}:
        raise HTTPException(status_code=422, detail="owner must be user or guest")
    if source is not None and source not in {"create", "upload"}:
        raise HTTPException(status_code=422, detail="source must be create or upload")
    lim, off = _page(limit, offset)
    query = db.query(Resume)
    if q:
        query = query.filter(Resume.title.ilike(f"%{q}%"))
    if owner == "user":
        query = query.filter(Resume.user_id.isnot(None))
    elif owner == "guest":
        query = query.filter(Resume.guest_session_id.isnot(None))
    if source:
        query = query.filter(Resume.source == source)
    total = query.count()
    rows = (
        query.options(joinedload(Resume.user))
        .order_by(Resume.created_at.desc())
        .offset(off)
        .limit(lim)
        .all()
    )
    msg = _message_counts(db, [row.id for row in rows])
    return AdminResumeList(
        items=[_resume_list_item(row, msg.get(row.id, 0)) for row in rows],
        total=total,
    )


@router.get("/v1/admin/resumes/{resume_id}", response_model=AdminResumeDetail)
def admin_resume_detail(
    resume_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    resume = load_resume_for_admin(resume_id, db)
    _ = resume.user
    msg = _message_counts(db, [resume.id])
    item = _resume_list_item(resume, msg.get(resume.id, 0))
    return AdminResumeDetail(**item.model_dump(), typst_source=resume.typst_source)


@router.get("/v1/admin/resumes/{resume_id}/messages", response_model=AdminChatMessageList)
def admin_resume_messages(
    resume_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    load_resume_for_admin(resume_id, db)
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.resume_id == resume_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return AdminChatMessageList(
        items=[
            AdminChatMessage(
                id=row.id,
                role=row.role,
                content=row.content,
                created_at=_iso(row.created_at) or "",
            )
            for row in rows
        ]
    )


@router.get("/v1/admin/resumes/{resume_id}/preview", response_model=PreviewPages)
def admin_resume_preview(
    resume_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    resume = load_resume_for_admin(resume_id, db)
    blobs = compile_typst_pages(resume.typst_source, "svg")
    return PreviewPages(pages=[blob.decode("utf-8") for blob in blobs])


@router.get("/v1/admin/resumes/{resume_id}/ats", response_model=AtsReport)
def admin_resume_ats(
    resume_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    resume = load_resume_for_admin(resume_id, db)
    pdf = compile_typst(resume.typst_source, "pdf")
    return analyze_pdf(pdf, resume.typst_source)


@router.get("/v1/admin/jobs/ingest", response_model=AdminIngestRunList)
def admin_jobs_ingest(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    rows = (
        db.query(JobIngestRun)
        .order_by(JobIngestRun.started_at.desc())
        .limit(20)
        .all()
    )
    return AdminIngestRunList(
        items=[
            AdminIngestRun(
                id=row.id,
                source=row.source,
                board_slug=row.board_slug,
                started_at=_iso(row.started_at) or "",
                finished_at=_iso(row.finished_at),
                status=row.status,
                ok_count=row.ok_count,
                error_count=row.error_count,
                upserted_count=row.upserted_count,
                expired_count=row.expired_count,
                error_snippet=row.error_snippet,
            )
            for row in rows
        ]
    )
