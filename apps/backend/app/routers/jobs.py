from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, nulls_last, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job
from app.schemas_jobs import JOB_SOURCES, JobDetail, JobList, JobListItem, JobSitemapItem, JobSitemapPage
from app.services.jobs.spotlight import select_featured

router = APIRouter()

LIST_MAX = 100
SITEMAP_PAGE_SIZE = 1000


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _like_term(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _encode_cursor(posted_at: datetime | None, job_id: str) -> str:
    payload = json.dumps({"t": _iso(posted_at) or "", "i": job_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime | None, str]:
    pad = "=" * ((4 - len(cursor) % 4) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + pad))
        raw = str(data["t"])
        job_id = str(data["i"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    if not raw:
        return None, job_id
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp, job_id


def _posted_at_order():
    return (nulls_last(Job.posted_at.desc()), Job.id.desc())


def _after_cursor(stamp: datetime | None, job_id: str):
    if stamp is None:
        return and_(Job.posted_at.is_(None), Job.id < job_id)
    return or_(
        Job.posted_at < stamp,
        and_(Job.posted_at == stamp, Job.id < job_id),
        Job.posted_at.is_(None),
    )


def _list_item(job: Job) -> JobListItem:
    return JobListItem(
        id=job.id,
        source=job.source,
        company=job.company,
        title=job.title,
        location=job.location,
        remote=job.remote,
        apply_url=job.apply_url,
        source_url=job.source_url,
        posted_at=_iso(job.posted_at),
        last_seen_at=_iso(job.last_seen_at) or "",
        is_active=job.is_active,
    )


def _filtered_query(
    db: Session,
    *,
    q: str | None,
    source: str | None,
    remote: bool | None,
    active: bool | None,
):
    query = db.query(Job)
    if active is None or active:
        query = query.filter(Job.is_active.is_(True))
    elif active is False:
        query = query.filter(Job.is_active.is_(False))
    if source:
        if source not in JOB_SOURCES:
            raise HTTPException(status_code=422, detail="Invalid source")
        query = query.filter(Job.source == source)
    if remote is True:
        query = query.filter(Job.remote.is_(True))
    elif remote is False:
        query = query.filter(Job.remote.is_(False))
    if q:
        term = _like_term(q.strip())
        if term != "%%":
            match = or_(
                Job.title.ilike(term, escape="\\"),
                Job.company.ilike(term, escape="\\"),
                Job.location.ilike(term, escape="\\"),
            )
            query = query.filter(match)
    return query


@router.get("/v1/jobs", response_model=JobList)
def list_jobs(
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    active: bool = True,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=LIST_MAX),
    db: Session = Depends(get_db),
):
    query = _filtered_query(db, q=q, source=source, remote=remote, active=active)
    query = query.order_by(*_posted_at_order())
    if cursor:
        stamp, job_id = _decode_cursor(cursor)
        query = query.filter(_after_cursor(stamp, job_id))
    rows = query.limit(limit + 1).all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode_cursor(last.posted_at, last.id)
        rows = rows[:limit]
    return JobList(items=[_list_item(row) for row in rows], next_cursor=next_cursor)


@router.get("/v1/jobs/sitemap", response_model=JobSitemapPage)
def jobs_sitemap(
    page: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    total = (
        db.query(func.count())
        .select_from(Job)
        .filter(Job.is_active.is_(True))
        .scalar()
        or 0
    )
    total_pages = max(1, (int(total) + SITEMAP_PAGE_SIZE - 1) // SITEMAP_PAGE_SIZE) if total else 0
    rows = (
        db.query(Job)
        .filter(Job.is_active.is_(True))
        .order_by(Job.last_seen_at.desc(), Job.id.desc())
        .offset(page * SITEMAP_PAGE_SIZE)
        .limit(SITEMAP_PAGE_SIZE)
        .all()
    )
    return JobSitemapPage(
        page=page,
        page_size=SITEMAP_PAGE_SIZE,
        total_pages=total_pages,
        items=[
            JobSitemapItem(id=row.id, last_seen_at=_iso(row.last_seen_at) or "")
            for row in rows
        ],
    )


@router.get("/v1/jobs/featured", response_model=JobList)
def featured_jobs(db: Session = Depends(get_db)):
    rows = select_featured(db)
    return JobList(items=[_list_item(row) for row in rows], next_cursor=None)


@router.get("/v1/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Not found")
    return JobDetail(
        **_list_item(job).model_dump(),
        description_html=job.description_html,
        description_text=job.description_text,
        first_seen_at=_iso(job.first_seen_at) or "",
    )
