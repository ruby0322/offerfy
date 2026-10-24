import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.models import Job, JobIngestRun
from app.services.jobs.ashby import parse_ashby
from app.services.jobs.boards import boards_path, load_boards, load_workday_boards
from app.services.jobs.greenhouse import parse_greenhouse
from app.services.jobs.ingest import CatalogResult, SourceResult, ingest_board
from app.services.jobs.lever import parse_lever
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.store import expire_missing, record_run, upsert_jobs
from app.services.jobs.types import JOB_SOURCES, Board, NormalizedJob

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jobs"
BACKEND_DATA = Path(__file__).resolve().parents[1] / "data"


def test_sanitize_strips_script_and_javascript_href():
    html, text = sanitize_html(
        '<p>Hi<script>alert(1)</script><a href="javascript:alert(1)">x</a>'
        '<a href="https://offerfy.cc">ok</a></p>'
    )
    assert "script" not in html.lower()
    assert "javascript:" not in html.lower()
    assert "https://offerfy.cc" in html
    assert "rel=\"noopener noreferrer\"" in html
    assert "Hi" in text
    assert "ok" in text


def test_sanitize_unescapes_greenhouse_entities():
    html, text = sanitize_html("&lt;p&gt;Build payments.&lt;/p&gt;")
    assert "<p>" in html
    assert "Build payments." in text


def test_parse_greenhouse_fixture():
    payload = json.loads((FIXTURES / "greenhouse.json").read_text(encoding="utf-8"))
    jobs = parse_greenhouse(payload, "stripe", "Stripe")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source_id == "stripe:4242"
    assert job.company == "Stripe"
    assert job.remote is True
    assert "script" not in job.description_html.lower()
    assert "javascript:" not in job.description_html.lower()
    assert "https://stripe.com/jobs" in job.description_html
    assert "Build payments." in job.description_text


def test_parse_lever_and_ashby_fixtures():
    lever = parse_lever(
        json.loads((FIXTURES / "lever.json").read_text(encoding="utf-8")),
        "spotify",
        "Spotify",
    )
    assert lever[0].source == "lever"
    assert lever[0].remote is True
    assert lever[0].title == "Backend Engineer"
    ashby = parse_ashby(
        json.loads((FIXTURES / "ashby.json").read_text(encoding="utf-8")),
        "notion",
        "Notion",
    )
    assert ashby[0].source_id == "notion:job-9"
    assert ashby[0].remote is False


def test_load_boards_skips_duplicates():
    boards = load_boards()
    keys = {(b.source, b.slug) for b in boards}
    assert len(keys) == len(boards)
    assert ("greenhouse", "stripe") in keys
    assert ("ashby", "openai") in keys


def test_shipped_board_list_is_curated():
    """Every row must name a provider we can actually fetch.

    The list once carried 64 boards that 404ed on every pass -- companies that
    had moved ATS or never used one of these -- which buried the real failures.
    Liveness itself is checked by /admin/jobs rather than here: probing 77 boards
    over the network would make this suite slow and flaky.
    """
    raw = json.loads((BACKEND_DATA / "ats_boards.json").read_text(encoding="utf-8"))
    rows = raw["boards"]
    assert len(rows) == len(load_boards()), "a row was dropped by load_boards"
    for row in rows:
        assert set(row) == {"source", "slug", "company"}, row
        assert row["source"] in JOB_SOURCES, row
        # taiwanjobs is retired; load_boards drops it, so a row here is a mistake.
        assert row["source"] != "taiwanjobs", row
        assert row["slug"] and row["slug"].strip() == row["slug"], row
        assert row["company"] and row["company"].strip() == row["company"], row


def test_upsert_and_expire_per_board(db_session):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    first = NormalizedJob(
        source="greenhouse",
        source_id="stripe:1",
        company="Stripe",
        title="A",
        location="Remote",
        remote=True,
        apply_url="https://example.com/a",
        source_url="https://example.com/a",
        description_html="<p>a</p>",
        description_text="a",
        posted_at=now,
    )
    second = NormalizedJob(
        source="greenhouse",
        source_id="stripe:2",
        company="Stripe",
        title="B",
        location="NYC",
        remote=False,
        apply_url="https://example.com/b",
        source_url="https://example.com/b",
        description_html="<p>b</p>",
        description_text="b",
        posted_at=now,
    )
    other = NormalizedJob(
        source="greenhouse",
        source_id="airbnb:9",
        company="Airbnb",
        title="C",
        location=None,
        remote=None,
        apply_url="https://example.com/c",
        source_url="https://example.com/c",
        description_html="",
        description_text="",
        posted_at=None,
    )
    upsert_jobs(db_session, [first, second, other], now=now)
    db_session.commit()
    expired = expire_missing(
        db_session,
        source="greenhouse",
        seen_ids={"stripe:1"},
        prefix="stripe:",
    )
    db_session.commit()
    assert expired == 1
    stripe1 = (
        db_session.query(Job)
        .filter(Job.source == "greenhouse", Job.source_id == "stripe:1")
        .one()
    )
    stripe2 = (
        db_session.query(Job)
        .filter(Job.source == "greenhouse", Job.source_id == "stripe:2")
        .one()
    )
    airbnb = (
        db_session.query(Job)
        .filter(Job.source == "greenhouse", Job.source_id == "airbnb:9")
        .one()
    )
    assert stripe1.is_active is True
    assert stripe2.is_active is False
    assert airbnb.is_active is True


