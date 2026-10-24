"""SmartRecruiters postings.

Added because the three original providers cannot reach some employers this
audience cares about -- Grab, Canva and Wise publish here and nowhere we could
read, so their entries in the board list fetched nothing for months.

Two things make this source different from greenhouse/lever/ashby:

1. **Descriptions are not in the list payload.** Each posting needs its own
   request, so a board like Delivery Hero's 950 postings would be 950 calls. We
   therefore apply the scope rules to the cheap list metadata *first* and only
   spend a detail request on a posting we intend to keep.
2. **It publishes a structured `function`** ("engineering", "sales", ...), which
   is a far better scope signal than guessing from the title, so we use it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.jobs.http import get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.scope import ScopeRules, function_in_scope, title_in_scope
from app.services.jobs.types import NormalizedJob

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit={limit}&offset={offset}"
PAGE_SIZE = 100
# A guard, not a quota: one board should not be able to spend the whole pass.
MAX_POSTINGS = 2000

# Boilerplate repeated verbatim across every posting at a company. Including it
# would make hundreds of job pages near-duplicates of each other, which is worth
# avoiding for its own sake and doubly so on pages we want indexed.
_SKIP_SECTIONS = frozenset({"companydescription"})


def fetch_smartrecruiters(
    client: httpx.Client, slug: str, company: str, *, rules: ScopeRules | None = None
) -> list[NormalizedJob] | None:
    summaries = _fetch_summaries(client, slug)
    if summaries is None:
        return None
    out: list[NormalizedJob] = []
    for raw in summaries:
        job = _normalize_summary(raw, slug, company, rules=rules)
        if job is None:
            continue
        detail = get_json(client, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job[0]}")
        normalized = _with_detail(job[1], detail)
        if normalized is not None:
            out.append(normalized)
    return out


def _fetch_summaries(client: httpx.Client, slug: str) -> list[dict[str, Any]] | None:
    """Paginate the list endpoint, keeping only in-scope postings.

    Returns None only when the board itself is missing, so a 404 stays
    distinguishable from a board that exists and has nothing for us.
    """
    first = get_json(client, LIST_URL.format(slug=slug, limit=PAGE_SIZE, offset=0))
    if first is None:
        return None
    total = int(first.get("totalFound") or 0) if isinstance(first, dict) else 0
    rows = list(_content(first))
    offset = PAGE_SIZE
    while offset < min(total, MAX_POSTINGS):
        page = get_json(client, LIST_URL.format(slug=slug, limit=PAGE_SIZE, offset=offset))
        if page is None:
            break
        batch = list(_content(page))
        if not batch:
            break
        rows.extend(batch)
        offset += PAGE_SIZE
    return rows


def _content(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    content = payload.get("content")
    return [row for row in content if isinstance(row, dict)] if isinstance(content, list) else []


def _normalize_summary(
    raw: dict[str, Any], slug: str, company: str, *, rules: ScopeRules | None = None
) -> tuple[str, NormalizedJob] | None:
    """Scope-check from list metadata alone, before paying for the detail call."""
    posting_id = str(raw.get("id") or "").strip()
    title = str(raw.get("name") or "").strip()
    if not posting_id or not title:
        return None
    function = raw.get("function")
    function_id = str(function.get("id") or "") if isinstance(function, dict) else ""
    if not function_in_scope(function_id, rules):
        return None
    if not title_in_scope(title, rules):
        return None
    location, remote = _location(raw.get("location"))
    return posting_id, NormalizedJob(
        source="smartrecruiters",
        source_id=f"{slug}:{posting_id}",
        company=company,
        title=title,
        location=location,
        remote=remote,
        apply_url="",
        source_url="",
        description_html="",
        description_text="",
        posted_at=_parse_dt(raw.get("releasedDate")),
    )


def _with_detail(job: NormalizedJob, detail: Any) -> NormalizedJob | None:
    """Fill in the description and urls. A posting with no url is unusable."""
    if not isinstance(detail, dict):
        return None
    url = str(detail.get("postingUrl") or "").strip()
    apply_url = str(detail.get("applyUrl") or "").strip() or url
    if not url:
        return None
    html, text = sanitize_html(_sections_html(detail))
    return NormalizedJob(
        source=job.source,
        source_id=job.source_id,
        company=job.company,
        title=job.title,
        location=job.location,
        remote=job.remote,
        apply_url=apply_url,
        source_url=url,
        description_html=html,
        description_text=text or job.title,
        posted_at=job.posted_at,
    )


def _sections_html(detail: dict[str, Any]) -> str:
    job_ad = detail.get("jobAd")
    sections = job_ad.get("sections") if isinstance(job_ad, dict) else None
    if not isinstance(sections, dict):
        return ""
    parts: list[str] = []
    for key, section in sections.items():
        if str(key).casefold() in _SKIP_SECTIONS or not isinstance(section, dict):
            continue
        body = str(section.get("text") or "").strip()
        if not body:
            continue
        heading = str(section.get("title") or "").strip()
        if heading:
            parts.append(f"<h3>{heading}</h3>")
        parts.append(body)
    return "\n".join(parts)


def _location(raw: Any) -> tuple[str | None, bool | None]:
    if not isinstance(raw, dict):
        return None, None
    bits = [str(raw.get(k) or "").strip() for k in ("city", "region", "country")]
    label = ", ".join(b for b in bits if b) or None
    remote = True if raw.get("remote") is True else None
    return label, remote


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
