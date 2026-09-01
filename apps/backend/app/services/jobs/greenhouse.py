from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.jobs.http import get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.types import NormalizedJob

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def fetch_greenhouse(client: httpx.Client, slug: str, company: str) -> list[NormalizedJob] | None:
    payload = get_json(client, BOARD_URL.format(token=slug))
    if payload is None:
        return None
    return parse_greenhouse(payload, slug, company)


def parse_greenhouse(payload: Any, slug: str, company: str) -> list[NormalizedJob]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []
    out: list[NormalizedJob] = []
    for raw in jobs:
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("id")
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("absolute_url") or "").strip()
        if job_id is None or not title or not url:
            continue
        location = None
        loc = raw.get("location")
        if isinstance(loc, dict):
            location = str(loc.get("name") or "").strip() or None
        html, text = sanitize_html(raw.get("content") if isinstance(raw.get("content"), str) else "")
        out.append(
            NormalizedJob(
                source="greenhouse",
                source_id=f"{slug}:{job_id}",
                company=company,
                title=title,
                location=location,
                remote=_remote_from_location(location),
                apply_url=url,
                source_url=url,
                description_html=html,
                description_text=text,
                posted_at=_parse_dt(raw.get("updated_at") or raw.get("first_published")),
            )
        )
    return out


def _remote_from_location(location: str | None) -> bool | None:
    if not location:
        return None
    lowered = location.lower()
    if "remote" in lowered or "anywhere" in lowered:
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