def test_failed_board_fetch_does_not_expire(db_session):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    upsert_jobs(
        db_session,
        [
            NormalizedJob(
                source="greenhouse",
                source_id="stripe:1",
                company="Stripe",
                title="A",
                location=None,
                remote=None,
                apply_url="https://example.com/a",
                source_url="https://example.com/a",
                description_html="",
                description_text="a",
                posted_at=None,
            )
        ],
        now=now,
    )
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ingest_board(db_session, client, Board("greenhouse", "stripe", "Stripe"))
    row = (
        db_session.query(Job)
        .filter(Job.source == "greenhouse", Job.source_id == "stripe:1")
        .one()
    )
    assert row.is_active is True


def test_failed_board_fetch_reports_an_error_result(db_session):
    """A swallowed per-board failure still has to be visible to the caller."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ingest_board(db_session, client, Board("greenhouse", "stripe", "Stripe"))
    assert result.status == "error"
    assert result.upserted == 0


def test_catalog_result_flags_a_pass_that_refreshed_nothing():
    """The condition the systemd unit exits non-zero on.

    All sources failing, or every source returning nothing, both leave the
    catalog ageing towards expiry — which is the outage this guards.
    """
    all_failed = CatalogResult(
        (
            SourceResult("greenhouse", "stripe", "error", error="boom"),
            SourceResult("taiwanjobs", None, "error", error="boom"),
        ),
        run_id="pass-1",
    )
    assert all_failed.refreshed_nothing is True
    assert len(all_failed.failed) == 2

    ok_but_empty = CatalogResult(
        (SourceResult("greenhouse", "stripe", "ok", upserted=0),), run_id="pass-2"
    )
    assert ok_but_empty.refreshed_nothing is True

    partial = CatalogResult(
        (
            SourceResult("greenhouse", "stripe", "ok", upserted=12),
            SourceResult("taiwanjobs", None, "error", error="boom"),
        ),
        run_id="pass-3",
    )
    # One dead board must not fail the whole run: the catalog did get refreshed.
    assert partial.refreshed_nothing is False
    assert partial.upserted == 12
    assert len(partial.failed) == 1


def test_ingest_board_stamps_the_pass_run_id(db_session):
    """Every row of a pass must carry the same run_id, or it cannot be folded up."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ingest_board(db_session, client, Board("greenhouse", "stripe", "Stripe"), run_id="pass-a")
    ingest_board(db_session, client, Board("lever", "acme", "Acme"), run_id="pass-a")
    ingest_board(db_session, client, Board("greenhouse", "other", "Other"), run_id="pass-b")

    rows = db_session.query(JobIngestRun).all()
    by_run: dict[str | None, int] = {}
    for row in rows:
        by_run[row.run_id] = by_run.get(row.run_id, 0) + 1
    assert by_run == {"pass-a": 2, "pass-b": 1}


def test_record_run_without_a_run_id_is_still_allowed(db_session):
    """Rows written before migration 014 have no run_id; that must not break."""
    record_run(
        db_session,
        source="greenhouse",
        board_slug="stripe",
        started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        status="ok",
    )
    db_session.commit()
    row = db_session.query(JobIngestRun).one()
    assert row.run_id is None


def test_out_of_scope_postings_are_not_stored_and_get_expired(db_session):
    """The scope filter must run before seen_ids.

    A posting we stop carrying has to drop out of seen_ids so expire_missing
    deactivates it on this same pass. If it were filtered afterwards, every
    out-of-scope page we ever ingested would stay indexed forever.
    """
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    upsert_jobs(
        db_session,
        [
            NormalizedJob(
                source="greenhouse", source_id="stripe:1", company="Stripe",
                title="Enterprise Account Executive", location="NYC", remote=None,
                apply_url="https://example.com/a", source_url="https://example.com/a",
                description_html="", description_text="a", posted_at=None,
            )
        ],
        now=now,
    )
    db_session.commit()

    payload = {"jobs": [
        {"id": 1, "title": "Enterprise Account Executive", "absolute_url": "https://example.com/a",
         "content": "sell things", "location": {"name": "NYC"}},
        {"id": 2, "title": "Staff Software Engineer", "absolute_url": "https://example.com/b",
         "content": "build things", "location": {"name": "Remote"}},
    ]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ingest_board(db_session, client, Board("greenhouse", "stripe", "Stripe"))

    titles = {j.title: j.is_active for j in db_session.query(Job).all()}
    assert titles["Staff Software Engineer"] is True
    # Still present as a row, but deactivated -- so it noindexes and leaves the sitemap.
    assert titles["Enterprise Account Executive"] is False

    # ok_count stays the pre-filter count so admin can show fetched vs kept.
    assert result.fetched == 2
    assert result.upserted == 1


def test_shipped_workday_board_list_is_well_formed():
    """Each row must carry host/tenant/site, and the slug must be unique.

    Liveness is not checked here -- it needs a POST per board and the admin
    monitor already reports failures. But note a wrong tenant still returns 406
    from the wildcard *.myworkdayjobs.com host, so a board can only be confirmed
    by a 200 with a `total`. Never add one on a guess.
    """
    raw = json.loads((BACKEND_DATA / "workday_boards.json").read_text(encoding="utf-8"))
    boards = load_workday_boards()
    assert len(boards) == len(raw["boards"])
    slugs = {b.slug for b in boards}
    assert len(slugs) == len(boards), "two boards share a source_id prefix"
    for b in boards:
        assert b.host.endswith("myworkdayjobs.com"), b
        assert b.tenant and b.site and b.company
        assert b.api("/jobs").startswith(f"https://{b.host}/wday/cxs/{b.tenant}/{b.site}")
