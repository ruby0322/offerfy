from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, field_validator

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
    # The employers with the most open roles, not all of them: 88 boards is
    # more than a menu should list, and the long tail is reachable by search
    # inside that menu. employer_total is what the menu's "all N" says, so
    # the two can never disagree about how many employers the catalog has.
    companies: list[JobFacetOption] = []
    employer_total: int = 0


class JobCityOptions(BaseModel):
    """Cities worth offering for one country, biggest first.

    Folded from JobFacets.city at read time by services.jobs.cities -- see
    that module for why the stored column is not itself a vocabulary. The
    counts do not sum to the country's total: a posting open in two cities
    counts once in each.
    """

    country: str
    items: list[JobFacetOption]


class JobPulseEmployer(BaseModel):
    company: str
    open_count: int
    new_count: int


class JobPulse(BaseModel):
    """The live board above the catalog.

    Every "new" figure here is posted_at-based, deliberately matching what
    ?posted_within_days= filters on, so the strip, the board and the filtered
    list can never tell the visitor three different stories. A posting whose
    board publishes no date (most of Workday) is therefore absent from the
    new counts while still being in active_total -- undercounting what is
    new beats inventing a date we were never given.

    refreshed_at is the freshest last_seen_at, i.e. when ingest last
    confirmed anything, which is the honest answer to "how live is this?".
    """

    active_total: int
    new_24h: int
    employer_count: int
    refreshed_at: str | None
    newest: list[JobListItem]
    top_employers: list[JobPulseEmployer]


class JobEmployer(BaseModel):
    company: str
    # Derived from company, never stored -- see services.jobs.employers for
    # why, and for why an employer whose name yields no slug is absent here
    # rather than present with an empty one.
    slug: str
    open_count: int
    new_count: int


class JobEmployerList(BaseModel):
    """Every employer with a live posting, biggest first.

    total counts the employers this list could name, which is the same number
    JobPulse.employer_count reports for an unfiltered board -- minus any whose
    name makes no slug. The two are allowed to differ by exactly that, and the
    index page says "N employers" from this one so it cannot quote a figure
    its own list does not fill.
    """

    items: list[JobEmployer]
    total: int


class JobEmployerProfile(BaseModel):
    """One employer, as much as the catalog actually knows about them.

    Everything here is counted from their own postings -- there is no company
    record behind it, no headcount, no blurb. new_count is the same
    posted_at-based week JobPulse uses, so an employer page and the board it
    came from cannot tell different stories about what is new.

    first_seen_at is the oldest first_seen_at among their live postings, i.e.
    when Offerfy started tracking them, not when the company was founded.
    """

    company: str
    slug: str
    open_count: int
    new_count: int
    remote_count: int
    first_seen_at: str | None
    last_seen_at: str | None
    # Which ATS their postings come from. Plural because an employer can run
    # two boards; named rather than linked, since Job stores no board URL.
    sources: list[str]
    cities: list[JobFacetOption]
    countries: list[JobFacetOption]
    functions: list[JobFacetOption]
    seniorities: list[JobFacetOption]


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


class ApplicationCreate(BaseModel):
    job_id: str
    source: Literal["digest", "search"] = "search"


class ApplicationConfirm(BaseModel):
    # Strict: "no" must not arrive as a truthy string.
    applied: StrictBool


class ApplicationPatch(BaseModel):
    # Only what a person reports themselves; every other status is reached by
    # the action that implies it. The service re-checks this.
    status: Literal["interviewing", "closed"] | None = None
    # Whether the employer ever answered. Only carries information alongside
    # status="closed", where "not moving forward" means both "they said no"
    # and "nobody answered"; interviewing implies a reply on its own. Strict,
    # for ApplicationConfirm's reason: "no" must not arrive as a truthy
    # string. Omitted means "not stated" and leaves the stamp alone, which is
    # why this is StrictBool | None and not a defaulted bool.
    replied: StrictBool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ApplicationOut(BaseModel):
    job_id: str
    job_title: str
    company: str
    apply_url: str
    # Reported, not filtered: a person's own history shows a closed posting.
    job_is_active: bool
    status: str
    source: str
    tailor_resume_id: str | None
    applied_at: str | None
    applied_confirmed_at: str | None
    # Null means "no reply recorded", which on a closed row means nobody
    # answered *or* that the row predates the column. Not a bool: the date is
    # what makes "11 days to a first reply" computable.
    replied_at: str | None
    last_status_at: str
    notes: str | None


class ResumeHealthOut(BaseModel):
    has_master: bool
    published: bool
    facts_state: str
    matching_revision_current: bool
    needs_rebuild: bool


class EmployerViewsOut(BaseModel):
    disclosure_count: int
    # null when no employer has read the profile; never "".
    last_disclosed_at: str | None


class DigestItemOut(BaseModel):
    job_id: str
    job_title: str
    company: str
    apply_url: str
    job_is_active: bool
    # str, not Literal: a degraded digest's items carry "unranked", which is
    # deliberately not one of the two real tracks and must pass through as-is.
    track: str
    rank: int
    tier: str | None
    fit_score: float | None
    reason: str | None
    gap: str | None


class DigestOut(BaseModel):
    status: str
    built_at: str
    opened_at: str | None
    candidate_count: int
    items: list[DigestItemOut]


class DashboardOut(BaseModel):
    preference: JobPreferenceOut
    match_count: int
    # How many of those the catalogue first saw since the `since` the caller
    # passed. None when it passed none -- a first visit has no window, and 0
    # would claim "nothing new" where the truth is "we were not asked".
    new_match_count: int | None = None
    # The first few of those matches, in the same shape /v1/jobs returns, so
    # the dashboard can render them with the jobs page's own row. Required, and
    # it must stay required: a writer exists (routers/dashboard.py fills it).
    # It was briefly removed on main while no writer existed, and this merge
    # then deleted it again with no conflict marker -- delete-versus-unchanged
    # resolves to deleted, so the field vanished silently while the handler
    # that fills it merged in fine.
    matches: list[JobListItem]
    applications: list[ApplicationOut]
    resume_health: ResumeHealthOut
    employer_views: EmployerViewsOut
    # None, not an empty object, when the person has no digest at all.
    digest: DigestOut | None
