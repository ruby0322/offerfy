from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.jobs.http import get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.types import NormalizedJob

BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def fetch_ashby(client: httpx.Client, slug: str, company: str) -> list[NormalizedJob] | None:
    payload = get_json(client, BOARD_URL.format(slug=slug))
    if payload is None:
        return None
    return parse_ashby(payload, slug, company)


def parse_ashby(payload: Any, slug: str, company: str) -> list[NormalizedJob]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []
    out: list[NormalizedJob] = []
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("id") or raw.get("jobId")
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("jobUrl") or raw.get("applyUrl") or "").strip()
        apply_url = str(raw.get("applyUrl") or url).strip()
        if job_id is None or not title or not apply_url:
            continue
        location = _location(raw)
        html_src = raw.get("descriptionHtml") or raw.get("description") or ""
        html, text = sanitize_html(html_src if isinstance(html_src, str) else "")
        out.append(
            NormalizedJob(
                source="ashby",
                source_id=f"{slug}:{job_id}",
                company=company,
                title=title,
                location=location,
                remote=_remote(raw, location),
                apply_url=apply_url,
                source_url=url or apply_url,
                description_html=html,
                description_text=text,
                posted_at=_parse_dt(raw.get("publishedAt") or raw.get("publishedDate")),
            )
        )
    return out


def _location(raw: dict[str, Any]) -> str | None:
    for key in ("location", "locationName"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            name = str(value.get("name") or "").strip()
            if name:
                return name
    return None


def _remote(raw: dict[str, Any], location: str | None) -> bool | None:
    if isinstance(raw.get("isRemote"), bool):
        return raw["isRemote"]
    workplace = str(raw.get("workplaceType") or raw.get("employmentType") or "").lower()
    if "remote" in workplace:
        return True
    if location and "remote" in location.lower():
        return True
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
