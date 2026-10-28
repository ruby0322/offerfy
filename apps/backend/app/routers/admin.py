from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import case, func, text
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.deps import load_resume_for_admin, require_admin
from app.models import (
    ChatMessage,
    DisclosureEvent,
    GuestSession,
    Job,
    JobIngestRun,
    LlmCall,
    QuotaGrant,
    RateEvent,
    Resume,
    TalentApiKey,
    UsageCounter,
    UsageRefusal,
    User,
)
from app.schemas import AtsReport, PreviewPages
from app.schemas_admin import (
    AdminChatMessage,
    AdminChatMessageList,
    AdminCounts,
    AdminDayPoint,
    AdminDisclosure,
    AdminDisclosureList,
    AdminHealth,
    AdminOverview,
    AdminQuotaGrant,
    AdminQuotaGrantCreate,
    AdminRecentResume,
    AdminRecentUser,
    AdminResumeDetail,
    AdminResumeList,
    AdminResumeListItem,
    AdminStopUseNotice,
    AdminStopUseNoticeCreate,
    AdminTalentKey,
    AdminTalentKeyCreate,
    AdminTalentKeyCreated,
    AdminTalentKeyList,
    AdminTalentObligation,
    AdminTalentObligationList,
    AdminUsageCeiling,
    AdminUsageGap,
    AdminUsageOverview,
    AdminUsageRefusal,
    AdminUsageSubject,
    AdminUsageTier,
    AdminUserDetail,
    AdminUserList,
    AdminUserListItem,
    AdminUserResume,
    AdminUserUsage,
)
from app.schemas_jobs import (
    AdminIngestBoardFailure,
    AdminIngestHealth,
    AdminIngestOverview,
    AdminIngestPass,
    AdminIngestRun,
    AdminIngestRunList,
)
from app.services.ats import analyze_pdf
from app.services.disclosure import pending_obligations, record_stop_use_notice
from app.services.grants import active_grants, all_grants, issue_grant, revoke_grant
from app.services.jobs.ingest import refreshed_nothing
from app.services.jobs.store import JOB_FRESHNESS_DAYS, freshness_cutoff
from app.services.talent_keys import mint_talent_key
from app.services.typst_compile import compile_typst, compile_typst_pages
from app.services import usage as usage_service
from app.services.usage import (
    CREDIT_UUSD,
    TIERS,
    limits_enabled,
    reconcile,
    refusals_by,
    round_cost_uusd,
    window_end,
    window_start,
)

router = APIRouter()

SERIES_DAYS = 14
INGEST_PASS_LIMIT = 20
# The cadence ops/systemd/offerfy-ingest.timer actually runs at. Kept here so
# the dashboard can say "overdue" rather than only "old".
INGEST_EXPECTED_INTERVAL_HOURS = 6
# One missed slot, with room for the timer's 10-minute jitter. Deliberately
# tight: this went unnoticed for 54 days once.
INGEST_OVERDUE_HOURS = 7
# A pass writes one row per board; ~119 today, so this holds a whole pass with
# headroom. The old endpoint capped at 20 and could not show even one.
INGEST_BOARD_LIMIT = 500


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _grant_out(db: Session, grant: QuotaGrant, *, active: bool) -> AdminQuotaGrant:
    issuer = None
    if grant.issued_by_user_id is not None:
        row = db.get(User, grant.issued_by_user_id)
        issuer = None if row is None else row.email
    return AdminQuotaGrant(
        id=grant.id,
        credits=grant.credits,
        reason=grant.reason,
        detail=grant.detail,
        starts_at=_iso(grant.starts_at) or "",
        expires_at=_iso(grant.expires_at),
        revoked_at=_iso(grant.revoked_at),
        issued_by=issuer,
        created_at=_iso(grant.created_at) or "",
        active=active,
    )


