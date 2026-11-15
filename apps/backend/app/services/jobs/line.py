"""LINE / LY Corporation postings.

The fifth provider, and the first one that is not somebody's ATS. LINE
publishes on none of the four -- every plausible slug 404s on Greenhouse,
Lever and Ashby, gives the meaningless SmartRecruiters 200/`totalFound: 0`, and
422s on Workday (AGENTS.md section 8b records the full probe). What it has
instead is `careers.linecorp.com`, a Gatsby site over a Strapi backend, and
Gatsby ships its page queries as JSON next to the HTML. That JSON is readable
without a key and `robots.txt` disallows only two 2021 paths, so unlike Google
there is no wall here -- which is why this source exists at all.

It was worth building for one number: of 81 published postings, **53 are
Taipei** (LINE Taiwan, LINE Pay Taiwan), the largest block of Taiwan
engineering roles the catalogue did not have.

Four things make this source unlike the others, and all four are load-bearing:

1. **The contract is a build artifact, not an API.** `/page-data/<path>/page-data.json`
   is how Gatsby hydrates a page; nobody promised it to us and a redeploy could
   reshape it. So every field is read defensively and a row that does not make
   sense is skipped rather than guessed at. A shape change must degrade to "this
   board returned nothing", which `refreshed_nothing` already treats as a failed
   pass -- never to a traceback mid-pass, and never to rows full of `None`.

2. **`publish` is the filter that matters.** The list payload carries **every**
   posting Strapi knows about, published or not: at the time of writing, 306 of
   387 are unpublished drafts. Ingesting the list as-is would quadruple the board
   with jobs nobody can apply to. `publish is True` is checked with `is`, not for
   truthiness, because a shape change that turns the field into a string would
   make `"False"` truthy and let all 387 through.

3. **Descriptions are not in the list payload**, the SmartRecruiters problem
   again, so the scope rules are applied to the cheap list metadata first and a
   detail request is only spent on a posting we mean to keep.

4. **The employer is on the posting, not the board.** One site carries 13
   employers -- LINE Taiwan, LINE Pay Taiwan, LINE Plus, LINE Bank Taiwan, IPX
   and more -- which are genuinely different companies, so each posting's own
   `companies[0].name` becomes its `company` and the board's name is only a
   fallback. That is what makes `/jobs/employers/line-taiwan` a real page rather
   than 81 postings filed under one fictional "LINE".

**`job_fields` is a structured taxonomy we deliberately do not use yet.** Each
posting carries one ("Server-side", "Data Engineering", "Human Resources"), and
it is a far better scope signal than a title -- LINE's titles are Korean,
Japanese and Thai as often as English, and the deny list has needles for neither
Korean nor Japanese, so `Senior HR Generalist` and `CS representative(EPI)`
currently pass `title_in_scope` as technical roles. Gating on `job_fields` would
fix that, but section 8b's rule is that a scope change is measured in both
directions over the whole live corpus in one pass, and this taxonomy has 58
values of which a dozen are literally prefixed "(not in use)". Measure it before
wiring it in; do not add a keep-list here on taste.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.jobs.http import get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.scope import ScopeRules, title_in_scope
from app.services.jobs.types import NormalizedJob

SITE = "https://careers.linecorp.com"
LIST_URL = f"{SITE}/page-data/jobs/page-data.json"
DETAIL_URL = f"{SITE}/page-data/jobs/{{posting_id}}/page-data.json"
# A guard, not a quota: one board should not be able to spend the whole pass.
MAX_POSTINGS = 1000


def fetch_line(
    client: httpx.Client, slug: str, company: str, *, rules: ScopeRules | None = None
) -> list[NormalizedJob] | None:
    """None means the site could not be read, never "nothing published"."""
    summaries = _fetch_summaries(client)
    if summaries is None:
        return None
    out: list[NormalizedJob] = []
    for raw in summaries[:MAX_POSTINGS]:
        pending = _normalize_summary(raw, slug, company, rules=rules)
        if pending is None:
            continue
        posting_id, job = pending
        detail = get_json(client, DETAIL_URL.format(posting_id=posting_id))
        normalized = _with_detail(job, detail)
        if normalized is not None:
            out.append(normalized)
    return out


def _fetch_summaries(client: httpx.Client) -> list[dict[str, Any]] | None:
    """Every posting Strapi knows about, published or not. Unpaginated: Gatsby
    inlines the whole collection, which is the one way this source is cheaper
    than the ATS providers."""
    payload = get_json(client, LIST_URL)
    nodes = _nodes(payload, "allStrapiJobs")
    # A reachable site whose payload has no jobs collection at all is a shape
    # change, not an empty board, and must not read as "LINE has no openings".
    return None if nodes is None else nodes


def _nodes(payload: Any, collection: str) -> list[dict[str, Any]] | None:
    """Unwrap Gatsby's result.data.<collection>.edges[].node."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("result")
    data = data.get("data") if isinstance(data, dict) else None
    group = data.get(collection) if isinstance(data, dict) else None
    edges = group.get("edges") if isinstance(group, dict) else None
    if not isinstance(edges, list):
        return None
    out: list[dict[str, Any]] = []
    for edge in edges:
        node = edge.get("node") if isinstance(edge, dict) else None
        if isinstance(node, dict):
            out.append(node)
    return out


