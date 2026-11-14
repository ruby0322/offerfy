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
    assert client.get("/v1/jobs?country=NOTACOUNTRY").status_code == 422


def test_country_filter_accepts_a_valid_country_with_no_matches(client, db_session):
    # "DE" is a real member of location_countries' value set but nothing in
    # this fixture is located there -- distinct from "not a country at all",
    # which 422s instead of returning an empty, successful result.
    _add_job(db_session, source_id="a:1", title="Backend Engineer", location="Taipei, Taiwan")
    enrich_jobs(db_session)
    db_session.commit()
    body = client.get("/v1/jobs?country=DE")
    assert body.status_code == 200
    assert body.json()["total"] == 0


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
    assert body == {
        "countries": [],
        "regions": [],
        "seniorities": [],
        "functions": [],
        "companies": [],
        "employer_total": 0,
    }


def test_facets_endpoint_counts_over_the_same_scope_as_the_list(client, db_session):
    """Option counts share GET /v1/jobs' scope: active, freshness irrelevant.

    A dropdown describes the list it filters, so the inactive posting is out
    (the list excludes it) and the stale-but-active one is in (the list shows
    it). Counting over the freshness window instead would make the counts
    disagree with `total`, and would empty every dropdown after an ingest
    outage longer than JOB_FRESHNESS_DAYS while the list still had postings.
    """
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
    # Both active rows, fresh and stale; not the inactive one.
    assert {"value": "TW", "count": 2} in body["countries"]
    assert client.get("/v1/jobs?country=TW").json()["total"] == 2


def test_company_filter_is_exact_not_a_prefix(client, db_session):
    """?company= takes a value we handed out, so it must not match a longer one.

    The facet menu and the landing wall both link here by exact employer
    name. A LIKE would make the day someone adds a "Stripes" board quietly
    merge two employers' catalogs.
    """
    _add_job(db_session, source_id="s:1", company="Stripe", title="Payments")
    _add_job(db_session, source_id="s:2", company="Stripe Press", title="Editor")
    body = client.get("/v1/jobs", params={"company": "Stripe"}).json()
    assert [row["title"] for row in body["items"]] == ["Payments"]
    assert body["total"] == 1


def test_company_filter_ignores_case(client, db_session):
    _add_job(db_session, source_id="s:1", company="Dcard")
    assert client.get("/v1/jobs", params={"company": "dcard"}).json()["total"] == 1


def test_city_filter_matches_an_alias_spelling(client, db_session):
    """?city=Bengaluru finds the rows an employer spelled "Bangalore".

    One city, two spellings, and the picker only ever offers the canonical
    one -- so the filter has to cover both or half the city disappears.
    """
    a = _add_job(db_session, source_id="in:1", location="Bengaluru, India", title="One")
    b = _add_job(db_session, source_id="in:2", location="Bangalore, India", title="Two")
    enrich_jobs(db_session, job_ids=[a.id, b.id])
    db_session.commit()
    body = client.get("/v1/jobs", params={"city": "Bengaluru"}).json()
    assert sorted(row["title"] for row in body["items"]) == ["One", "Two"]


def test_city_filter_matches_a_multi_city_row(client, db_session):
    """A posting open in two cities belongs to each city's list."""
    job = _add_job(
        db_session,
        source_id="in:3",
        location="Bengaluru; Gurugram, India",
        title="Both",
    )
    enrich_jobs(db_session, job_ids=[job.id])
    db_session.commit()
    assert client.get("/v1/jobs", params={"city": "Gurugram"}).json()["total"] == 1


def test_city_filter_does_not_reach_a_different_city(client, db_session):
    job = _add_job(db_session, source_id="us:1", location="New York, United States")
    enrich_jobs(db_session, job_ids=[job.id])
    db_session.commit()
    assert client.get("/v1/jobs", params={"city": "York"}).json()["total"] == 0


def test_cities_endpoint_folds_spellings_and_drops_site_codes(client, db_session):
    """The picker shows cities, not whatever the ATS put in that position."""
    ids = []
    for index, location in enumerate(
        [
            "Bengaluru, India",
            "Bangalore, India",
            "Gurugram, India",
            "Fab 10N/X, India",
            "Hybrid, India",
        ]
    ):
        ids.append(_add_job(db_session, source_id=f"in:{index}", location=location).id)
    enrich_jobs(db_session, job_ids=ids)
    db_session.commit()
    body = client.get("/v1/jobs/cities", params={"country": "IN"}).json()
    assert body["country"] == "IN"
    assert body["items"] == [
        {"value": "Bengaluru", "count": 2},
        {"value": "Gurugram", "count": 1},
    ]