def _parse_expiry(raw: str | None) -> datetime | None:
    """ISO 8601 in, aware UTC out. A naive string is read as UTC rather than
    rejected: the admin form sends a local-looking datetime, and guessing the
    server's zone would be worse than naming one.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="expires_at must be ISO 8601")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _utc(dt: datetime | None) -> datetime | None:
    """Normalise to aware UTC. SQLite hands back naive datetimes in tests, and
    subtracting one of those from an aware now() raises."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_day(dt: datetime) -> str:
    normalised = _utc(dt)
    assert normalised is not None
    return normalised.date().isoformat()


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
            "llm_calls": 0,
            "llm_input_tokens": 0,
            "llm_cached_tokens": 0,
            "llm_output_tokens": 0,
            "llm_reasoning_tokens": 0,
            "llm_cache_write_tokens": 0,
            "llm_cost_uusd": 0,
            "llm_unmeasured_calls": 0,
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
    # The hit rate is read off a narrower set of rows than the totals, so it
    # gets its own pair of running sums. Only a call that reported an input
    # count *and* a cached count says anything about caching: a row with input
    # tokens and a null `cached_tokens` -- every vision call, and any proxy
    # that drops `input_tokens_details` -- would otherwise read as a 0% hit on
    # tokens whose cache status was never measured.
    rate_inputs = {key: 0 for key in keys}
    rate_cached = {key: 0 for key in keys}

    # Tokens are sums, not counts, so these rows cannot go through bump(). A
    # null column means the provider did not report that number; it adds zero
    # to the total while the call itself still counts.
    # Whole rows rather than columns, because the cost comes from
    # app.services.usage.round_cost_uusd -- the same function enforcement and
    # the reconciliation use, so a figure here can never disagree with the one
    # a person was limited by.
    for row in db.query(LlmCall).filter(LlmCall.created_at >= start):
        created, purpose = row.created_at, row.purpose
        inputs, cached = row.input_tokens, row.cached_tokens
        outputs, reasoning = row.output_tokens, row.reasoning_tokens
        if created is None:
            continue
        key = _utc_day(created)
        bucket = buckets.get(key)
        if bucket is None:
            continue
        bucket["llm_calls"] += 1
        bucket["llm_input_tokens"] += int(inputs or 0)
        bucket["llm_cached_tokens"] += int(cached or 0)
        bucket["llm_output_tokens"] += int(outputs or 0)
        bucket["llm_reasoning_tokens"] += int(reasoning or 0)
        bucket["llm_cache_write_tokens"] += int(row.cache_write_tokens or 0)
        cost, measured = round_cost_uusd(row)
        bucket["llm_cost_uusd"] += cost
        if not measured:
            bucket["llm_unmeasured_calls"] += 1
        # The decisions endpoint reports input tokens only, so a vision row
        # can never contribute a cached count -- it stays out of the rate even
        # if some proxy starts reporting one.
        if purpose != "vision" and inputs is not None and cached is not None:
            rate_inputs[key] += int(inputs)
            rate_cached[key] += int(cached)

    points: list[AdminDayPoint] = []
    for key in keys:
        measured = rate_inputs[key]
        rate = round(rate_cached[key] / measured, 4) if measured else None
        points.append(AdminDayPoint(date=key, llm_cache_hit_rate=rate, **buckets[key]))
    return points


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
    order: str | None = "created",
    limit: int | None = 50,
    offset: int | None = 0,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """The user list, orderable by this window's spend.

    `order='usage'` is an OUTER join to usage_counters, not an inner one: a
    user who has spent nothing this week has no counter row, and dropping them
    from the list would make "sort by usage" quietly also mean "hide everyone
    who was idle".
    """
    lim, off = _page(limit, offset)
    start = window_start()
    query = db.query(User)
    if q:
        query = query.filter(User.email.ilike(f"%{q}%"))
    total = query.count()
    if (order or "").strip() == "usage":
        spend = (
            db.query(UsageCounter.subject_id.label("sid"), UsageCounter.uusd.label("uusd"))
            .filter(
                UsageCounter.window_start == start,
                UsageCounter.subject_kind == "user",
            )
            .subquery()
        )
        rows = (
            query.outerjoin(spend, spend.c.sid == User.id)
            .order_by(func.coalesce(spend.c.uusd, 0).desc(), User.created_at.desc())
            .offset(off)
            .limit(lim)
            .all()
        )
    else:
        rows = query.order_by(User.created_at.desc()).offset(off).limit(lim).all()
    counts = dict(
        db.query(Resume.user_id, func.count(Resume.id))
        .filter(Resume.user_id.in_([row.id for row in rows] or [""]))
        .group_by(Resume.user_id)
        .all()
    )
    spent = dict(
        db.query(UsageCounter.subject_id, UsageCounter.uusd)
        .filter(
            UsageCounter.window_start == start,
            UsageCounter.subject_kind == "user",
            UsageCounter.subject_id.in_([row.id for row in rows] or [""]),
        )
        .all()
    )
    turns = dict(
        db.query(UsageCounter.subject_id, UsageCounter.turns)
        .filter(
            UsageCounter.window_start == start,
            UsageCounter.subject_kind == "user",
            UsageCounter.subject_id.in_([row.id for row in rows] or [""]),
        )
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
                tier=usage_service.tier_of(db, row, None),
                credits_used=round(int(spent.get(row.id) or 0) / CREDIT_UUSD, 2),
                turns_used=int(turns.get(row.id) or 0),
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
    bal = usage_service.balance(db, user, None)
    lifetime_uusd = 0
    lifetime_turns = 0
    for row in db.query(LlmCall).filter(LlmCall.user_id == user.id):
        cost, _ = round_cost_uusd(row)
        lifetime_uusd += cost
        if row.purpose == "chat" and row.round_index == 0:
            lifetime_turns += 1
    refusals = (
        db.query(func.count(UsageRefusal.id))
        .filter(
            UsageRefusal.subject_kind == "user",
            UsageRefusal.subject_id == user.id,
            UsageRefusal.created_at >= window_start(),
        )
        .scalar()
        or 0
    )
    active_ids = {row.id for row in active_grants(db, user=user)}
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
        usage=AdminUserUsage(
            tier=bal.tier,
            credits_used=bal.used_credits,
            limit_credits=bal.limit_credits,
            turns_used=bal.turns_used,
            turn_limit=bal.turn_limit,
            remaining_credits=bal.remaining_credits,
            tier_credits=bal.tier_credits,
            granted_credits=bal.granted_credits,
            reset_at=_iso(bal.reset_at) or "",
            refusals=int(refusals),
            unmeasured_rounds=bal.unmeasured_rounds,
            lifetime_uusd=lifetime_uusd,
            lifetime_turns=lifetime_turns,
        ),
        grants=[
            _grant_out(db, row, active=row.id in active_ids)
            for row in all_grants(db, user=user)
        ],
    )


