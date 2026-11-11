from datetime import datetime, timedelta, timezone

from app.models import Job
from app.services.jobs.enrich import enrich_jobs
from app.services.jobs.store import JOB_FRESHNESS_DAYS


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
    # last_seen_at is wall-clock relative here on purpose: the sitemap only
    # lists rows inside JOB_FRESHNESS_DAYS, so the fixture's fixed default date
    # would age out of the window and rot this test.
    fresh = datetime.now(timezone.utc)
    job = _add_job(db_session, source_id="stripe:1", last_seen_at=fresh)
    _add_job(db_session, source_id="stripe:2", is_active=False, last_seen_at=fresh)
    response = client.get("/v1/jobs/sitemap")
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 0
    assert [row["id"] for row in body["items"]] == [job.id]


def test_jobs_sitemap_excludes_rows_past_the_freshness_window(client, db_session):
    """A stalled ingest must not keep handing crawlers expired postings.

    is_active only flips when an ingest run sees the posting gone, so rows stay
    active indefinitely once ingest stops. The frontend noindexes anything past
    JOB_FRESHNESS_DAYS, and the sitemap has to agree.
    """
    now = datetime.now(timezone.utc)
    fresh = _add_job(
        db_session,
        source_id="stripe:fresh",
        last_seen_at=now - timedelta(days=JOB_FRESHNESS_DAYS - 1),
    )
    _add_job(
        db_session,
        source_id="stripe:stale",
        last_seen_at=now - timedelta(days=JOB_FRESHNESS_DAYS + 1),
    )
    body = client.get("/v1/jobs/sitemap").json()
    assert [row["id"] for row in body["items"]] == [fresh.id]
    assert body["total_pages"] == 1


def test_featured_jobs_skips_job_id_route(client, db_session):
    _add_job(db_session, source_id="stripe:1", title="Engineer", company="Stripe")
    response = client.get("/v1/jobs/featured")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Engineer"
    assert "description_html" not in body["items"][0]
    assert body["total"] == 1


def test_filter_by_seniority(client, db_session):
    _add_job(db_session, source_id="a:1", title="Staff Engineer")
    _add_job(db_session, source_id="a:2", title="Junior Developer")
    enrich_jobs(db_session)
    db_session.commit()
    body = client.get("/v1/jobs?seniority=staff_plus").json()
    assert [item["title"] for item in body["items"]] == ["Staff Engineer"]


def test_filter_by_country_and_function(client, db_session):
    # One Taipei backend posting and one San Francisco ML posting.
    _add_job(db_session, source_id="a:1", title="Backend Engineer", location="Taipei, Taiwan")
    _add_job(
        db_session,
        source_id="a:2",
        title="Machine Learning Engineer",
        location="San Francisco, CA",
    )
    enrich_jobs(db_session)
    db_session.commit()
    body = client.get("/v1/jobs?country=TW&function=backend").json()
    assert body["total"] == 1


def test_invalid_facet_value_is_422(client):
    assert client.get("/v1/jobs?seniority=wizard").status_code == 422
    assert client.get("/v1/jobs?function=vibes").status_code == 422
    assert client.get("/v1/jobs?region=mars").status_code == 422


def test_total_counts_the_filtered_set_not_the_page(client, db_session):
    # 25 matching active postings, default limit 20.
    for i in range(25):
        _add_job(db_session, source_id=f"mid:{i}", title=f"Engineer {i}")
    enrich_jobs(db_session)
    db_session.commit()
    body = client.get("/v1/jobs?seniority=mid").json()
    assert len(body["items"]) == 20
    assert body["total"] == 25


def test_unfiltered_list_still_returns_jobs_with_no_facets_row(client, db_session):
    _add_job(db_session, source_id="a:9", title="Engineer")  # never enriched
    body = client.get("/v1/jobs").json()
    assert body["total"] == 1


def test_facet_filter_excludes_jobs_with_no_facets_row(client, db_session):
    _add_job(db_session, source_id="a:9", title="Engineer")  # never enriched
    assert client.get("/v1/jobs?seniority=mid").json()["total"] == 0


def test_cursor_paging_under_a_facet_filter_yields_each_row_once(client, db_session):
    # 25 matching postings; walk every page via next_cursor.
    for i in range(25):
        _add_job(db_session, source_id=f"page:{i}", title=f"Engineer {i}")
    enrich_jobs(db_session)
    db_session.commit()
    seen, cursor = [], None
    while True:
        url = "/v1/jobs?seniority=mid&limit=10" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 25


def test_posted_within_days_filters_on_posted_at(client, db_session):
    # One posting 3 days old, one 60 days old, both active and fresh.
    now = datetime.now(timezone.utc)
    _add_job(db_session, source_id="a:1", title="Recent", posted_at=now - timedelta(days=3))
    _add_job(db_session, source_id="a:2", title="Old", posted_at=now - timedelta(days=60))
    assert client.get("/v1/jobs?posted_within_days=7").json()["total"] == 1


def test_posted_within_days_excludes_null_posted_at(client, db_session):
    """Decision: a posting with no posted_at cannot be confirmed within any
    window, so posted_within_days excludes it rather than admitting it (the
    way _posted_at_order's nulls_last sorts it last but still shows it)."""
    _add_job(db_session, source_id="a:1", title="Undated", posted_at=None)
    body = client.get("/v1/jobs?posted_within_days=7").json()
    assert body["total"] == 0


def test_seniority_and_function_accept_the_fallback_values(client, db_session):
    # "unknown" and "other" are real vocabulary members, not sentinels.
    _add_job(db_session, source_id="a:1", title="Mystery Role")
    enrich_jobs(db_session)
    db_session.commit()
    assert client.get("/v1/jobs?seniority=unknown").status_code == 200
    assert client.get("/v1/jobs?function=other").status_code == 200


def test_seniority_filter_includes_job_with_null_country(client, db_session):
    # Title gives a seniority but the location string names no country.
    _add_job(db_session, source_id="a:1", title="Senior Engineer", location="Remote")
    enrich_jobs(db_session)
    db_session.commit()
    body = client.get("/v1/jobs?seniority=senior").json()
    assert body["total"] == 1
    facets = client.get("/v1/jobs/facets").json()
    assert facets["countries"] == []


def test_facets_endpoint_empty_catalogue_returns_empty_lists(client, db_session):
    body = client.get("/v1/jobs/facets").json()
    assert body == {"countries": [], "regions": [], "seniorities": [], "functions": []}


def test_facets_endpoint_counts_only_active_fresh_postings(client, db_session):
    # One active fresh TW posting, one inactive TW posting, one TW posting
    # last seen beyond JOB_FRESHNESS_DAYS.
    now = datetime.now(timezone.utc)
    active_fresh = _add_job(
        db_session, source_id="tw:1", location="Taipei, Taiwan", last_seen_at=now
    )
    inactive = _add_job(
        db_session,
        source_id="tw:2",
        location="Taipei, Taiwan",
        is_active=False,
        last_seen_at=now,
    )
    stale = _add_job(
        db_session,
        source_id="tw:3",
        location="Taipei, Taiwan",
        last_seen_at=now - timedelta(days=JOB_FRESHNESS_DAYS + 1),
    )
    enrich_jobs(db_session, job_ids=[active_fresh.id, inactive.id, stale.id])
    db_session.commit()
    body = client.get("/v1/jobs/facets").json()
    assert {"value": "TW", "count": 1} in body["countries"]
