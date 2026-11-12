from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, StrictBool, field_validator

# Imported, not redeclared: two copies of this tuple silently drift.
from app.services.jobs.enrich import (
    default_taxonomy,
    valid_countries,
    valid_functions,
    valid_seniorities,
)
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
    # Defaulted, not required: /v1/jobs/featured builds this same model and
    # must pass total=len(rows) explicitly, but a required field would break
    # it the moment anyone forgets -- see the router for that call site.
    total: int = 0


class JobFacetOption(BaseModel):
    value: str
    count: int


class JobFacetOptions(BaseModel):
    countries: list[JobFacetOption]
    regions: list[JobFacetOption]
    seniorities: list[JobFacetOption]
    functions: list[JobFacetOption]


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


# --- Job preferences ---------------------------------------------------------
#
# The pipe encoding of target_functions / target_locations is a storage detail
# (see models.JobPreference); the wire format is a list of strings. The columns
# carry no CHECK constraint on purpose -- the vocabularies may grow -- so this
# is the only place a bad value can be stopped.

PREFERENCE_ACTIVITIES = ("actively_looking", "browsing", "paused")


def _clean_members(values: list[str], allowed: set[str], what: str) -> list[str]:
    out: list[str] = []
    for raw in values:
        value = raw.strip().lower()
        if value not in allowed:
            raise ValueError(f"Invalid {what}: {raw!r}")
        if value not in out:
            out.append(value)
    return out


class JobPreferenceIn(BaseModel):
    """A full replacement of the person's preferences, never a patch.

    An omitted field is cleared (activity returns to its default), because a
    partial update cannot express "I no longer want a seniority filter".
    """

    target_functions: list[str] = []
    target_locations: list[str] = []
    target_seniority: str | None = None
    remote_required: StrictBool | None = None
    activity: Literal["actively_looking", "browsing", "paused"] = "actively_looking"

    @field_validator("target_functions")
    @classmethod
    def _functions(cls, v: list[str]) -> list[str]:
        t = default_taxonomy()
        return _clean_members(v, valid_functions(t), "function")

    @field_validator("target_locations")
    @classmethod
    def _locations(cls, v: list[str]) -> list[str]:
        # Countries, lowercased: what seeding writes (preferences.py).
        t = default_taxonomy()
        allowed = {c.lower() for c in valid_countries(t)}
        return _clean_members(v, allowed, "location")

    @field_validator("target_seniority")
    @classmethod
    def _seniority(cls, v: str | None) -> str | None:
        if v is None:
            return None
        t = default_taxonomy()
        # "unknown" is a valid /jobs BROWSE filter -- there it means "show me
        # the postings no rule could classify". As a stated TARGET it is
        # meaningless: nobody is looking for an unclassified level. Omitting
        # the field is how you say "no seniority preference".
        allowed = valid_seniorities(t) - {"unknown"}
        value = v.strip().lower()
        if value not in allowed:
            raise ValueError(f"Invalid seniority: {v!r}")
        return value


class JobPreferenceOut(BaseModel):
    target_functions: list[str]
    target_locations: list[str]
    target_seniority: str | None
    remote_required: bool | None
    activity: str
    # null before the first save: the seed is transient and has no timestamp.
    # Not "", which a client parsing a timestamp field would choke on.
    updated_at: str | None
    # "missing" | "stale" | "fresh": whether the editor should offer a rebuild.
    facts_state: str


class ResumeFactsRefreshOut(BaseModel):
    facts_state: str
    # A named reason when the build did not produce facts; None on success.
    error: str | None = None
