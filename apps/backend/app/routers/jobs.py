from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, nulls_last, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job, JobFacets
from app.schemas_jobs import (
    JOB_SOURCES,
    JobCityOptions,
    JobDetail,
    JobEmployer,
    JobEmployerList,
    JobEmployerProfile,
    JobFacetOption,
    JobFacetOptions,
    JobList,
    JobListItem,
    JobPulse,
    JobPulseEmployer,
    JobSitemapItem,
    JobSitemapPage,
)
from app.services.jobs.cities import fold_city_counts, stored_values_for
from app.services.jobs.employers import employer_slug, resolve_slug, slug_index
from app.services.jobs.enrich import (
    Taxonomy,
    default_taxonomy,
    valid_countries,
    valid_functions,
    valid_seniorities,
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


def _valid_regions(taxonomy: Taxonomy) -> set[str]:
    return set(taxonomy.location_regions.values())


def list_item(job: Job) -> JobListItem:
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
    company: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
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
    if company:
        # Equality, not ILIKE: company is picked from a facet we produced, so
        # it is an exact stored value. A LIKE here would make "Wise" match
        # "Wisetech" the day such a board is added.
        query = query.filter(func.lower(Job.company) == company.strip().lower())
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
    if seniority or function or country or region or city:
        query = query.join(JobFacets, JobFacets.job_id == Job.id)
        taxonomy = default_taxonomy()
        if seniority:
            # Comma-separated, because a curated board is often one audience
            # spread over two rungs: "new grad" is new_grad AND junior, and
            # "ready to move" is mid, senior and staff_plus. The filter UI
            # still sends exactly one value; this only widens what the URL
            # can say. Every member is validated, so one bad name is a 422
            # rather than a silently narrower list.
            wanted = [part.strip() for part in seniority.split(",") if part.strip()]
            if not wanted or any(item not in valid_seniorities(taxonomy) for item in wanted):
                raise HTTPException(status_code=422, detail="Invalid seniority")
            query = query.filter(JobFacets.seniority.in_(wanted))
        if function:
            if function not in valid_functions(taxonomy):
                raise HTTPException(status_code=422, detail="Invalid function")
            query = query.filter(JobFacets.function == function)
        if region:
            if region not in _valid_regions(taxonomy):
                raise HTTPException(status_code=422, detail="Invalid region")
            query = query.filter(JobFacets.region == region)
        if country:
            if country not in valid_countries(taxonomy):
                raise HTTPException(status_code=422, detail="Invalid country")
            query = query.filter(JobFacets.country == country)
        if city and city.strip():
            # Resolved against the distinct spellings actually stored, not by
            # LIKE: see services.jobs.cities.stored_values_for for why both
            # equality and substring get this wrong. The DISTINCT is over a
            # few hundred values per country, and is only paid when a city
            # filter is actually asked for.
            distinct = db.query(JobFacets.city).filter(JobFacets.city.isnot(None))
            if country:
                distinct = distinct.filter(JobFacets.country == country)
            spellings = stored_values_for(
                city, [row[0] for row in distinct.distinct().all()], taxonomy
            )
            # An empty IN () is false in SQL, which is the right answer for a
            # city no posting names -- an empty list, not an error.
            query = query.filter(JobFacets.city.in_(spellings))

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
    company: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
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
    company: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    seniority: str | None = None,
    function: str | None = None,
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
    company: str | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    seniority: str | None = None,
    function: str | None = None,
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
    base = _filtered_query(
        db,
        q=q,
        source=source,
        remote=remote,
        active=True,
        company=company,
        country=country,
        region=region,
        city=city,
        seniority=seniority,
        function=function,
        posted_within_days=posted_within_days,
    )

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
        base.with_entities(Job.company, func.count(Job.id), new_in_window)
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
