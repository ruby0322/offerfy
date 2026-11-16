from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job, JobFacets
from app.schemas_jobs import (
    JobCityOptions,
    JobDetail,
    JobEmployer,
    JobEmployerList,
    JobEmployerProfile,
    JobFacetOption,
    JobFacetOptions,
    JobList,
    JobPulse,
    JobPulseEmployer,
    JobSitemapItem,
    JobSitemapPage,
)
from app.services.jobs.cities import fold_city_counts
from app.services.jobs.employers import employer_slug, resolve_slug, slug_index
from app.services.jobs.enrich import (
    default_taxonomy,
    valid_countries,
)
from app.services.jobs.search import (
    after_cursor as _after_cursor,
)
from app.services.jobs.search import (
    filtered_query,
    list_item,
)
from app.services.jobs.search import (
    iso as _iso,
)
from app.services.jobs.search import (
    posted_at_order as _posted_at_order,
)
from app.services.jobs.spotlight import select_featured
from app.services.jobs.store import freshness_cutoff

router = APIRouter()

LIST_MAX = 100
SITEMAP_PAGE_SIZE = 1000
PULSE_NEWEST = 5
PULSE_EMPLOYERS = 5
FACET_COMPANIES = 24
CITY_OPTIONS = 12
# Cities named on one employer's page. Their other breakdowns come from closed
# vocabularies and need no cap; the city fold has a long tail.
EMPLOYER_CITIES = 8
# What "new" means everywhere on the live board. One constant, because the
# strip ("+733 in the last 24h"), the employer leaderboard and the ?posted_
# within_days= filter are read side by side and must agree.
PULSE_NEW_HOURS = 24
PULSE_EMPLOYER_WINDOW_DAYS = 7

def _sitemap_conditions():
    """Rows eligible for the jobs sitemap.

    A row past the freshness window is noindex on the frontend, so listing it
    here would only hand Google a sitemap full of URLs it has been told to
    drop. Both the count and the page query use this so they cannot drift.
    """
    return (Job.is_active.is_(True), Job.last_seen_at >= freshness_cutoff())


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


