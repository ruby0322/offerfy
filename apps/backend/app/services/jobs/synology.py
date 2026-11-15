"""Synology postings.

Synology is on none of the four ATS providers and has no Workday tenant
(probed 2026-11-15; see AGENTS.md section 8b), but it publishes its own keyless
JSON API at `career.synology.com/api`, and its `robots.txt` is `Disallow:` with
an empty value -- allow all. So it is reachable, and this is the fetcher.

Three things shape this module:

1. **The list payload carries the full description inline.** Measured over all
   32 live postings: the list's `description` is byte-identical to the detail
   call's. So unlike smartrecruiters and workday, this source does not *need* a
   detail call to produce a usable posting.
2. **But the requirements are detail-only.** `other_condition` -- the "You are
   expected to have:" block, filled on 32 of 32 -- appears nowhere in the list.
   That is the half of a posting this product cares most about, so we do pay for
   the detail call. It is affordable here in a way it is not for workday: the
   whole board is 32 postings, and scoping first takes it to ~19.
3. **A failed detail call must not lose the posting.** Because of (1) the list
   row alone is already a complete posting, so a detail call that 404s or
   errors falls back to it instead of dropping it. `_detail_or_none` is what
   makes that a swallow rather than a raise.

The list endpoint is not paginated -- `GET /api/enum/{location}` returns every
posting for that location in one response, Vietnam and Thailand ones included --
so `slug` here is a **location id**, not a company slug, and `HQ` is the only
one. Keeping it in the slug field means `source_id` is `HQ:204` and
`expire_missing`'s `prefix` scopes per location exactly as it does for the other
providers.

There is no posted date anywhere in either payload, so `posted_at` is always
None. The column allows it; `validThrough` falls back to the row's own
timestamps.
"""

from __future__ import annotations

from html import escape
from typing import Any

import httpx

from app.services.jobs.http import get_json
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.scope import ScopeRules, title_in_scope
from app.services.jobs.types import NormalizedJob

LIST_URL = "https://career.synology.com/api/enum/{location}?locale={locale}"
DETAIL_URL = "https://career.synology.com/api/position/{location}/{posting_id}?locale={locale}"
# The public posting page, which is also the apply page -- applications are a
# form on the posting itself, so there is no separate apply url to carry.
PAGE_URL = "https://career.synology.com/{locale}/{location}/position/{posting_id}"

# `en-global` returns English for every posting written in English and the
# original zh-TW/ja for the rest, which is what we want: the alternative
# (`zh-tw`) translates the chrome but not the body, so it would only make the
# English postings harder to scope.
LOCALE = "en-global"

# A cost guard on the detail calls, not a cap on coverage: a posting past this
# many still ships, from its list row alone. That distinction matters -- a cap
# that dropped postings would take them out of `seen_ids` and have
# `expire_missing` deactivate live jobs, which is the trap called out in
# workday.py. 32 postings today, so this is headroom, not a limit in practice.
MAX_DETAIL_CALLS = 500

# Bullet markers Synology's own text uses. The fullwidth ones are not typos:
# the zh-TW postings mix "- " with "－" inside a single block.
_BULLETS = ("- ", "– ", "* ", "• ", "－", "‧", "·")


def fetch_synology(
    client: httpx.Client, slug: str, company: str, *, rules: ScopeRules | None = None
) -> list[NormalizedJob] | None:
    """Fetch one Synology location. None means the location could not be read.

    `slug` is a location id (`HQ`), not a company slug -- see the module
    docstring.
    """
    payload = get_json(client, LIST_URL.format(location=slug, locale=LOCALE))
    if payload is None:
        return None
    rows = _rows(payload)
    out: list[NormalizedJob] = []
    spent = 0
    for raw in rows:
        posting_id = _posting_id(raw)
        title = str(raw.get("name") or "").strip()
        if not posting_id or not title:
            continue
        # Scope from the free list metadata, before paying for the detail call.
        if not title_in_scope(title, rules):
            continue
        detail: dict[str, Any] | None = None
        if spent < MAX_DETAIL_CALLS:
            spent += 1
            detail = _detail_or_none(client, slug, posting_id)
        out.append(_normalize(raw, detail, slug, posting_id, title, company))
    return out


def _rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("list")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _posting_id(raw: dict[str, Any]) -> str:
    value = raw.get("id")
    # `id_position` ("HQ0000204") is the human-facing code and `id` the numeric
    # key the API itself takes, so the url-bearing one is the id we store.
    if isinstance(value, bool) or value is None:
        return ""
    return str(value).strip()


