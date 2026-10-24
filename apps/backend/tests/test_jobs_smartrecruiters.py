import json
from pathlib import Path

import httpx

from app.services.jobs.smartrecruiters import fetch_smartrecruiters

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jobs"


def _client() -> tuple[httpx.Client, list[str]]:
    """A transport that serves the list payload and one detail payload.

    It records every path so the test can prove we do not pay a detail request
    for a posting the scope rules already rejected.
    """
    seen: list[str] = []
    listing = json.loads((FIXTURES / "smartrecruiters_list.json").read_text())
    detail = json.loads((FIXTURES / "smartrecruiters_detail.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json=listing)
        return httpx.Response(200, json=detail)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_parses_postings_and_skips_the_company_boilerplate():
    client, _ = _client()
    jobs = fetch_smartrecruiters(client, "grab", "Grab")
    assert jobs is not None
    by_title = {j.title: j for j in jobs}

    job = by_title["Senior Software Engineer, Payments"]
    assert job.source == "smartrecruiters"
    assert job.source_id == "grab:744000157446751"
    assert job.company == "Grab"
    assert job.location == "Singapore, sg"
    assert job.source_url == "https://jobs.smartrecruiters.com/Grab/744000157446751"
    assert job.apply_url.endswith("/apply")
    assert "Build payment systems" in job.description_text
    assert "5 years of backend work" in job.description_text
    # companyDescription is identical on every posting at a company; including it
    # would make hundreds of job pages near-duplicates.
    assert "superapp" not in job.description_text
    assert job.posted_at is not None and job.posted_at.year == 2026


def test_the_sales_function_is_dropped_without_a_detail_request():
    """Scope is applied to the cheap list metadata first.

    One request per posting is the whole cost of this source, so a posting we
    are not going to keep must not cost one.
    """
    client, seen = _client()
    jobs = fetch_smartrecruiters(client, "grab", "Grab")
    assert jobs is not None
    assert "Enterprise Account Executive" not in {j.title for j in jobs}
    assert "744000157446752" not in " ".join(seen)
    # Two kept postings, so exactly two detail requests plus the one listing.
    assert len([p for p in seen if not p.endswith("/postings")]) == 2


def test_remote_flag_comes_from_the_structured_location():
    client, _ = _client()
    jobs = fetch_smartrecruiters(client, "grab", "Grab")
    assert jobs is not None
    remote = {j.title: j.remote for j in jobs}
    assert remote["Staff Data Scientist"] is True
    assert remote["Senior Software Engineer, Payments"] is None


def test_missing_board_returns_none_not_empty():
    """None means "no such board" and must stay distinct from "nothing for us",
    because expire_missing skips a failed fetch and would otherwise deactivate
    the whole company."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_smartrecruiters(client, "nope", "Nope") is None