def _filtered_query(db: Session, **filters):
    """`services.jobs.search.filtered_query`, with its ValueError as a 422.

    The builder moved to a service so the editor agent's search tool could
    call it too (see that module's docstring). It raises ValueError rather
    than HTTPException, so the HTTP surface translates here and answers
    exactly as it did before the move.
    """
    try:
        return filtered_query(db, **filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/v1/jobs", response_model=JobList)
def list_jobs(
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    active: bool = True,
    company: list[str] | None = Query(default=None),
    country: str | None = None,
    region: str | None = None,
    city: list[str] | None = Query(default=None),
    seniority: str | None = None,
    function: list[str] | None = Query(default=None),
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
        company=company,
        country=country,
        region=region,
        city=city,
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
    return JobList(items=[list_item(row) for row in rows], next_cursor=next_cursor, total=total)


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
    return JobList(items=[list_item(row) for row in rows], next_cursor=None, total=len(rows))


@router.get("/v1/jobs/facets", response_model=JobFacetOptions)
def jobs_facets(
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    company: list[str] | None = Query(default=None),
    country: str | None = None,
    region: str | None = None,
    city: list[str] | None = Query(default=None),
    seniority: str | None = None,
    function: list[str] | None = Query(default=None),
    posted_within_days: int | None = Query(default=None, ge=1, le=90),
    db: Session = Depends(get_db),
):
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
    # Every filter GET /v1/jobs takes, applied here too, so a board page or
    # an already-narrowed search sees the counts *it* would get rather than
    # the whole catalogue's.
    #
    # With one exception, and it is not cosmetic: a facet never filters its
    # OWN options. Without that, choosing Taiwan leaves the country menu
    # holding Taiwan alone and there is no way back to anywhere else -- the
    # menu stops being a way to change your mind and becomes a readout of
    # what you already picked. So each facet's counts come from the query
    # with its own key (and, for country, the city that hangs off it)
    # dropped, which also makes the numbers answer the question the menu is
    # actually asking: "how many if I switched to this instead".
    filters = {
        "q": q,
        "source": source,
        "remote": remote,
        "active": True,
        "company": company,
        "country": country,
        "region": region,
        "city": city,
        "seniority": seniority,
        "function": function,
        "posted_within_days": posted_within_days,
    }

    def _without(*keys: str):
        narrowed = dict(filters)
        for key in keys:
            narrowed[key] = None
        narrowed["active"] = True
        return _filtered_query(db, **narrowed).subquery()

    def _options(column_name: str, *drop: str) -> list[JobFacetOption]:
        column = getattr(JobFacets, column_name)
        scope = _without(*drop)
        rows = (
            db.query(column, func.count())
            .select_from(scope)
            .join(JobFacets, JobFacets.job_id == scope.c.id)
            .filter(column.isnot(None))
            .group_by(column)
            .all()
        )
        return [JobFacetOption(value=value, count=count) for value, count in rows]

    employers = _without("company")
    company_rows = (
        db.query(employers.c.company, func.count(employers.c.id))
        .group_by(employers.c.company)
        .order_by(func.count(employers.c.id).desc(), employers.c.company.asc())
        .limit(FACET_COMPANIES)
        .all()
    )
    employer_total = db.query(func.count(func.distinct(employers.c.company))).scalar() or 0

    return JobFacetOptions(
        # country drops city too: the cities on offer hang off the country,
        # so counting countries under a city filter would leave exactly one.
        countries=_options("country", "country", "city"),
        regions=_options("region", "region"),
        seniorities=_options("seniority", "seniority"),
        functions=_options("function", "function"),
        companies=[
            JobFacetOption(value=value, count=count) for value, count in company_rows
        ],
        employer_total=int(employer_total),
    )


@router.get("/v1/jobs/cities", response_model=JobCityOptions)
def jobs_cities(
    country: str = Query(min_length=2, max_length=2),
    limit: int = Query(default=CITY_OPTIONS, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Cities worth offering for one country.

    Declared above GET /v1/jobs/{job_id}, like every other literal path on
    this router, or "cities" is captured as a job id.

    Scoped to is_active for the same reason /v1/jobs/facets is: a picker has
    to describe the list it filters. The fold happens in Python over a GROUP
    BY (a few hundred rows at most), never over the postings themselves.
    """
    taxonomy = default_taxonomy()
    if country not in valid_countries(taxonomy):
        raise HTTPException(status_code=422, detail="Invalid country")
    rows = (
        db.query(JobFacets.city, func.count(JobFacets.job_id))
        .select_from(Job)
        .join(JobFacets, JobFacets.job_id == Job.id)
        .filter(Job.is_active.is_(True), JobFacets.country == country)
        .filter(JobFacets.city.isnot(None))
        .group_by(JobFacets.city)
        .all()
    )
    folded = fold_city_counts([(value, count) for value, count in rows], taxonomy)
    return JobCityOptions(
        country=country,
        items=[JobFacetOption(value=name, count=count) for name, count in folded[:limit]],
    )


@router.get("/v1/jobs/pulse", response_model=JobPulse)
def jobs_pulse(
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    company: list[str] | None = Query(default=None),
    country: str | None = None,
    region: str | None = None,
    city: list[str] | None = Query(default=None),
    seniority: str | None = None,
    function: list[str] | None = Query(default=None),
    posted_within_days: int | None = Query(default=None, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """The live board: how big the catalog is, what just landed, who is hiring.

    One endpoint rather than three because the three numbers are read as one
    sentence; served separately they could be minutes apart and visibly
    disagree. Declared above GET /v1/jobs/{job_id} -- see jobs_cities.
    """
    now = datetime.now(timezone.utc)
    fresh_cutoff = now - timedelta(hours=PULSE_NEW_HOURS)
    week_cutoff = now - timedelta(days=PULSE_EMPLOYER_WINDOW_DAYS)

    # Takes the same filters as the list and the facets, so a curated board
    # gets its own pulse -- "839 open internships, +23 in the last 24h, 61
    # employers hiring interns" -- from one endpoint rather than a second one
    # that could drift from this.
    filters = dict(
        q=q,
        source=source,
        remote=remote,
        active=True,
        country=country,
        region=region,
        city=city,
        seniority=seniority,
        function=function,
        posted_within_days=posted_within_days,
    )
    base = _filtered_query(db, company=company, **filters)
    # The employer leaderboard drops the employer filter, for the reason
    # jobs_facets spells out at length: it is a control, not a readout. The
    # board page renders these rows as a multi-select -- pick NVIDIA and the
    # row has to still offer Stripe, or there is no way to add a second
    # employer or to see what else was on offer. Everything else here does
    # describe the filtered list, so it keeps the filter.
    employer_base = _filtered_query(db, company=None, **filters)

    active_total = base.with_entities(func.count(Job.id)).scalar() or 0
    new_24h = (
        base.filter(Job.posted_at.isnot(None), Job.posted_at >= fresh_cutoff)
        .with_entities(func.count(Job.id))
        .scalar()
        or 0
    )
    employer_count = (
        base.with_entities(func.count(func.distinct(Job.company))).scalar() or 0
    )
    refreshed_at = base.with_entities(func.max(Job.last_seen_at)).scalar()

    newest = (
        base.filter(Job.posted_at.isnot(None))
        .order_by(Job.posted_at.desc(), Job.id.desc())
        .limit(PULSE_NEWEST)
        .all()
    )

    # One grouped pass, not a per-employer query: the leaderboard is five
    # rows but the group is every employer in scope.
    new_in_window = func.sum(
        case((and_(Job.posted_at.isnot(None), Job.posted_at >= week_cutoff), 1), else_=0)
    )
    employer_rows = (
        employer_base.with_entities(Job.company, func.count(Job.id), new_in_window)
        .group_by(Job.company)
        .order_by(new_in_window.desc(), func.count(Job.id).desc(), Job.company.asc())
        .limit(PULSE_EMPLOYERS)
        .all()
    )

    return JobPulse(
        active_total=int(active_total),
        new_24h=int(new_24h),
        employer_count=int(employer_count),
        refreshed_at=_iso(refreshed_at),
        newest=[list_item(row) for row in newest],
        top_employers=[
            JobPulseEmployer(
                company=company,
                open_count=int(open_count or 0),
                new_count=int(new_count or 0),
            )
            for company, open_count, new_count in employer_rows
        ],
    )


@router.get("/v1/jobs/employers", response_model=JobEmployerList)
def jobs_employers(db: Session = Depends(get_db)):
    """Every employer with a live posting, biggest first.

    Declared above GET /v1/jobs/{job_id} -- same ordering requirement as
    every other literal path on this router, or "employers" is captured as a
    job id.

    Unpaginated, and meant to stay that way: this is the whole set the
    employer index lists (~80 rows today, one per curated board), and it is
    one grouped pass over the active catalog rather than a query per
    employer. If the board list ever grows by an order of magnitude this
    needs a cursor like GET /v1/jobs has.

    Scoped to is_active, like the facets and the pulse: an employer whose
    postings have all closed has nothing to show, so they drop off the index
    and their page 404s rather than rendering an empty list.
    """
    now = datetime.now(timezone.utc)
    week_cutoff = now - timedelta(days=PULSE_EMPLOYER_WINDOW_DAYS)
    new_in_window = func.sum(
        case((and_(Job.posted_at.isnot(None), Job.posted_at >= week_cutoff), 1), else_=0)
    )
    rows = (
        db.query(Job.company, func.count(Job.id), new_in_window)
        .filter(Job.is_active.is_(True))
        .group_by(Job.company)
        .all()
    )
    # One index built from every name in scope, so a slug two employers would
    # both claim is dropped from both -- see services.jobs.employers.
    by_company = {
        company: slug
        for slug, company in slug_index(company for company, _open, _new in rows).items()
    }
    items = [
        JobEmployer(
            company=company,
            slug=by_company[company],
            open_count=int(open_count or 0),
            new_count=int(new_count or 0),
        )
        for company, open_count, new_count in rows
        if company in by_company
    ]
    # Count first, then name, and casefolded: ties would otherwise order by
    # whatever the database handed back, which changes between calls.
    items.sort(key=lambda row: (-row.open_count, row.company.casefold()))
    return JobEmployerList(items=items, total=len(items))


@router.get("/v1/jobs/employers/{slug}", response_model=JobEmployerProfile)
def jobs_employer(slug: str, db: Session = Depends(get_db)):
    """One employer's profile: their counts, and the shape of their hiring.

    Declared above GET /v1/jobs/{job_id}, like /v1/jobs/employers itself.

    The slug is resolved against the companies currently in scope rather
    than parsed, because the slug is derived from the stored name and the
    derivation is lossy. An unknown or ambiguous slug is a 404 -- the page
    has no honest content for either case.

    Cities are capped because the fold can produce a long tail; the other
    three are closed vocabularies (enrich.py), so they are returned whole
    and the page decides how many to show.
    """
    companies = [
        row[0] for row in db.query(Job.company).filter(Job.is_active.is_(True)).distinct()
    ]
    company = resolve_slug(slug, companies)
    if company is None:
        raise HTTPException(status_code=404, detail="Employer not found")

    now = datetime.now(timezone.utc)
    week_cutoff = now - timedelta(days=PULSE_EMPLOYER_WINDOW_DAYS)
    base = _filtered_query(
        db, q=None, source=None, remote=None, active=True, company=company
    )

    open_count = base.with_entities(func.count(Job.id)).scalar() or 0
    new_count = (
        base.filter(Job.posted_at.isnot(None), Job.posted_at >= week_cutoff)
        .with_entities(func.count(Job.id))
        .scalar()
        or 0
    )
    remote_count = (
        base.filter(Job.remote.is_(True)).with_entities(func.count(Job.id)).scalar() or 0
    )
    first_seen_at, last_seen_at = base.with_entities(
        func.min(Job.first_seen_at), func.max(Job.last_seen_at)
    ).one()
    sources = sorted(
        row[0] for row in base.with_entities(Job.source).distinct()
    )

    scope = base.subquery()

    def _options(column_name: str) -> list[JobFacetOption]:
        column = getattr(JobFacets, column_name)
        rows = (
            db.query(column, func.count())
            .select_from(scope)
            .join(JobFacets, JobFacets.job_id == scope.c.id)
            .filter(column.isnot(None))
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
            .all()
        )
        return [JobFacetOption(value=value, count=count) for value, count in rows]

    city_rows = (
        db.query(JobFacets.city, func.count())
        .select_from(scope)
        .join(JobFacets, JobFacets.job_id == scope.c.id)
        .filter(JobFacets.city.isnot(None))
        .group_by(JobFacets.city)
        .all()
    )
    folded = fold_city_counts([(value, count) for value, count in city_rows])

    return JobEmployerProfile(
        company=company,
        slug=employer_slug(company),
        open_count=int(open_count),
        new_count=int(new_count),
        remote_count=int(remote_count),
        first_seen_at=_iso(first_seen_at),
        last_seen_at=_iso(last_seen_at),
        sources=sources,
        cities=[
            JobFacetOption(value=name, count=count)
            for name, count in folded[:EMPLOYER_CITIES]
        ],
        countries=_options("country"),
        functions=_options("function"),
        seniorities=_options("seniority"),
    )


@router.get("/v1/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Not found")
    return JobDetail(
        **list_item(job).model_dump(),
        description_html=job.description_html,
        description_text=job.description_text,
        first_seen_at=_iso(job.first_seen_at) or "",
    )
