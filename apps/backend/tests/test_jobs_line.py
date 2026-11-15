"""LINE is the first source whose contract is a build artifact rather than an
API, so these tests pin the things a Gatsby redeploy could quietly change.

The fixtures were generated from the live payload and trimmed to the keys
`fetch_line` actually reads, so `line_list.json` doubles as a statement of the
contract this source depends on.
"""

import json
from pathlib import Path

import httpx

from app.services.jobs.line import fetch_line

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jobs"


def _client(
    *, listing: dict | None = None, empty_detail_for: str = "3108"
) -> tuple[httpx.Client, list[str]]:
    """Serve the list payload and a detail payload per posting.

    Every path is recorded, because the whole cost of this source is one detail
    request per posting and a posting we reject must not cost one.
    """
    seen: list[str] = []
    rows = listing if listing is not None else json.loads((FIXTURES / "line_list.json").read_text())
    detail = json.loads((FIXTURES / "line_detail.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path == "/page-data/jobs/page-data.json":
            return httpx.Response(200, json=rows)
        if f"/{empty_detail_for}/" in path:
            blank = {"result": {"data": {"strapiJobs": {"content": "", "apply_link": ""}}}}
            return httpx.Response(200, json=blank)
        return httpx.Response(200, json=detail)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _titles(jobs: list) -> set[str]:
    return {job.title for job in jobs}


def test_parses_a_posting_into_the_normalized_shape():
    client, _ = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    job = next(j for j in jobs if j.title == "Server-Side Engineer(EPI)")

    assert job.source == "line"
    assert job.source_id == "linecorp:3101"
    assert job.location == "Taipei"
    # The site publishes no remote flag at all, and inferring one from a title
    # is how "Remote Support Engineer" becomes a remote job.
    assert job.remote is None
    assert job.source_url == "https://careers.linecorp.com/jobs/3101"
    assert job.apply_url == (
        "https://careers.linecorp-global.com/careers/JobApplication?jobId=2082"
    )
    assert "Build the payment server." in job.description_text
    assert "5 years of backend work" in job.description_text
    assert "alert(1)" not in job.description_html
    assert job.posted_at is not None


def test_source_id_is_prefixed_so_expire_missing_can_scope_to_this_board():
    """ingest_board expires by `f"{board.slug}:"`, so every id must carry it or
    one pass would deactivate the whole source."""
    client, _ = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    assert jobs
    assert all(job.source_id.startswith("linecorp:") for job in jobs)


def test_unpublished_drafts_are_skipped_and_cost_no_detail_request():
    """The list payload carries every posting Strapi knows about: at the time of
    writing 306 of 387 are unpublished. Ingesting them would quadruple the board
    with jobs nobody can apply to."""
    client, seen = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    assert "Unpublished Draft Engineer" not in _titles(jobs)
    assert not any("/3103/" in path for path in seen)


def test_publish_is_checked_with_is_true_so_a_string_false_does_not_pass():
    """If a shape change ever made `publish` a string, `"False"` would be truthy
    and the board would quadruple silently. This is the regression guard."""
    client, seen = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    assert "Stringly Published Engineer" not in _titles(jobs)
    assert not any("/3104/" in path for path in seen)


def test_an_out_of_scope_title_costs_no_detail_request():
    client, seen = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    assert "Technical Recruiter, Hardware" not in _titles(jobs)
    assert not any("/3102/" in path for path in seen)


def test_a_closed_application_window_is_dropped():
    client, _ = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    assert "Expired Window Engineer" not in _titles(jobs)


def test_the_whole_family_collapses_to_one_employer():
    """Deliberate, and lossy: the site's 13 legal entities are all filed under
    the board row's name so that one employer page and one LINE mark cover the
    family, instead of ten pages whose monograms collided four ways on "LP".
    """
    client, _ = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    assert jobs
    assert {job.company for job in jobs} == {"LINE"}


def test_a_postings_own_entity_never_reaches_the_company_field():
    """The fixture rows carry real entities -- LINE Taiwan, LINE Plus, and a
    "LINE Investment Technologies " stored with a trailing space -- and none may
    leak through, or the family splits back into separate employer pages. The
    payload still has them; we just do not read them.
    """
    client, _ = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    entities = {
        "LINE Taiwan",
        "LINE Plus",
        "LINE Investment Technologies",
        "LINE Investment Technologies ",
    }
    assert entities.isdisjoint({job.company for job in jobs})
    assert all(job.company == "LINE" for job in jobs)


def test_the_localised_title_is_used_when_there_is_no_english_one():
    """96 of 387 postings have no title_en, so it is the fallback that has to
    work, not the happy path."""
    client, _ = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    job = next(j for j in jobs if j.title == "일본어 전문 소프트웨어 엔지니어")
    assert job.location == "Bundang"


def test_a_posting_with_no_description_is_dropped_rather_than_published_empty():
    client, _ = _client()
    jobs = fetch_line(client, "linecorp", "LINE")
    assert jobs is not None
    assert "No Description Engineer" not in _titles(jobs)


def test_a_shape_change_reads_as_board_not_found_rather_than_an_empty_board():
    """The contract is a build artifact nobody promised us. If the jobs
    collection stops being where we look, that has to surface as a failed board
    -- which `refreshed_nothing` counts -- and never as "LINE has no openings",
    which would expire every row we hold.
    """
    client, _ = _client(listing={"result": {"data": {}}})
    assert fetch_line(client, "linecorp", "LINE") is None

    client, _ = _client(listing={"nothing": "like the real thing"})
    assert fetch_line(client, "linecorp", "LINE") is None


def test_a_missing_site_reads_as_board_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_line(client, "linecorp", "LINE") is None