def test_cities_endpoint_folds_scripts_as_well_as_spellings(client, db_session):
    """Taiwan's picker was empty of every CJK-spelled posting.

    The fold's length floor counted characters, so "台北" was dropped as if
    it were an airport code. These four postings are one city and must come
    back as one row with a count of four -- the Bangalore/Bengaluru case in
    a second script.
    """
    ids = []
    for index, location in enumerate(
        ["Taipei, Taiwan", "台北", "臺北市", "Taipei City, Taiwan", "竹科"]
    ):
        ids.append(_add_job(db_session, source_id=f"tw:{index}", location=location).id)
    enrich_jobs(db_session, job_ids=ids)
    db_session.commit()
    body = client.get("/v1/jobs/cities", params={"country": "TW"}).json()
    assert body["items"] == [
        {"value": "Taipei", "count": 4},
        {"value": "Hsinchu", "count": 1},
    ]


def test_city_filter_matches_a_cjk_spelling(client, db_session):
    """?city=Taipei has to find the postings an employer wrote as 台北."""
    ids = []
    for index, location in enumerate(["Taipei, Taiwan", "台北", "新竹市"]):
        ids.append(_add_job(db_session, source_id=f"tw:{index}", location=location, title=f"T{index}").id)
    enrich_jobs(db_session, job_ids=ids)
    db_session.commit()
    body = client.get("/v1/jobs", params={"city": "Taipei"}).json()
    assert sorted(row["title"] for row in body["items"]) == ["T0", "T1"]
    # And the Hsinchu posting is reachable under its own Latin name, not
    # swept into Taipei.
    assert client.get("/v1/jobs", params={"city": "Hsinchu"}).json()["total"] == 1


def test_cities_endpoint_rejects_an_unknown_country(client):
    assert client.get("/v1/jobs/cities", params={"country": "ZZ"}).status_code == 422


def test_cities_endpoint_offers_nothing_for_a_city_state(client, db_session):
    """Singapore's own name is the country, so it is not a city within it."""
    job = _add_job(db_session, source_id="sg:1", location="Singapore")
    enrich_jobs(db_session, job_ids=[job.id])
    db_session.commit()
    assert client.get("/v1/jobs/cities", params={"country": "SG"}).json()["items"] == []


def test_facets_endpoint_reports_employers(client, db_session):
    _add_job(db_session, source_id="a:1", company="NVIDIA")
    _add_job(db_session, source_id="a:2", company="NVIDIA")
    _add_job(db_session, source_id="b:1", company="Stripe")
    _add_job(db_session, source_id="c:1", company="Closed", is_active=False)
    body = client.get("/v1/jobs/facets").json()
    assert body["companies"] == [
        {"value": "NVIDIA", "count": 2},
        {"value": "Stripe", "count": 1},
    ]
    assert body["employer_total"] == 2


def test_pulse_counts_only_what_is_open(client, db_session):
    now = datetime.now(timezone.utc)
    _add_job(db_session, source_id="p:1", company="NVIDIA", posted_at=now, last_seen_at=now)
    _add_job(
        db_session,
        source_id="p:2",
        company="Stripe",
        posted_at=now - timedelta(days=3),
        last_seen_at=now - timedelta(hours=2),
    )
    _add_job(
        db_session,
        source_id="p:3",
        company="Gone",
        is_active=False,
        posted_at=now,
        last_seen_at=now,
    )
    body = client.get("/v1/jobs/pulse").json()
    assert body["active_total"] == 2
    assert body["new_24h"] == 1
    assert body["employer_count"] == 2
    assert body["refreshed_at"] is not None


def test_pulse_new_counts_ignore_a_posting_with_no_date(client, db_session):
    """Undercounting what is new beats inventing a date a board never gave.

    Most of Workday publishes "Posted Yesterday" as a phrase, which the
    ingest cannot turn into a timestamp. Those rows are in active_total and
    out of new_24h -- the same rule ?posted_within_days= follows.
    """
    now = datetime.now(timezone.utc)
    _add_job(db_session, source_id="p:1", posted_at=None, last_seen_at=now)
    body = client.get("/v1/jobs/pulse").json()
    assert body["active_total"] == 1
    assert body["new_24h"] == 0
    assert body["newest"] == []


def test_pulse_newest_is_most_recently_posted_first(client, db_session):
    now = datetime.now(timezone.utc)
    _add_job(db_session, source_id="p:1", title="Older", posted_at=now - timedelta(hours=5))
    _add_job(db_session, source_id="p:2", title="Newer", posted_at=now - timedelta(minutes=5))
    titles = [row["title"] for row in client.get("/v1/jobs/pulse").json()["newest"]]
    assert titles == ["Newer", "Older"]


def test_pulse_leaderboard_ranks_by_new_then_open(client, db_session):
    """Who is hiring hardest is about this week, with size as the tiebreak."""
    now = datetime.now(timezone.utc)
    for index in range(4):
        _add_job(
            db_session,
            source_id=f"big:{index}",
            company="Big But Quiet",
            posted_at=now - timedelta(days=90),
        )
    for index in range(2):
        _add_job(
            db_session,
            source_id=f"busy:{index}",
            company="Busy",
            posted_at=now - timedelta(days=1),
        )
    rows = client.get("/v1/jobs/pulse").json()["top_employers"]
    assert rows[0] == {"company": "Busy", "open_count": 2, "new_count": 2}
    assert rows[1] == {"company": "Big But Quiet", "open_count": 4, "new_count": 0}


