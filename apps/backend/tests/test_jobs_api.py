from datetime import datetime, timedelta, timezone

from app.models import Job


def _add_job(db_session, **kwargs) -> Job:
    now = kwargs.pop("now", datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))
    job = Job(
        source=kwargs.get("source", "greenhouse"),
        source_id=kwargs["source_id"],
        company=kwargs.get("company", "Stripe"),
        title=kwargs.get("title", "Engineer"),
        location=kwargs.get("location", "Remote"),
        remote=kwargs.get("remote", True),
        apply_url=kwargs.get("apply_url", "https://example.com/apply"),
        source_url=kwargs.get("source_url", "https://example.com/job"),
        description_text=kwargs.get("description_text", "Build things"),
        description_html=kwargs.get("description_html", "<p>Build things</p>"),
        posted_at=kwargs.get("posted_at", now),
        first_seen_at=kwargs.get("first_seen_at", now),
        last_seen_at=kwargs.get("last_seen_at", now),
        is_active=kwargs.get("is_active", True),
    )
    if "id" in kwargs:
        job.id = kwargs["id"]
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_list_jobs_unauthenticated_excludes_html(client, db_session):
    _add_job(db_session, source_id="stripe:1", title="Payments Engineer")
    response = client.get("/v1/jobs")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["title"] == "Payments Engineer"
    assert item["source"] == "greenhouse"
    assert "description_html" not in item
    assert body["next_cursor"] is None


def test_list_jobs_hides_inactive_by_default(client, db_session):
    _add_job(db_session, source_id="stripe:1", title="Open")
    _add_job(db_session, source_id="stripe:2", title="Closed", is_active=False)
    response = client.get("/v1/jobs")
    titles = [row["title"] for row in response.json()["items"]]
    assert titles == ["Open"]


def test_list_jobs_q_and_source_filters(client, db_session):
    _add_job(db_session, source_id="stripe:1", company="Stripe", title="Engineer")
    _add_job(
        db_session,
        source="ashby",
        source_id="openai:1",
        company="OpenAI",
        title="Researcher",
        location="San Francisco",
        remote=False,
    )
    listed = client.get("/v1/jobs", params={"q": "stripe"})
    assert [row["company"] for row in listed.json()["items"]] == ["Stripe"]
    ashby = client.get("/v1/jobs", params={"source": "ashby"})
    assert [row["company"] for row in ashby.json()["items"]] == ["OpenAI"]
    remote = client.get("/v1/jobs", params={"remote": True})
    assert [row["company"] for row in remote.json()["items"]] == ["Stripe"]


def test_list_jobs_orders_by_posted_at_not_last_seen(client, db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    _add_job(
        db_session,
        source_id="old-post-fresh-ingest",
        title="Ingested last",
        posted_at=now - timedelta(days=30),
        last_seen_at=now,
        id="job-ingest",
    )
    _add_job(
        db_session,
        source_id="new-post",
        title="Posted today",
        posted_at=now,
        last_seen_at=now - timedelta(hours=3),
        id="job-new",
    )
    titles = [row["title"] for row in client.get("/v1/jobs").json()["items"]]
    assert titles == ["Posted today", "Ingested last"]


def test_list_jobs_cursor(client, db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    _add_job(
        db_session,
        source_id="a",
        title="A",
        posted_at=now,
        last_seen_at=now - timedelta(days=1),
        id="job-a",
    )
    _add_job(
        db_session,
        source_id="b",
        title="B",
        posted_at=now - timedelta(hours=1),
        last_seen_at=now,
        id="job-b",
    )
    first = client.get("/v1/jobs", params={"limit": 1})
    assert first.status_code == 200
    assert first.json()["items"][0]["title"] == "A"
    cursor = first.json()["next_cursor"]
    assert cursor
    second = client.get("/v1/jobs", params={"limit": 1, "cursor": cursor})
    assert second.json()["items"][0]["title"] == "B"
    assert second.json()["next_cursor"] is None


def test_list_jobs_cursor_identical_posted_at(client, db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    _add_job(db_session, source_id="a", title="A", posted_at=now, id="job-c")
    _add_job(db_session, source_id="b", title="B", posted_at=now, id="job-b")
    _add_job(db_session, source_id="c", title="C", posted_at=now, id="job-a")
    first = client.get("/v1/jobs", params={"limit": 2})
    body = first.json()
    assert [row["id"] for row in body["items"]] == ["job-c", "job-b"]
    second = client.get("/v1/jobs", params={"limit": 2, "cursor": body["next_cursor"]})
    assert [row["id"] for row in second.json()["items"]] == ["job-a"]
    assert second.json()["next_cursor"] is None


def test_list_jobs_null_posted_at_sorts_last(client, db_session):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    _add_job(
        db_session,
        source_id="dated",
        title="Dated",
        posted_at=now - timedelta(days=10),
        last_seen_at=now - timedelta(days=10),
        id="job-dated",
    )
    _add_job(
        db_session,
        source_id="undated",
        title="Undated",
        posted_at=None,
        last_seen_at=now,
        id="job-undated",
    )
    titles = [row["title"] for row in client.get("/v1/jobs").json()["items"]]
    assert titles == ["Dated", "Undated"]


def test_get_job_inactive_still_returns(client, db_session):
    job = _add_job(
        db_session,
        source_id="stripe:closed",
        title="Closed role",
        is_active=False,
        description_html="<p>Closed</p>",
    )
    missing = client.get("/v1/jobs/does-not-exist")
    assert missing.status_code == 404
    got = client.get(f"/v1/jobs/{job.id}")
    assert got.status_code == 200
    body = got.json()
    assert body["is_active"] is False
    assert body["description_html"] == "<p>Closed</p>"


def test_jobs_sitemap_active_only(client, db_session):
    job = _add_job(db_session, source_id="stripe:1")
    _add_job(db_session, source_id="stripe:2", is_active=False)
    response = client.get("/v1/jobs/sitemap")
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 0
    assert [row["id"] for row in body["items"]] == [job.id]


def test_featured_jobs_skips_job_id_route(client, db_session):
    _add_job(db_session, source_id="stripe:1", title="Engineer", company="Stripe")
    response = client.get("/v1/jobs/featured")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Engineer"
    assert "description_html" not in body["items"][0]