@router.post(
    "/v1/admin/users/{user_id}/grants", response_model=AdminQuotaGrant, status_code=201
)
def admin_issue_grant(
    user_id: str,
    body: AdminQuotaGrantCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Raise one person's weekly ceiling without a deploy.

    No endpoint to *edit* a grant: changing the credits on a live one would
    rewrite what someone's ceiling was last week. Revoke and issue instead, so
    the history stays readable.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")
    expires_at = _parse_expiry(body.expires_at)
    try:
        grant = issue_grant(
            db,
            user=user,
            credits=body.credits,
            reason=body.reason,
            detail=body.detail,
            issued_by=admin,
            expires_at=expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.flush()
    active_ids = {row.id for row in active_grants(db, user=user)}
    return _grant_out(db, grant, active=grant.id in active_ids)


@router.delete(
    "/v1/admin/users/{user_id}/grants/{grant_id}", response_model=AdminQuotaGrant
)
def admin_revoke_grant(
    user_id: str,
    grant_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Revoke, never delete -- see models.QuotaGrant. Takes effect at once
    rather than at the window boundary, which is the one place a ceiling is
    allowed to move down mid-week: this is the lever for abuse.
    """
    grant = db.get(QuotaGrant, grant_id)
    if grant is None or grant.user_id != user_id:
        raise HTTPException(status_code=404, detail="Not found")
    revoke_grant(db, grant=grant)
    db.flush()
    return _grant_out(db, grant, active=False)


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


def _ingest_row(row: JobIngestRun) -> AdminIngestRun:
    return AdminIngestRun(
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


def _ingest_passes(db: Session, limit: int) -> list[AdminIngestPass]:
    """Fold the per-board rows of each pass into one row.

    Grouped in SQL rather than bucketed in Python: this table gains ~119 rows
    per pass, roughly 480 a day, so streaming it all into a dict would not age
    well. Rows with no run_id predate migration 014 and have no pass to belong
    to; they are counted separately instead of guessed at.
    """
    groups = (
        db.query(
            JobIngestRun.run_id.label("run_id"),
            func.min(JobIngestRun.started_at).label("started_at"),
            func.max(JobIngestRun.finished_at).label("finished_at"),
            func.sum(case((JobIngestRun.status == "ok", 1), else_=0)).label("sources_ok"),
            func.sum(case((JobIngestRun.status != "ok", 1), else_=0)).label("sources_failed"),
            func.sum(JobIngestRun.ok_count).label("fetched"),
            func.sum(JobIngestRun.upserted_count).label("upserted"),
            func.sum(JobIngestRun.expired_count).label("expired"),
        )
        .filter(JobIngestRun.run_id.isnot(None))
        .group_by(JobIngestRun.run_id)
        .order_by(func.min(JobIngestRun.started_at).desc())
        .limit(limit)
        .all()
    )
    if not groups:
        return []

    run_ids = [group.run_id for group in groups]
    failures: dict[str, list[AdminIngestBoardFailure]] = {run_id: [] for run_id in run_ids}
    failing_rows = (
        db.query(JobIngestRun)
        .filter(JobIngestRun.run_id.in_(run_ids), JobIngestRun.status != "ok")
        .order_by(JobIngestRun.source, JobIngestRun.board_slug)
        .all()
    )
    for row in failing_rows:
        failures[row.run_id].append(
            AdminIngestBoardFailure(
                source=row.source,
                board_slug=row.board_slug,
                error_snippet=row.error_snippet,
            )
        )

    passes: list[AdminIngestPass] = []
    for group in groups:
        sources_ok = int(group.sources_ok or 0)
        sources_failed = int(group.sources_failed or 0)
        upserted = int(group.upserted or 0)
        started = _utc(group.started_at)
        finished = _utc(group.finished_at)
        duration = (finished - started).total_seconds() if started and finished else None
        if refreshed_nothing(sources_ok=sources_ok, upserted=upserted):
            status = "failed"
        elif sources_failed:
            status = "degraded"
        else:
            status = "ok"
        passes.append(
            AdminIngestPass(
                run_id=group.run_id,
                started_at=_iso(started) or "",
                finished_at=_iso(finished),
                duration_seconds=duration,
                sources_ok=sources_ok,
                sources_failed=sources_failed,
                fetched=int(group.fetched or 0),
                upserted=upserted,
                expired=int(group.expired or 0),
                status=status,
                failing_boards=failures[group.run_id],
            )
        )
    return passes


def _ingest_health(db: Session, now: datetime, passes: list[AdminIngestPass]) -> AdminIngestHealth:
    cutoff = freshness_cutoff(now)
    freshest = _utc(
        db.query(func.max(Job.last_seen_at)).filter(Job.is_active.is_(True)).scalar()
    )
    age_hours = (now - freshest).total_seconds() / 3600 if freshest else None
    fresh_count = _count(db, Job, Job.is_active.is_(True), Job.last_seen_at >= cutoff)
    stale_count = _count(db, Job, Job.is_active.is_(True), Job.last_seen_at < cutoff)
    # "failed" only, not "degraded". 63 of 118 boards fail on every single pass
    # (companies that never used these ATS providers), so counting degraded here
    # would pin a permanent red marker to a dashboard whose whole job is to be
    # believed when it goes red.
    last_failure = next((item.started_at for item in passes if item.status == "failed"), None)
    return AdminIngestHealth(
        freshest_last_seen_at=_iso(freshest),
        catalog_age_hours=age_hours,
        fresh_job_count=fresh_count,
        stale_job_count=stale_count,
        freshness_days=JOB_FRESHNESS_DAYS,
        expected_interval_hours=INGEST_EXPECTED_INTERVAL_HOURS,
        # No catalog at all counts as overdue: there is nothing to serve.
        overdue=age_hours is None or age_hours > INGEST_OVERDUE_HOURS,
        last_failure_at=last_failure,
    )


@router.get("/v1/admin/jobs/ingest", response_model=AdminIngestOverview)
def admin_jobs_ingest(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    passes = _ingest_passes(db, INGEST_PASS_LIMIT)
    legacy = _count(db, JobIngestRun, JobIngestRun.run_id.is_(None))
    return AdminIngestOverview(
        health=_ingest_health(db, datetime.now(timezone.utc), passes),
        passes=passes,
        legacy_row_count=legacy,
    )


@router.get("/v1/admin/jobs/ingest/{run_id}", response_model=AdminIngestRunList)
def admin_jobs_ingest_pass(
    run_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Every per-board row of one pass, for drilling into a failure."""
    rows = (
        db.query(JobIngestRun)
        .filter(JobIngestRun.run_id == run_id)
        .order_by(JobIngestRun.started_at.asc())
        .limit(INGEST_BOARD_LIMIT)
        .all()
    )
    return AdminIngestRunList(items=[_ingest_row(row) for row in rows], limit=INGEST_BOARD_LIMIT)

# --- talent introduction: the obligation side -------------------------------
#
# include_in_schema=False on every route below. create_app() serves
# /openapi.json, /docs and /redoc, and the Next rewrite publishes them at
# offerfy.cc/api/openapi.json -- so a route that appears there is a route on
# the open web, whatever it requires to call. These describe who holds whose
# resume data, and the key-minting ones describe how to get a credential.


@router.get(
    "/v1/admin/talent/obligations",
    response_model=AdminTalentObligationList,
    include_in_schema=False,
)
def admin_talent_obligations(
    limit: int | None = None,
    offset: int | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Everyone we still owe a stop-use notice, newest withdrawal first.

    This is the operator's half of "we require anyone who already has it to
    stop using it". There is no mailer in this codebase, so the queue exists
    to be worked by hand and the discharge recorded.
    """
    lim, off = _page(limit, offset)
    rows = pending_obligations(db)
    page = rows[off : off + lim]
    return AdminTalentObligationList(
        items=[
            AdminTalentObligation(
                user_id=row["user_id"],
                user_email=row["user_email"],
                withdrawn_at=row["withdrawn_at"].isoformat(),
                consent_event_id=row["consent_event_id"],
                api_key_id=row["api_key_id"],
                recipient_label=row["recipient_label"],
                recipient_contact=row["recipient_contact"],
                first_disclosed_at=row["first_disclosed_at"].isoformat(),
                last_disclosed_at=row["last_disclosed_at"].isoformat(),
                disclosure_count=row["disclosure_count"],
            )
            for row in page
        ],
        total=len(rows),
    )


@router.post(
    "/v1/admin/talent/obligations/{user_id}/notices",
    response_model=AdminStopUseNotice,
    include_in_schema=False,
)
def admin_record_stop_use_notice(
    user_id: str,
    body: AdminStopUseNoticeCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Record that this recipient was told to stop. Appends, never updates."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Not found")
    notice = record_stop_use_notice(
        db,
        user=user,
        recipient_contact=body.recipient_contact,
        sent_by=admin.email,
        api_key_id=body.api_key_id,
        detail=body.detail,
    )
    db.commit()
    return AdminStopUseNotice(
        id=notice.id,
        user_id=notice.user_id,
        recipient_contact=notice.recipient_contact,
        channel=notice.channel,
        sent_by=notice.sent_by,
        detail=notice.detail,
        created_at=notice.created_at.isoformat(),
    )


@router.get(
    "/v1/admin/talent/disclosures",
    response_model=AdminDisclosureList,
    include_in_schema=False,
)
def admin_talent_disclosures(
    user_id: str | None = None,
    api_key_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    lim, off = _page(limit, offset)
    query = db.query(DisclosureEvent)
    if user_id:
        query = query.filter(DisclosureEvent.user_id == user_id)
    if api_key_id:
        query = query.filter(DisclosureEvent.api_key_id == api_key_id)
    total = query.count()
    rows = (
        query.order_by(DisclosureEvent.created_at.desc(), DisclosureEvent.id.desc())
        .limit(lim)
        .offset(off)
        .all()
    )
    return AdminDisclosureList(
        items=[
            AdminDisclosure(
                id=row.id,
                user_id=row.user_id,
                api_key_id=row.api_key_id,
                recipient_label=row.recipient_label,
                recipient_contact=row.recipient_contact,
                scope=row.scope,
                profile_content_hash=row.profile_content_hash,
                policy_version=row.policy_version,
                route=row.route,
                created_at=row.created_at.isoformat(),
            )
            for row in rows
        ],
        total=total,
    )


def _talent_key_out(key: TalentApiKey, disclosures: int = 0) -> AdminTalentKey:
    return AdminTalentKey(
        id=key.id,
        label=key.label,
        contact=key.contact,
        created_by=key.created_by,
        created_at=key.created_at.isoformat(),
        expires_at=_iso(key.expires_at),
        revoked_at=_iso(key.revoked_at),
        last_used_at=_iso(key.last_used_at),
        disclosure_count=disclosures,
    )


@router.post(
    "/v1/admin/talent/keys",
    response_model=AdminTalentKeyCreated,
    include_in_schema=False,
)
def admin_mint_talent_key(
    body: AdminTalentKeyCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Issue one credential to one named organisation.

    The raw token is in this response and nowhere else, ever: only its hash is
    stored. Issuing is deliberately here and not self-serve.
    """
    label = (body.label or "").strip()
    contact = (body.contact or "").strip()
    if not label or not contact:
        raise HTTPException(status_code=422, detail="label and contact are required")
    if body.expires_in_days is not None and body.expires_in_days < 1:
        raise HTTPException(status_code=422, detail="expires_in_days must be positive")
    key, raw = mint_talent_key(
        db,
        label=label,
        contact=contact,
        created_by=admin.email,
        expires_in_days=body.expires_in_days,
    )
    db.commit()
    out = _talent_key_out(key)
    return AdminTalentKeyCreated(**out.model_dump(), token=raw)


@router.get(
    "/v1/admin/talent/keys",
    response_model=AdminTalentKeyList,
    include_in_schema=False,
)
def admin_list_talent_keys(
    db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    keys = (
        db.query(TalentApiKey)
        .order_by(TalentApiKey.created_at.desc(), TalentApiKey.id.desc())
        .all()
    )
    counts = dict(
        db.query(DisclosureEvent.api_key_id, func.count(DisclosureEvent.id))
        .group_by(DisclosureEvent.api_key_id)
        .all()
    )
    return AdminTalentKeyList(
        items=[_talent_key_out(key, counts.get(key.id, 0)) for key in keys],
        total=len(keys),
    )


@router.delete(
    "/v1/admin/talent/keys/{key_id}", status_code=204, include_in_schema=False
)
def admin_revoke_talent_key(
    key_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Revoke, never delete.

    disclosure_events.api_key_id has to keep pointing somewhere, and a revoked
    key's past disclosures are still obligations we owe.
    """
    key = db.get(TalentApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------
# usage (Task 9)
# --------------------------------------------------------------------------


def _subject_emails(db: Session, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {
        row.id: row.email
        for row in db.query(User).filter(User.id.in_(ids)).all()
    }


def _tier_of_subject(db: Session, kind: str, subject_id: str) -> str:
    """The tier a counter row belongs to.

    A guest or network counter is the guest tier by definition. For a user it
    is read live from the consent ledger through services.usage, the same way
    enforcement reads it, so this view cannot disagree with what they were
    actually limited by.
    """
    if kind != "user":
        return usage_service.GUEST
    user = db.get(User, subject_id)
    return usage_service.tier_of(db, user, None) if user else usage_service.REGISTERED


def _usage_subjects(db: Session, *, start: datetime, limit: int) -> list[AdminUsageSubject]:
    """Who spent what this window, most expensive first.

    Read off usage_counters rather than aggregated from llm_calls, because the
    counters are what the limit is enforced against -- a view built on the
    telemetry would show a number nobody was held to.
    """
    rows = (
        db.query(UsageCounter)
        .filter(UsageCounter.window_start == start)
        .order_by(UsageCounter.uusd.desc())
        .limit(limit)
        .all()
    )
    emails = _subject_emails(
        db, [r.subject_id for r in rows if r.subject_kind == "user"]
    )
    refusal_counts = dict(
        db.query(UsageRefusal.subject_id, func.count(UsageRefusal.id))
        .filter(UsageRefusal.created_at >= start)
        .group_by(UsageRefusal.subject_id)
        .all()
    )
    out: list[AdminUsageSubject] = []
    for row in rows:
        tier_name = _tier_of_subject(db, row.subject_kind, row.subject_id)
        tier = TIERS[tier_name]
        limit_uusd = tier.credits * CREDIT_UUSD if tier.credits is not None else None
        over = bool(
            (limit_uusd is not None and row.uusd >= limit_uusd)
            or (tier.turns is not None and row.turns >= tier.turns)
        )
        out.append(
            AdminUsageSubject(
                subject_kind=row.subject_kind,
                subject_id=row.subject_id,
                email=emails.get(row.subject_id),
                tier=tier_name,
                credits_used=round(row.uusd / CREDIT_UUSD, 2),
                uusd_used=int(row.uusd),
                turns_used=int(row.turns),
                refusals=int(refusal_counts.get(row.subject_id) or 0),
                unmeasured_rounds=int(row.unmeasured),
                limit_credits=float(tier.credits) if tier.credits is not None else None,
                turn_limit=tier.turns,
                over_limit=over,
            )
        )
    return out


@router.get("/v1/admin/usage", response_model=AdminUsageOverview)
def admin_usage(
    top: int | None = 25,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """What AI editing is costing, and who is costing it.

    Everything here is priced through services.usage, so no figure on this page
    can disagree with the figure someone was limited by.
    """
    start, end = window_start(), window_end()
    top_n = max(1, min(int(top or 25), 200))
    subjects = _usage_subjects(db, start=start, limit=top_n)

    settings = get_settings()
    totals = dict(
        db.query(UsageCounter.subject_kind, func.coalesce(func.sum(UsageCounter.uusd), 0))
        .filter(UsageCounter.window_start == start)
        .group_by(UsageCounter.subject_kind)
        .all()
    )
    everyone = sum(int(v) for v in totals.values())
    # 'network' rides alongside its guest session, so counting both would
    # double the guest pool.
    guests = int(totals.get("guest") or 0)

    def ceiling(name: str, spent: int, raw: int | None) -> AdminUsageCeiling:
        cap = int(raw) if raw and raw > 0 else None
        return AdminUsageCeiling(
            name=name,
            spent_uusd=spent,
            ceiling_uusd=cap,
            share=round(spent / cap, 4) if cap else None,
        )

    # Every tier appears even at zero, so an empty row reads as "nobody on this
    # tier spent anything" rather than as a missing tier.
    by_tier_acc = {name: [0, 0, 0, 0] for name in TIERS}
    for subject in subjects:
        acc = by_tier_acc[subject.tier]
        acc[0] += 1
        acc[1] += subject.uusd_used
        acc[2] += subject.turns_used
    by_tier = [
        AdminUsageTier(
            tier=name,
            subjects=acc[0],
            uusd_used=acc[1],
            credits_used=round(acc[1] / CREDIT_UUSD, 2),
            turns_used=acc[2],
        )
        for name, acc in by_tier_acc.items()
    ]

    purpose_cost: dict[str, int] = {}
    for row in db.query(LlmCall).filter(LlmCall.created_at >= start):
        cost, _ = round_cost_uusd(row)
        purpose_cost[row.purpose] = purpose_cost.get(row.purpose, 0) + cost

    return AdminUsageOverview(
        window_start=_iso(start) or "",
        window_end=_iso(end) or "",
        enforced=limits_enabled(),
        credit_uusd=CREDIT_UUSD,
        ceilings=[
            ceiling("global", everyone, settings.global_weekly_ceiling_uusd),
            ceiling("guest_pool", guests, settings.guest_weekly_ceiling_uusd),
        ],
        by_tier=by_tier,
        top_subjects=subjects,
        refusals=[
            AdminUsageRefusal(tier=t, limit_kind=k, count=n)
            for t, k, n in refusals_by(db, since=start)
        ],
        gaps=[
            AdminUsageGap(
                subject_kind=k, subject_id=i, counter_uusd=c, llm_call_uusd=l
            )
            for k, i, c, l in reconcile(db, window_start=start)
        ],
        by_purpose=sorted(purpose_cost.items(), key=lambda kv: -kv[1]),
    )
