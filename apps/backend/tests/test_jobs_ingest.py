import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.models import Job
from app.services.jobs.ashby import parse_ashby
from app.services.jobs.boards import load_boards
from app.services.jobs.greenhouse import parse_greenhouse
from app.services.jobs.ingest import ingest_board
from app.services.jobs.lever import parse_lever
from app.services.jobs.sanitize import sanitize_html
from app.services.jobs.store import expire_missing, upsert_jobs
from app.services.jobs.taiwanjobs import parse_taiwanjobs
from app.services.jobs.types import Board, NormalizedJob

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jobs"


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


def test_parse_taiwanjobs_strips_fullwidth_tag_annotations():
    raw = (FIXTURES / "taiwanjobs.xml").read_text(encoding="utf-8")
    jobs = parse_taiwanjobs(raw)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "taiwanjobs"
    assert job.title == "軟體工程師"
    assert job.company == "範例科技"
    assert job.remote is True
    assert job.source_id == "tw-1"


def test_load_boards_skips_duplicates():
    boards = load_boards()
    keys = {(b.source, b.slug) for b in boards}
    assert len(keys) == len(boards)
    assert ("greenhouse", "stripe") in keys
    assert ("ashby", "openai") in keys


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
