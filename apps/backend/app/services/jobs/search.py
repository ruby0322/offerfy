"""Catalogue search: one query builder, two callers.

This is `app/routers/jobs.py`'s filter, moved out of the router so that
something other than an HTTP handler can run it. The editor agent's
`search_jobs` tool (`app/services/jobs_tools.py`) is the second caller, and a
tool handler must not import a router private.

Two differences from the version that lived in the router, both deliberate:

- `filtered_query` raises `ValueError`, not `HTTPException`. The router
  translates it back to 422, so `/v1/jobs` answers exactly as it did; a tool
  handler turns it into `{"error": ...}` for the model to correct. A service
  that raised HTTP status codes would be lying about where it can be used.

- `fresh_only` pairs `is_active` with `last_seen_at >= freshness_cutoff()`.
  The HTTP list does NOT pass it, so its behaviour is unchanged; the agent
  does. `AGENTS.md` section 8 is the reason the two differ: `is_active` only
  flips when an ingest run sees a posting disappear, so a stalled ingest
  leaves every row active forever. For a crawler that degrades to a noindex,
  which is why the sitemap already pairs the two (`_sitemap_conditions`), and
  for the per-user prefilter it would mean recommending dead roles, which is
  why `match_sweep.prefilter_query` pairs them too. An agent telling someone a
  role is open is the same claim, so it reads the same way. The window itself
  stays single-sourced in `store.JOB_FRESHNESS_DAYS` -- imported, never
  copied.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, nulls_last, or_
from sqlalchemy.orm import Session

from app.models import Job, JobFacets
from app.schemas_jobs import JOB_SOURCES, JobListItem
from app.services.jobs.cities import stored_values_for
from app.services.jobs.enrich import (
    Taxonomy,
    default_taxonomy,
    valid_countries,
    valid_functions,
    valid_seniorities,
)
from app.services.jobs.store import freshness_cutoff


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def like_term(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def posted_at_order():
    return (nulls_last(Job.posted_at.desc()), Job.id.desc())


def after_cursor(stamp: datetime | None, job_id: str):
    if stamp is None:
        return and_(Job.posted_at.is_(None), Job.id < job_id)
    return or_(
        Job.posted_at < stamp,
        and_(Job.posted_at == stamp, Job.id < job_id),
        Job.posted_at.is_(None),
    )


def company_values(company: str | Sequence[str] | None) -> list[str]:
    """The employers a `company` filter names, folded for case-insensitive use.

    One value or several: `?company=Stripe&company=Dcard` means "either", the
    OR a multi-select employer row needs. Repeated parameters rather than one
    comma-joined value -- unlike `seniority` below, which splits on commas
    because it is a closed vocabulary with no commas in it. A company name is
    free text out of `data/ats_boards.json`, so the day one of them is
    "Thermo Fisher, Inc." a comma split would quietly filter for two employers
    that do not exist. Blank entries are dropped rather than matching "".
    """
    raw = [company] if isinstance(company, str) else list(company or ())
    return [part.strip().lower() for part in raw if part and part.strip()]


def valid_regions(taxonomy: Taxonomy) -> set[str]:
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
        posted_at=iso(job.posted_at),
        last_seen_at=iso(job.last_seen_at) or "",
        is_active=job.is_active,
    )


def filtered_query(
    db: Session,
    *,
    q: str | None,
    source: str | None,
    remote: bool | None,
    active: bool | None,
    company: str | Sequence[str] | None = None,
    country: str | None = None,
    region: str | None = None,
    city: str | None = None,
    seniority: str | None = None,
    function: str | None = None,
    posted_within_days: int | None = None,
    fresh_only: bool = False,
):
    """Every posting matching these filters. Raises ValueError on bad vocabulary."""
    query = db.query(Job)
    if active is None or active:
        query = query.filter(Job.is_active.is_(True))
    elif active is False:
        query = query.filter(Job.is_active.is_(False))
    if fresh_only:
        # Not a substitute for is_active and not substituted by it; see the
        # module docstring.
        query = query.filter(Job.last_seen_at >= freshness_cutoff())
    if source:
        if source not in JOB_SOURCES:
            raise ValueError("Invalid source")
        query = query.filter(Job.source == source)
    if remote is True:
        query = query.filter(Job.remote.is_(True))
    elif remote is False:
        query = query.filter(Job.remote.is_(False))
    companies = company_values(company)
    if companies:
        # Equality, not ILIKE: company is picked from a facet we produced, so
        # it is an exact stored value. A LIKE here would make "Wise" match
        # "Wisetech" the day such a board is added. IN over that same equality
        # when several are named, so the employer row's selection is an OR and
        # one employer still costs the one comparison it always did.
        query = query.filter(func.lower(Job.company).in_(companies))
    if q:
        term = like_term(q.strip())
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
                raise ValueError("Invalid seniority")
            query = query.filter(JobFacets.seniority.in_(wanted))
        if function:
            if function not in valid_functions(taxonomy):
                raise ValueError("Invalid function")
            query = query.filter(JobFacets.function == function)
        if region:
            if region not in valid_regions(taxonomy):
                raise ValueError("Invalid region")
            query = query.filter(JobFacets.region == region)
        if country:
            if country not in valid_countries(taxonomy):
                raise ValueError("Invalid country")
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
        # so it is excluded here, unlike posted_at_order's nulls_last which
        # still displays it (at the end) when no age filter is active.
        cutoff = datetime.now(timezone.utc) - timedelta(days=posted_within_days)
        query = query.filter(Job.posted_at >= cutoff)

    return query
