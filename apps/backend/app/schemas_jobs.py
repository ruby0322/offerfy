from datetime import datetime, timezone

from pydantic import BaseModel

# Imported, not redeclared: two copies of this tuple silently drift.
from app.services.jobs.types import JOB_SOURCES


class JobListItem(BaseModel):
    id: str
    source: str
    company: str
    title: str
    location: str | None = None
    remote: bool | None = None
    apply_url: str
    source_url: str
    posted_at: str | None = None
    last_seen_at: str
    is_active: bool


class JobDetail(JobListItem):
    description_html: str
    description_text: str
    first_seen_at: str


class JobList(BaseModel):
    items: list[JobListItem]
    next_cursor: str | None = None


class JobSitemapItem(BaseModel):
    id: str
    last_seen_at: str


class JobSitemapPage(BaseModel):
    page: int
    page_size: int
    total_pages: int
    items: list[JobSitemapItem]


class AdminIngestRun(BaseModel):
    id: str
    source: str
    board_slug: str | None = None
    started_at: str
    finished_at: str | None = None
    status: str
    ok_count: int
    error_count: int
    upserted_count: int
    expired_count: int
    error_snippet: str | None = None


class AdminIngestRunList(BaseModel):
    items: list[AdminIngestRun]
    limit: int


class AdminIngestBoardFailure(BaseModel):
    source: str
    board_slug: str | None = None
    error_snippet: str | None = None


class AdminIngestPass(BaseModel):
    """One ingest pass, folded up from its ~119 per-board rows."""

    run_id: str
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    sources_ok: int
    sources_failed: int
    # What the boards handed us. fetched - upserted is how many the scope rules
    # dropped, which is worth being able to see: a filter that quietly eats a
    # whole board should not take weeks to spot.
    #
    # Caveat: smartrecruiters scopes itself on the list payload before fetching
    # each posting's description -- it has to, that is one request per posting --
    # so for that source fetched is already post-filter and the difference
    # undercounts. Every other source reports the raw board count.
    fetched: int
    upserted: int
    expired: int
    # "ok" | "degraded" | "failed". Mirrors CatalogResult.refreshed_nothing so
    # this never disagrees with the exit code the systemd unit saw: "failed"
    # means the catalog did not move, "degraded" means some board did not.
    status: str
    failing_boards: list[AdminIngestBoardFailure]


class AdminIngestHealth(BaseModel):
    # Outcome-based, so it catches a timer that stopped as well as one that ran
    # and failed: how old the freshest active posting is.
    freshest_last_seen_at: str | None = None
    catalog_age_hours: float | None = None
    # How many job pages are actually indexable vs noindexed right now, split on
    # freshness_days. This is the number SEO lives or dies by.
    fresh_job_count: int
    stale_job_count: int
    freshness_days: int
    expected_interval_hours: int
    overdue: bool
    last_failure_at: str | None = None


class AdminIngestOverview(BaseModel):
    health: AdminIngestHealth
    passes: list[AdminIngestPass]
    # Rows from before migration 014, which have no run_id and so cannot be
    # grouped into a pass. Surfaced rather than silently dropped.
    legacy_row_count: int
