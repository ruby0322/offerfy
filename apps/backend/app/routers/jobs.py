from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, nulls_last, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job, JobFacets
from app.schemas_jobs import (
    JOB_SOURCES,
    JobDetail,
    JobFacetOption,
    JobFacetOptions,
    JobList,
    JobListItem,
    JobSitemapItem,
    JobSitemapPage,
)
from app.services.jobs.enrich import Taxonomy, default_taxonomy
from app.services.jobs.spotlight import select_featured
from app.services.jobs.store import freshness_cutoff

router = APIRouter()

LIST_MAX = 100
SITEMAP_PAGE_SIZE = 1000

def _sitemap_conditions():
    """Rows eligible for the jobs sitemap.

    A row past the freshness window is noindex on the frontend, so listing it
    here would only hand Google a sitemap full of URLs it has been told to
    drop. Both the count and the page query use this so they cannot drift.
    """
    return (Job.is_active.is_(True), Job.last_seen_at >= freshness_cutoff())


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


def _valid_seniorities(taxonomy: Taxonomy) -> set[str]:
    # "unknown" is a real value enrich.py writes when no rule matches a
    # title at all -- it is not a sentinel to exclude from the vocabulary.
    return {rule.seniority for rule in taxonomy.seniority_rules} | {"unknown"}


def _valid_functions(taxonomy: Taxonomy) -> set[str]:
    # Same reasoning as _valid_seniorities: "other" is a real bucket.
    return {rule.function for rule in taxonomy.function_rules} | {"other"}


def _valid_regions(taxonomy: Taxonomy) -> set[str]:
    return set(taxonomy.location_regions.values())


def _valid_countries(taxonomy: Taxonomy) -> set[str]:
    # JobFacets.country can only ever hold one of these: location_facets
    # (enrich.py) sources every country it emits from this same needle
    # table, so the set is closed, not open, exactly like location_regions.
    return set(taxonomy.location_countries.values())


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
    country: str | None = None,
    region: str | None = None,
    seniority: str | None = None,
    function: str | None = None,
    posted_within_days: int | None = None,
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

    # JobFacets is only joined when a facet filter is actually requested.
    # Unconditionally joining it would wrongly drop every posting with no
    # facets row at all (the common case -- ~11k live postings have none
    # yet, and some Workday postings never will) even on a facet-free
    # request, which is what test_unfiltered_list_still_returns_jobs_with_no_facets_row
    # and test_facet_filter_excludes_jobs_with_no_facets_row together pin.
    # The join is also deliberately INNER rather than LEFT: an unenriched
    # posting genuinely does not match e.g. seniority=mid, so excluding it
    # is the intended behavior, not an accident of SQL's NULL semantics --
    # every filter below is an equality comparison, so a LEFT join would
    # already exclude the unmatched row too (NULL == value is NULL, not
    # true); INNER just says that intent directly instead of relying on
    # that to keep covering for it.
    if seniority or function or country or region:
        query = query.join(JobFacets, JobFacets.job_id == Job.id)
        taxonomy = default_taxonomy()
        if seniority:
            if seniority not in _valid_seniorities(taxonomy):
                raise HTTPException(status_code=422, detail="Invalid seniority")
            query = query.filter(JobFacets.seniority == seniority)
        if function:
            if function not in _valid_functions(taxonomy):
                raise HTTPException(status_code=422, detail="Invalid function")
            query = query.filter(JobFacets.function == function)
        if region:
            if region not in _valid_regions(taxonomy):
                raise HTTPException(status_code=422, detail="Invalid region")
            query = query.filter(JobFacets.region == region)
        if country:
            if country not in _valid_countries(taxonomy):
                raise HTTPException(status_code=422, detail="Invalid country")
            query = query.filter(JobFacets.country == country)

    if posted_within_days is not None:
        # Decision: posted_at IS NULL cannot be confirmed within any window,
        # so it is excluded here, unlike _posted_at_order's nulls_last which
        # still displays it (at the end) when no age filter is active.
        cutoff = datetime.now(timezone.utc) - timedelta(days=posted_within_days)
        query = query.filter(Job.posted_at >= cutoff)

    return query


@router.get("/v1/jobs", response_model=JobList)
def list_jobs(
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    active: bool = True,
    country: str | None = None,
    region: str | None = None,
    seniority: str | None = None,
    function: str | None = None,
    posted_within_days: int | None = Query(default=None, ge=1, le=90),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=LIST_MAX),
    db: Session = Depends(get_db),
):
    query = _filtered_query(
        db,
        q=q,
        source=source,
        remote=remote,
        active=active,
        country=country,
        region=region,
        seniority=seniority,
        function=function,
        posted_within_days=posted_within_days,
    )
    # Counted over the filtered set before ordering and cursoring -- the
    # total describes every matching row, not just the page about to be
    # returned.
    total = query.with_entities(func.count(Job.id)).scalar() or 0
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
    return JobList(items=[_list_item(row) for row in rows], next_cursor=next_cursor, total=total)


@router.get("/v1/jobs/sitemap", response_model=JobSitemapPage)
def jobs_sitemap(
    page: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    conditions = _sitemap_conditions()
    total = (
        db.query(func.count())
        .select_from(Job)
        .filter(*conditions)
        .scalar()
        or 0
    )
    total_pages = max(1, (int(total) + SITEMAP_PAGE_SIZE - 1) // SITEMAP_PAGE_SIZE) if total else 0
    rows = (
        db.query(Job)
        .filter(*conditions)
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
    # total must be passed explicitly: JobList.total defaults to 0, and
    # featured_jobs never pages, so leaving it unset would report 0 for a
    # non-empty list of featured rows.
    return JobList(items=[_list_item(row) for row in rows], next_cursor=None, total=len(rows))


@router.get("/v1/jobs/facets", response_model=JobFacetOptions)
def jobs_facets(db: Session = Depends(get_db)):
    """Facet option counts for the catalog's filter UI.

    Declared above GET /v1/jobs/{job_id} -- same ordering requirement as
    /v1/jobs/sitemap and /v1/jobs/featured, or "facets" is captured as a
    job id.

    Counts over `is_active` alone -- deliberately the same condition GET
    /v1/jobs filters on, and deliberately *not* _sitemap_conditions().
    A dropdown's job is to describe the list it filters, so it has to
    share that list's scope or its counts do not sum to the list's total.
    The freshness window drives noindex and validThrough, which govern
    crawlers, not visitors: a stale-but-active posting is still perfectly
    reachable on /jobs. Counting over the window would also mean that an
    ingest outage longer than JOB_FRESHNESS_DAYS emptied every dropdown
    while the list still showed postings. The sitemap keeps
    _sitemap_conditions() because for a sitemap the crawler *is* the
    audience.

    The join to JobFacets is implicitly INNER (an aggregate over a column
    that lives only on JobFacets), which is also what drops NULLs and
    rows with no facets row at all out of every bucket.
    """
    conditions = (Job.is_active.is_(True),)

    def _options(column) -> list[JobFacetOption]:
        rows = (
            db.query(column, func.count())
            .select_from(Job)
            .join(JobFacets, JobFacets.job_id == Job.id)
            .filter(*conditions)
            .filter(column.isnot(None))
            .group_by(column)
            .all()
        )
        return [JobFacetOption(value=value, count=count) for value, count in rows]

    return JobFacetOptions(
        countries=_options(JobFacets.country),
        regions=_options(JobFacets.region),
        seniorities=_options(JobFacets.seniority),
        functions=_options(JobFacets.function),
    )


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