def test_seniority_accepts_several_rungs(client, db_session):
    """A curated board is often one audience over two rungs.

    "New grad" means new_grad AND junior; "ready to move" means mid, senior
    and staff_plus. Without this the board pages would each need their own
    endpoint, or would show half their audience.
    """
    ids = []
    for index, title in enumerate(
        ["New Grad Engineer", "Junior Engineer", "Staff Engineer"]
    ):
        ids.append(_add_job(db_session, source_id=f"g:{index}", title=title).id)
    enrich_jobs(db_session, job_ids=ids)
    db_session.commit()
    body = client.get("/v1/jobs", params={"seniority": "new_grad,junior"}).json()
    assert sorted(row["title"] for row in body["items"]) == [
        "Junior Engineer",
        "New Grad Engineer",
    ]
    assert body["total"] == 2


def test_seniority_list_with_one_bad_member_is_422(client):
    response = client.get("/v1/jobs", params={"seniority": "mid,not_a_rung"})
    assert response.status_code == 422


def test_facets_scope_to_the_filters_they_are_given(client, db_session):
    """A board page's menus describe that board, not the whole catalogue.

    /jobs/taiwan shows function counts for Taiwan; the open /jobs page
    passes nothing and still gets catalogue-wide counts, which is what
    test_facets_endpoint_counts_over_the_same_scope_as_the_list pins.
    """
    tw = _add_job(
        db_session,
        source_id="tw:1",
        company="Appier",
        title="Machine Learning Engineer",
        location="Taipei, Taiwan",
    )
    us = _add_job(
        db_session,
        source_id="us:1",
        company="Stripe",
        title="Machine Learning Engineer",
        location="San Francisco, United States",
    )
    enrich_jobs(db_session, job_ids=[tw.id, us.id])
    db_session.commit()

    everywhere = client.get("/v1/jobs/facets").json()
    assert {"value": "ml", "count": 2} in everywhere["functions"]
    assert everywhere["employer_total"] == 2

    taiwan = client.get("/v1/jobs/facets", params={"country": "TW"}).json()
    assert {"value": "ml", "count": 1} in taiwan["functions"]
    assert taiwan["companies"] == [{"value": "Appier", "count": 1}]
    assert taiwan["employer_total"] == 1


def test_pulse_scopes_to_the_filters_it_is_given(client, db_session):
    """A curated board gets its own pulse from the same endpoint."""
    now = datetime.now(timezone.utc)
    intern = _add_job(
        db_session,
        source_id="i:1",
        company="NVIDIA",
        title="Software Engineering Intern",
        posted_at=now,
    )
    senior = _add_job(
        db_session,
        source_id="s:1",
        company="Stripe",
        title="Staff Engineer",
        posted_at=now,
    )
    enrich_jobs(db_session, job_ids=[intern.id, senior.id])
    db_session.commit()

    everything = client.get("/v1/jobs/pulse").json()
    assert everything["active_total"] == 2
    assert everything["employer_count"] == 2

    interns = client.get("/v1/jobs/pulse", params={"seniority": "intern"}).json()
    assert interns["active_total"] == 1
    assert interns["new_24h"] == 1
    assert interns["employer_count"] == 1
    assert [row["company"] for row in interns["top_employers"]] == ["NVIDIA"]
    assert [row["title"] for row in interns["newest"]] == ["Software Engineering Intern"]


def test_a_facet_does_not_filter_its_own_options(client, db_session):
    """Choosing a country must not empty the country menu.

    This is what makes the menus a way to change your mind rather than a
    readout of the current choice: under ?country=TW the country list still
    offers the US, with the count you would get by switching, while the
    function list IS narrowed to Taiwan.
    """
    tw = _add_job(
        db_session,
        source_id="tw:1",
        company="Appier",
        title="Machine Learning Engineer",
        location="Taipei, Taiwan",
    )
    us_one = _add_job(
        db_session,
        source_id="us:1",
        company="Stripe",
        title="Backend Engineer",
        location="San Francisco, United States",
    )
    us_two = _add_job(
        db_session,
        source_id="us:2",
        company="Stripe",
        title="Backend Engineer",
        location="New York, United States",
    )
    enrich_jobs(db_session, job_ids=[tw.id, us_one.id, us_two.id])
    db_session.commit()

    body = client.get("/v1/jobs/facets", params={"country": "TW"}).json()
    countries = {row["value"]: row["count"] for row in body["countries"]}
    assert countries == {"TW": 1, "US": 2}
    # The function menu, which the country filter does not own, is scoped.
    functions = {row["value"] for row in body["functions"]}
    assert functions == {"ml"}
    # Same rule for the employer menu.
    assert {row["value"] for row in body["companies"]} == {"Appier"}

    by_employer = client.get("/v1/jobs/facets", params={"company": "Stripe"}).json()
    assert {row["value"] for row in by_employer["companies"]} == {"Appier", "Stripe"}