def _normalize_summary(
    raw: dict[str, Any], slug: str, company: str, *, rules: ScopeRules | None = None
) -> tuple[str, NormalizedJob] | None:
    """Scope-check from list metadata alone, before paying for the detail call."""
    if raw.get("publish") is not True:
        return None
    posting_id = str(raw.get("strapiId") or "").strip()
    title = _title(raw)
    if not posting_id or not title:
        return None
    if _closed(raw.get("end_date")):
        return None
    if not title_in_scope(title, rules):
        return None
    return posting_id, NormalizedJob(
        source="line",
        source_id=f"{slug}:{posting_id}",
        company=_company(raw, company),
        title=title,
        location=_location(raw),
        # The site has no remote flag of any kind, and guessing one from a
        # title is how "Remote Support Engineer" becomes a remote job.
        remote=None,
        apply_url="",
        source_url="",
        description_html="",
        description_text="",
        posted_at=_parse_dt(raw.get("start_date")),
    )


def _with_detail(job: NormalizedJob, detail: Any) -> NormalizedJob | None:
    """Fill in the description and the apply url from the posting's own page."""
    node = None
    if isinstance(detail, dict):
        data = detail.get("result")
        data = data.get("data") if isinstance(data, dict) else None
        node = data.get("strapiJobs") if isinstance(data, dict) else None
    if not isinstance(node, dict):
        return None
    html, text = sanitize_html(str(node.get("content") or ""))
    if not html.strip():
        # No description means the detail page did not really answer for this
        # posting. Carrying it would publish an empty job page.
        return None
    source_url = f"{SITE}/jobs/{job.source_id.split(':', 1)[-1]}"
    apply_url = str(node.get("apply_link") or "").strip() or source_url
    return NormalizedJob(
        source=job.source,
        source_id=job.source_id,
        company=job.company,
        title=job.title,
        location=job.location,
        remote=job.remote,
        apply_url=apply_url,
        source_url=source_url,
        description_html=html,
        description_text=text or job.title,
        posted_at=job.posted_at,
    )


def _title(raw: dict[str, Any]) -> str:
    """Prefer the English title, which is what the scope rules can read.

    96 of 387 postings have no `title_en`, so the localised `title` is the
    fallback rather than the other way round.
    """
    for key in ("title_en", "title"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    return ""


def _company(raw: dict[str, Any], fallback: str) -> str:
    companies = raw.get("companies")
    if isinstance(companies, list):
        for entry in companies:
            if isinstance(entry, dict):
                # Strapi has at least one name stored with a trailing space
                # ("LINE Investment Technologies "), which would otherwise be a
                # second employer slug for the same company.
                name = str(entry.get("name") or "").strip()
                if name:
                    return name
    return fallback


def _location(raw: dict[str, Any]) -> str | None:
    city = ""
    cities = raw.get("cities")
    if isinstance(cities, list):
        for entry in cities:
            if isinstance(entry, dict):
                city = str(entry.get("name") or "").strip()
                if city:
                    break
    return city or None


def _closed(value: Any) -> bool:
    """Whether an application window has already shut.

    Most postings have no `end_date` and the dated ones lean on a 2999 sentinel,
    so this only ever excludes a genuinely expired window.
    """
    end = _parse_dt(value)
    return end is not None and end < datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
