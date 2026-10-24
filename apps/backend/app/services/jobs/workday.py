"""Workday job boards.

Workday holds employers the other providers cannot reach -- NVIDIA, Micron,
Applied Materials, Intel, NXP, Marvell -- which is most of the competitive
semiconductor tier. It is also by far the most expensive source we have, and the
design here is entirely about that:

* The list endpoint is capped at 20 postings per call and carries almost
  nothing: a title, a path, a location string, and `postedOn` as the English
  phrase "Posted Yesterday". No description, no usable date.
* Everything real -- description, `startDate`, apply url -- lives behind a
  per-posting detail call at roughly 0.7s each.

Fetching all ~12k postings every pass would take 20-36 minutes and put 12,600
requests on Workday every time. So two things keep it affordable:

1. **Scope from the free list metadata first.** A posting we would not carry
   never costs a detail call.
2. **Only fetch details we do not already have.** A posting whose source_id is
   already stored is reported back as "still live" and skipped, so steady-state
   passes only pay for genuinely new listings. The caller must then touch those
   rows -- see `store.touch_jobs` for why that is not optional.

Because of (2) this source does not go through `ingest_board`; it has its own
entry point in `app.jobs.ingest_workday` and its own timer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.jobs.http import get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.scope import ScopeRules, title_in_scope
from app.services.jobs.types import NormalizedJob

PAGE_SIZE = 20  # Workday rejects anything larger with a 400.
# A guard so one enormous board cannot monopolise a pass. Boards are sorted
# newest-first by Workday, so a cap keeps the fresh end.
MAX_POSTINGS = 4000


@dataclass(frozen=True)
class WorkdayBoard:
    host: str  # e.g. "nvidia.wd5.myworkdayjobs.com"
    tenant: str  # e.g. "nvidia"
    site: str  # e.g. "NVIDIAExternalCareerSite"
    company: str

    @property
    def slug(self) -> str:
        """Stable per-board prefix for source_id, so expire_missing can scope to
        one board the way it does for the other providers."""
        return f"{self.tenant}-{self.site}".lower()

    def api(self, path: str = "") -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}{path}"


@dataclass(frozen=True)
class WorkdayFetch:
    """What one board pass found.

    `new` carries full postings to upsert. `still_live` is the source_ids we
    deliberately did not re-fetch; they must be touched and must be included in
    seen_ids, or expire_missing will deactivate the entire board.
    """

    new: list[NormalizedJob]
    still_live: set[str]
    fetched: int

    @property
    def seen_ids(self) -> set[str]:
        return {job.source_id for job in self.new} | self.still_live


def fetch_workday(
    client: httpx.Client,
    board: WorkdayBoard,
    *,
    known_ids: set[str] | None = None,
    rules: ScopeRules | None = None,
) -> WorkdayFetch | None:
    """None means the board itself could not be read, never "nothing found"."""
    known = known_ids or set()
    summaries = _list_postings(client, board)
    if summaries is None:
        return None

    new: list[NormalizedJob] = []
    still_live: set[str] = set()
    for raw in summaries:
        title = str(raw.get("title") or "").strip()
        path = str(raw.get("externalPath") or "").strip()
        if not title or not path:
            continue
        # Scope from list metadata: free, and skips the expensive call entirely.
        if not title_in_scope(title, rules):
            continue
        source_id = f"{board.slug}:{_req_id(raw) or path}"
        if source_id in known:
            still_live.add(source_id)
            continue
        job = _fetch_detail(client, board, raw, source_id, title, path)
        if job is not None:
            new.append(job)
    return WorkdayFetch(new=new, still_live=still_live, fetched=len(summaries))


def _list_postings(client: httpx.Client, board: WorkdayBoard) -> list[dict[str, Any]] | None:
    first = _post(client, board.api("/jobs"), 0)
    if first is None:
        return None
    total = int(first.get("total") or 0)
    rows = _postings(first)
    offset = PAGE_SIZE
    while offset < min(total, MAX_POSTINGS):
        page = _post(client, board.api("/jobs"), offset)
        if page is None:
            break
        batch = _postings(page)
        if not batch:
            break
        rows.extend(batch)
        offset += PAGE_SIZE
    return rows


def _post(client: httpx.Client, url: str, offset: int) -> dict[str, Any] | None:
    """Workday's search is POST-only, so this cannot reuse http.get_json."""
    body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
    try:
        response = client.post(url, json=body, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _postings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("jobPostings")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _req_id(raw: dict[str, Any]) -> str | None:
    """Workday puts the requisition id in bulletFields. Preferred over the path,
    which contains the title and therefore changes if the title is edited."""
    bullets = raw.get("bulletFields")
    if isinstance(bullets, list):
        for item in bullets:
            text = str(item or "").strip()
            if text:
                return text
    return None


def _fetch_detail(
    client: httpx.Client,
    board: WorkdayBoard,
    raw: dict[str, Any],
    source_id: str,
    title: str,
    path: str,
) -> NormalizedJob | None:
    detail = get_json(client, board.api(path))
    info = detail.get("jobPostingInfo") if isinstance(detail, dict) else None
    if not isinstance(info, dict):
        return None
    url = str(info.get("externalUrl") or "").strip()
    if not url:
        return None
    html, text = sanitize_html(str(info.get("jobDescription") or ""))
    location = str(info.get("location") or raw.get("locationsText") or "").strip() or None
    remote_type = str(info.get("remoteType") or "")
    return NormalizedJob(
        source="workday",
        source_id=source_id,
        company=board.company,
        title=str(info.get("title") or title).strip(),
        location=location,
        remote=True if "remote" in remote_type.casefold() else None,
        apply_url=url,
        source_url=url,
        description_html=html,
        description_text=text or title,
        # startDate is a real date; postedOn is the string "Posted Yesterday",
        # which is why we never read it.
        posted_at=_parse_date(info.get("startDate")),
    )


def _parse_date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
