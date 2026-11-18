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

And a third thing keeps it *permitted*: these boards sit behind Cloudflare, and
a run that spends its request budget at full speed gets the whole estate to stop
answering. See DETAIL_DELAY_SECONDS.

Because of (2) this source does not go through `ingest_board`; it has its own
entry point in `app.jobs.ingest_workday` and its own timer.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.jobs.http import RETRY_STATUSES, get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.scope import ScopeRules, title_in_scope
from app.services.jobs.types import NormalizedJob

PAGE_SIZE = 20  # Workday rejects anything larger with a 400.
# A guard so one enormous board cannot monopolise a pass. Boards are sorted
# newest-first by Workday, so a cap keeps the fresh end.
MAX_POSTINGS = 4000

# Pacing. This is the one source that pays a request per posting, so it is the
# one that earns a bot reputation: a cold board is thousands of sequential
# calls at whatever rate the network allows. Once the edge decides we are a
# bot it stops serving the board at all (see `_is_security_check`), and the
# reputation outlives the pass -- it was still refusing most tenants hours
# later. A pause between detail calls is what keeps a pass under that
# threshold; it costs a cold 4000-posting board ~27 extra minutes, which is
# affordable precisely because steady state only fetches genuinely new rows.
DETAIL_DELAY_SECONDS = 0.4

# The list POST had no retry at all, so one transient 429 on the first page
# failed the whole board.
LIST_ATTEMPTS = 3
LIST_BACKOFF_SECONDS = 1.5

# The statuses that really do mean "this board is not there": 404 for a tenant
# that has no such site, 422 for a wrong site slug on a real tenant, 406 for a
# nonsense tenant on the wildcard host. Documented in AGENTS.md section 8c,
# where adding a board requires a confirmed 200 against these.
NOT_FOUND_STATUSES = frozenset({404, 406, 422})


class WorkdayUnavailable(RuntimeError):
    """The board could not be read, for a reason other than not existing.

    Raised rather than returned as None so that the status reaches the ingest
    row. Collapsing every non-200 into None reported a throttled board as
    `board not found`, which points at the board config -- the one thing that
    is not wrong when the edge is challenging us.
    """

    def __init__(self, reason: str, *, url: str) -> None:
        self.reason = reason
        super().__init__(f"workday list: {reason} ({url})")


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
    detail_delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> WorkdayFetch | None:
    """None means the board is not there, never "nothing found".

    Any other reason the board could not be read raises `WorkdayUnavailable`,
    so the caller records the status rather than a guess at the cause.

    `detail_delay` defaults to DETAIL_DELAY_SECONDS; `sleep` is injected only so
    tests can run the pacing without waiting for it.
    """
    known = known_ids or set()
    delay = DETAIL_DELAY_SECONDS if detail_delay is None else detail_delay
    summaries = _list_postings(client, board, sleep=sleep)
    if summaries is None:
        return None

    new: list[NormalizedJob] = []
    still_live: set[str] = set()
    # Workday can list the same requisition more than once (two locations, or a
    # "-1" variant of the same path). upsert_jobs adds per row, so a repeat
    # inside one batch violates the unique constraint before it ever reaches a
    # second pass.
    batch_ids: set[str] = set()
    details = 0  # detail calls actually made, which is what needs pacing
    for raw in summaries:
        title = str(raw.get("title") or "").strip()
        path = str(raw.get("externalPath") or "").strip()
        if not title or not path:
            continue
        # Scope from list metadata: free, and skips the expensive call entirely.
        if not title_in_scope(title, rules):
            continue
        source_id = f"{board.slug}:{_req_id(raw) or path}"
        if source_id in batch_ids:
            continue
        batch_ids.add(source_id)
        if source_id in known:
            still_live.add(source_id)
            continue
        # Between calls, not before the first: a steady-state pass that finds
        # one new posting should not pay a pause for it.
        if details and delay > 0:
            sleep(delay)
        details += 1
        job = _fetch_detail(client, board, raw, source_id, title, path)
        if job is not None:
            new.append(job)
    return WorkdayFetch(new=new, still_live=still_live, fetched=len(summaries))


