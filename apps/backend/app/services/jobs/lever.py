from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.jobs.http import get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.types import NormalizedJob

BOARD_URL = "https://api.lever.co/v0/postings/{site}?mode=json"


def fetch_lever(client: httpx.Client, slug: str, company: str) -> list[NormalizedJob] | None:
    payload = get_json(client, BOARD_URL.format(site=slug))
    if payload is None:
        return None
    return parse_lever(payload, slug, company)


def parse_lever(payload: Any, slug: str, company: str) -> list[NormalizedJob]:
    rows = payload if isinstance(payload, list) else []
    out: list[NormalizedJob] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("id")
        title = str(raw.get("text") or "").strip()
        hosted = str(raw.get("hostedUrl") or raw.get("applyUrl") or "").strip()
        apply_url = str(raw.get("applyUrl") or hosted).strip()
        if job_id is None or not title or not apply_url:
            continue
        categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
        location = str(categories.get("location") or "").strip() or None
        html_src = ""
        if isinstance(raw.get("description"), str):
            html_src += raw["description"]
        if isinstance(raw.get("additional"), str):
            html_src += raw["additional"]
        html, text = sanitize_html(html_src)
        out.append(
            NormalizedJob(
                source="lever",
                source_id=f"{slug}:{job_id}",
                company=company,
                title=title,
                location=location,
                remote=_remote(raw, location),
                apply_url=apply_url,
                source_url=hosted or apply_url,
                description_html=html,
                description_text=text,
                posted_at=_parse_created(raw.get("createdAt")),
            )
        )
    return out


def _remote(raw: dict[str, Any], location: str | None) -> bool | None:
    workplace = str(raw.get("workplaceType") or "").lower()
    if workplace == "remote":
        return True
    if workplace in {"on-site", "onsite", "hybrid"}:
        return False
    if location and "remote" in location.lower():
        return True
    return None


def _parse_created(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        ms = float(value)
        if ms > 10_000_000_000:
            ms = ms / 1000.0
        return datetime.fromtimestamp(ms, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None
