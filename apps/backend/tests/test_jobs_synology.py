"""Synology's own-API source.

The fixtures are real payloads, trimmed: `synology_list.json` is a cut of
`GET /api/enum/HQ?locale=en-global` and `synology_details.json` holds the
`GET /api/position/HQ/<id>` responses keyed by id. Two of the five postings in
the list are out of scope and their details are present in the fixture on
purpose, so a test can prove they are never requested.

`Graphic Designer` (id 48) is in scope and deliberately has **no** detail
entry, which is what exercises the fallback: a detail call that fails must cost
the requirements block, never the posting.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.services.jobs import synology as synology_module
from app.services.jobs.synology import fetch_synology

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jobs"


def _client() -> tuple[httpx.Client, list[str]]:
    """A transport serving the list and the per-id details, recording paths."""
    seen: list[str] = []
    listing = json.loads((FIXTURES / "synology_list.json").read_text(encoding="utf-8"))
    details = json.loads((FIXTURES / "synology_details.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if "/api/enum/" in path:
            return httpx.Response(200, json=listing)
        posting_id = path.rsplit("/", 1)[-1]
        if posting_id in details:
            return httpx.Response(200, json=details[posting_id])
        # Exactly what the live API does for an id it does not have.
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _by_title(jobs) -> dict:
    return {job.title: job for job in jobs}


def test_parses_a_posting_from_the_list_plus_its_detail():
    client, _ = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None

    job = _by_title(jobs)["Product Developer (系統軟體開發)"]
    assert job.source == "synology"
    # The slug is the location, so the prefix expire_missing scopes on is "HQ:".
    assert job.source_id == "HQ:204"
    assert job.company == "Synology"
    assert job.location == "Taipei, Taiwan"
    assert job.apply_url == "https://career.synology.com/en-global/HQ/position/204"
    # The posting page is the apply page; there is no separate apply url.
    assert job.source_url == job.apply_url
    # The role, from the list payload...
    assert "Synology designs data management solutions" in job.description_text
    # ...and the requirements, which exist only in the detail payload.
    assert "You are expected to have" in job.description_text
    assert "Proficiency in C/C++ and Python or Go" in job.description_text


def test_posted_at_is_none_because_neither_payload_carries_a_date():
    """Not an oversight to be 'fixed' by inventing one: there is no date field
    in the list or the detail response at any locale."""
    client, _ = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    assert jobs != []
    assert all(job.posted_at is None for job in jobs)


def test_out_of_scope_postings_cost_no_detail_request():
    """Scope comes off the free list metadata first.

    Their details are in the fixture, so if the fetcher asked for them the test
    would pass on content and fail here -- which is the point.
    """
    client, seen = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    titles = {job.title for job in jobs}
    assert "Product Sales Specialist" not in titles
    assert "Executive Driver (行政專車司機)" not in titles
    detail_paths = [p for p in seen if "/api/position/" in p]
    assert not any(p.endswith(("/241", "/238")) for p in detail_paths)
    # Three kept postings, so three detail calls and one listing.
    assert len(detail_paths) == 3
    assert len([p for p in seen if "/api/enum/" in p]) == 1


def test_a_failed_detail_call_keeps_the_posting():
    """The list row is already a complete posting, so a 404 on the detail must
    cost the requirements and nothing else."""
    client, _ = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    job = _by_title(jobs)["Graphic Designer"]
    assert job.source_id == "HQ:48"
    # Body still present, from the list payload.
    assert "發揮你的想像力和設計能力詮釋品牌形象" in job.description_text
    # The detail-only block is simply absent.
    assert "我們期待您具備" not in job.description_text


def test_a_detail_transport_error_is_swallowed_too():
    """Not just 404: a connect error on one posting must not fail the board."""
    listing = json.loads((FIXTURES / "synology_list.json").read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/enum/" in request.url.path:
            return httpx.Response(200, json=listing)
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    assert len(jobs) == 3
    assert all(job.description_text for job in jobs)


def test_plain_text_becomes_paragraphs_and_lists():
    """The API serves plain text with newlines. Handing that straight to the
    sanitizer would render a posting as one unbroken paragraph."""
    client, _ = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    html = _by_title(jobs)["Product Developer (系統軟體開發)"].description_html
    assert "<p>" in html
    # The "- " bulleted requirements become a real list, with the marker gone.
    assert "<ul><li>" in html
    assert "<li>Proficiency in C/C++ and Python or Go</li>" in html
    assert "<li>- " not in html


def test_a_literal_angle_bracket_survives_without_double_escaping():
    """The text is escaped before the sanitizer and unescaped again by its
    convert_charrefs, so "< 5ms" must come back as text, not a dropped tag and
    not "&amp;lt;"."""
    client, _ = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    job = _by_title(jobs)["Product Developer (系統軟體開發)"]
    assert "tail latency < 5ms & correctness" in job.description_text
    assert "&lt; 5ms" in job.description_html
    assert "&amp;lt;" not in job.description_html


def test_team_desc_goes_last_so_the_posting_leads():
    client, _ = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    text = _by_title(jobs)["Product Developer (系統軟體開發)"].description_text
    assert text.index("You are expected to have") < text.index(
        "Synology's product developers create cutting-edge products"
    )


def test_remote_is_none_not_false_when_the_payload_says_nothing():
    client, _ = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    assert all(job.remote is None for job in jobs)


def test_the_detail_cap_limits_cost_without_dropping_postings(monkeypatch):
    """A cap that dropped postings would take them out of seen_ids and have
    expire_missing deactivate live jobs -- the trap workday.py calls out."""
    monkeypatch.setattr(synology_module, "MAX_DETAIL_CALLS", 1)
    client, seen = _client()
    jobs = fetch_synology(client, "HQ", "Synology")
    assert jobs is not None
    # Still every in-scope posting...
    assert len(jobs) == 3
    assert all(job.description_text for job in jobs)
    # ...on one detail request.
    assert len([p for p in seen if "/api/position/" in p]) == 1


def test_a_missing_location_returns_none_not_empty():
    """None means "could not read it" and must stay distinct from "nothing for
    us": expire_missing skips a failed fetch and would otherwise deactivate
    every Synology posting."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_synology(client, "nope", "Synology") is None


@pytest.mark.parametrize("payload", [{}, {"list": None}, {"list": [1, 2]}, []])
def test_a_junk_list_payload_is_empty_not_an_exception(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_synology(client, "HQ", "Synology") == []


def test_a_row_with_no_id_or_title_is_skipped():
    listing = {"list": [{"id": None, "name": "Software Developer"}, {"id": 9, "name": "  "}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=listing)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_synology(client, "HQ", "Synology") == []
