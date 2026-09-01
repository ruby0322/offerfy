from datetime import datetime, timezone

from pydantic import BaseModel, Field

JOB_SOURCES = ("greenhouse", "lever", "ashby", "taiwanjobs")


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
    limit: int = Field(default=20)