def _detail_or_none(client: httpx.Client, location: str, posting_id: str) -> dict[str, Any] | None:
    """The detail payload's `data`, or None if it could not be had.

    Swallowed on purpose: the list row is already a complete posting (see the
    module docstring), so a detail failure should cost the requirements block
    and nothing else. Letting it raise would turn one bad posting into a failed
    board, and `ingest_board` records a failed board as a source error.
    """
    try:
        payload = get_json(
            client, DETAIL_URL.format(location=location, posting_id=posting_id, locale=LOCALE)
        )
    except httpx.HTTPError:
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _normalize(
    raw: dict[str, Any],
    detail: dict[str, Any] | None,
    slug: str,
    posting_id: str,
    title: str,
    company: str,
) -> NormalizedJob:
    url = PAGE_URL.format(locale=LOCALE, location=slug, posting_id=posting_id)
    location = _location(raw, detail)
    html, text = sanitize_html(_description_html(raw, detail))
    return NormalizedJob(
        source="synology",
        source_id=f"{slug}:{posting_id}",
        company=company,
        title=title,
        location=location,
        remote=_remote(location),
        apply_url=url,
        source_url=url,
        description_html=html,
        description_text=text,
        # Neither payload carries one, in any field, at any locale.
        posted_at=None,
    )


def _description_html(raw: dict[str, Any], detail: dict[str, Any] | None) -> str:
    """Compose the posting body from the plain-text fields, in reading order.

    No headings are added, because Synology's own text already labels its
    blocks ("You are expected to have:", "我們期待你具備："). The role leads and
    `team_desc` -- which is team-and-company prose, and the nearest thing here
    to the boilerplate `_SKIP_SECTIONS` drops at smartrecruiters -- goes last,
    so the top of every posting is the posting rather than the pitch.
    """
    source = detail or raw
    blocks = [
        str(source.get("description") or raw.get("description") or ""),
        str(source.get("other_condition") or ""),
        str(source.get("skill") or ""),
        str(source.get("major_prefer") or ""),
        str(source.get("team_desc") or ""),
    ]
    return "\n".join(_html_from_text(b) for b in blocks if b.strip())


def _html_from_text(text: str) -> str:
    """Plain text to HTML the sanitizer keeps.

    The API serves plain text with "\\n", and `sanitize_html` escapes data and
    emits no tags of its own -- so handing it the raw string would render every
    posting as one unbroken paragraph. Text is escaped here and unescaped again
    by the sanitizer's `convert_charrefs`, which is what keeps a literal "<" in
    a posting from being eaten as a tag without double-escaping it.
    """
    out: list[str] = []
    for block in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        lines = [line.strip() for line in block.split("\n")]
        lines = [line for line in lines if line]
        # Runs, not whole blocks: Synology's lists arrive with their lead-in on
        # the same block as their items ("You are expected to have:" then a
        # column of "- "), so a block is a mix of prose and list and has to be
        # grouped rather than classified.
        for bulleted, run in _runs(lines):
            if bulleted:
                items = "".join(f"<li>{escape(_strip_bullet(line))}</li>" for line in run)
                out.append(f"<ul>{items}</ul>")
            else:
                out.append("<p>" + "<br>".join(escape(line) for line in run) + "</p>")
    return "".join(out)


def _runs(lines: list[str]) -> list[tuple[bool, list[str]]]:
    """Consecutive lines grouped by whether they are bulleted.

    A single bulleted line on its own stays prose: one stray dash in a
    paragraph is not a list, and wrapping it in `<ul>` would be noisier than
    leaving it alone.
    """
    runs: list[tuple[bool, list[str]]] = []
    for line in lines:
        bulleted = line.startswith(_BULLETS)
        if runs and runs[-1][0] == bulleted:
            runs[-1][1].append(line)
        else:
            runs.append((bulleted, [line]))
    out: list[tuple[bool, list[str]]] = []
    for bulleted, run in runs:
        if bulleted and len(run) < 2:
            # Demote to prose, merging into a preceding prose run if there is one.
            if out and not out[-1][0]:
                out[-1][1].extend(run)
            else:
                out.append((False, run))
            continue
        out.append((bulleted, run))
    return out


def _strip_bullet(line: str) -> str:
    for marker in _BULLETS:
        if line.startswith(marker):
            return line[len(marker) :].strip()
    return line


def _location(raw: dict[str, Any], detail: dict[str, Any] | None) -> str | None:
    for source in (detail or {}, raw):
        for key in ("place", "location"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _remote(location: str | None) -> bool | None:
    """None, not False: nothing in either payload states a work arrangement.

    Every posting today is an office in Taipei, Vietnam or Thailand, but a
    False here would be a claim the source never made.
    """
    if location and "remote" in location.casefold():
        return True
    return None