def _list_postings(
    client: httpx.Client,
    board: WorkdayBoard,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]] | None:
    url = board.api("/jobs")
    first = _post(client, url, 0, sleep=sleep)
    if first is None:
        return None
    total = int(first.get("total") or 0)
    rows = _postings(first)
    offset = PAGE_SIZE
    while offset < min(total, MAX_POSTINGS):
        page = _post(client, url, offset, sleep=sleep)
        if page is None:
            # Only offset 0 may legitimately say "not there". Past that the
            # board has already answered, so a missing page is a short read --
            # and a short read must not be returned: the caller hands these ids
            # to expire_missing, so one unreadable page in the middle of a
            # 145-page board would deactivate the entire tail of it. Failing
            # the board leaves the rows alone instead.
            raise WorkdayUnavailable(
                f"answered offset 0 then went missing at offset {offset}", url=url
            )
        batch = _postings(page)
        if not batch:
            # A real end, not a short read: `total` can run ahead of the pages.
            break
        rows.extend(batch)
        offset += PAGE_SIZE
    return rows


def _post(
    client: httpx.Client,
    url: str,
    offset: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """Workday's search is POST-only, so this cannot reuse http.get_json.

    None means one of NOT_FOUND_STATUSES -- the board is not there. Every other
    failure raises, because the status is the whole diagnosis.
    """
    body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
    last: httpx.Response | None = None
    for attempt in range(LIST_ATTEMPTS):
        try:
            response = client.post(url, json=body, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise WorkdayUnavailable(f"{type(exc).__name__}: {exc}", url=url) from exc
        last = response
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                raise WorkdayUnavailable(
                    "200 with a body that is not JSON", url=url
                ) from None
            if not isinstance(payload, dict):
                raise WorkdayUnavailable(
                    "200 with a JSON body that is not an object", url=url
                )
            return payload
        if response.status_code in NOT_FOUND_STATUSES:
            return None
        if response.status_code in RETRY_STATUSES and attempt + 1 < LIST_ATTEMPTS:
            sleep(LIST_BACKOFF_SECONDS * (attempt + 1))
            continue
        break
    assert last is not None
    raise WorkdayUnavailable(_status_reason(last), url=url)


def _status_reason(response: httpx.Response) -> str:
    if _is_security_check(response):
        # The shape seen on 2026-11-19: every tenant but one answering the list
        # POST with a Cloudflare interstitial under a 429, naming this host's
        # IP. Worth saying in the ingest row, because the fix is pacing and
        # patience, not a board config change.
        return f"HTTP {response.status_code}: edge served a security check, not the board"
    return f"HTTP {response.status_code}"


def _is_security_check(response: httpx.Response) -> bool:
    """A challenge page rather than an API response.

    Workday fronts these boards with Cloudflare, which answers a client it has
    decided is a bot with an HTML "Security Check" page -- and sends it under a
    429, so the status alone does not distinguish it from ordinary throttling.
    """
    if "html" not in response.headers.get("content-type", "").casefold():
        return False
    try:
        body = response.text.casefold()
    except (UnicodeDecodeError, httpx.HTTPError):
        return False
    return "security check" in body or "enable javascript" in body


def _postings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("jobPostings")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _req_id(raw: dict[str, Any]) -> str | None:
    """The requisition id from bulletFields, if there is actually one in there.

    Preferred over externalPath, which embeds the title and so changes whenever
    the title is edited. But bulletFields is a list of display labels, not an id
    field. Intel prefixes promoted postings with the literal "Spotlight Job" --
    `['Spotlight Job', 'JR0287775']` -- so taking element 0 collapsed 21 of its
    first 100 postings onto one source_id and failed the first production run on
    the unique constraint.

    So scan for the first entry that actually looks like an id: no whitespace,
    at least one digit. That finds the requisition number wherever it sits.
    """
    bullets = raw.get("bulletFields")
    if not isinstance(bullets, list):
        return None
    for item in bullets:
        text = str(item or "").strip()
        if text and not any(c.isspace() for c in text) and any(c.isdigit() for c in text):
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
