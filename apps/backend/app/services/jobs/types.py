from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# "taiwanjobs" is retired -- nothing ingests it any more (see AGENTS.md) -- but
# it stays here and in the jobs.job_source_values check constraint because
# historical rows still carry it. Narrowing either would fail against them.
JOB_SOURCES = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workday",
    "taiwanjobs",
)

USER_AGENT = "OfferfyBot/1.0 (+https://offerfy.cc)"
MAX_DESCRIPTION_CHARS = 200_000


@dataclass(frozen=True)
class NormalizedJob:
    source: str
    source_id: str
    company: str
    title: str
    location: str | None
    remote: bool | None
    apply_url: str
    source_url: str
    description_html: str
    description_text: str
    posted_at: datetime | None


@dataclass
class Board:
    source: str
    slug: str
    company: str
