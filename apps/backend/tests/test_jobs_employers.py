from datetime import datetime, timedelta, timezone

from app.models import Job
from app.services.jobs.employers import employer_slug, resolve_slug, slug_index
from app.services.jobs.enrich import enrich_jobs

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _add_job(db_session, **kwargs) -> Job:
    now = kwargs.pop("now", NOW)
    job = Job(
        source=kwargs.get("source", "greenhouse"),
        source_id=kwargs["source_id"],
        company=kwargs.get("company", "Stripe"),
        title=kwargs.get("title", "Engineer"),
        location=kwargs.get("location", "Remote"),
        remote=kwargs.get("remote", True),
        apply_url="https://example.com/apply",
        source_url="https://example.com/job",
        description_text=kwargs.get("description_text", "Build things"),
        description_html="<p>Build things</p>",
        posted_at=kwargs.get("posted_at", now),
        first_seen_at=kwargs.get("first_seen_at", now),
        last_seen_at=kwargs.get("last_seen_at", now),
        is_active=kwargs.get("is_active", True),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


# --- slug derivation ---------------------------------------------------------


def test_slug_folds_case_spaces_and_punctuation():
    assert employer_slug("Anthropic") == "anthropic"
    assert employer_slug("Scale AI") == "scale-ai"
    assert employer_slug("xAI") == "xai"
    assert employer_slug("Grafana Labs") == "grafana-labs"
    assert employer_slug("N26") == "n26"
    assert employer_slug("  Delivery Hero  ") == "delivery-hero"


def test_slug_is_empty_when_the_name_has_no_ascii_alphanumerics():
    # Not a row of escaped bytes: such an employer gets no page at all.
    assert employer_slug("台積電") == ""
    assert employer_slug("---") == ""


def test_colliding_slug_drops_both_employers():
    # Neither name can be served under "wise" without being the wrong answer
    # half the time, so the slug names nobody.
    index = slug_index(["Wise", "WISE", "Stripe"])
    assert index == {"stripe": "Stripe"}
    assert resolve_slug("wise", ["Wise", "WISE", "Stripe"]) is None
    assert resolve_slug("stripe", ["Wise", "WISE", "Stripe"]) == "Stripe"


def test_resolve_slug_accepts_any_spelling_that_folds_to_the_same_slug():
    assert resolve_slug("Scale-AI", ["Scale AI"]) == "Scale AI"
    assert resolve_slug("unknown", ["Scale AI"]) is None


# --- GET /v1/jobs/employers --------------------------------------------------


def test_employer_index_lists_every_live_employer_biggest_first(client, db_session):
    _add_job(db_session, source_id="s:1", company="Stripe")
    _add_job(db_session, source_id="s:2", company="Stripe")
    _add_job(db_session, source_id="a:1", company="Anthropic")
    response = client.get("/v1/jobs/employers")
    assert response.status_code == 200
    body = response.json()
    assert [row["company"] for row in body["items"]] == ["Stripe", "Anthropic"]
    assert [row["slug"] for row in body["items"]] == ["stripe", "anthropic"]
    assert [row["open_count"] for row in body["items"]] == [2, 1]
    assert body["total"] == 2


def test_employer_index_excludes_employers_with_no_live_posting(client, db_session):
    _add_job(db_session, source_id="s:1", company="Stripe")
    _add_job(db_session, source_id="g:1", company="Gone", is_active=False)
    companies = [row["company"] for row in client.get("/v1/jobs/employers").json()["items"]]
    assert companies == ["Stripe"]


def test_employer_index_counts_this_weeks_postings(client, db_session):
    now = datetime.now(timezone.utc)
    _add_job(db_session, source_id="s:1", company="Stripe", posted_at=now, now=now)
    _add_job(
        db_session,
        source_id="s:2",
        company="Stripe",
        posted_at=now - timedelta(days=30),
        now=now,
    )
    row = client.get("/v1/jobs/employers").json()["items"][0]
    assert row["open_count"] == 2
    assert row["new_count"] == 1


def test_employer_index_omits_an_employer_whose_name_makes_no_slug(client, db_session):
    _add_job(db_session, source_id="s:1", company="Stripe")
    _add_job(db_session, source_id="t:1", company="台積電")
    body = client.get("/v1/jobs/employers").json()
    assert [row["company"] for row in body["items"]] == ["Stripe"]
    # total counts what the list can name, so the index cannot quote a figure
    # its own rows do not fill.
    assert body["total"] == 1


# --- GET /v1/jobs/employers/{slug} -------------------------------------------


def test_employer_profile_counts_only_that_employer(client, db_session):
    now = datetime.now(timezone.utc)
    _add_job(
        db_session,
        source_id="a:1",
        company="Anthropic",
        remote=True,
        posted_at=now,
        first_seen_at=now - timedelta(days=90),
        now=now,
    )
    _add_job(
        db_session,
        source_id="a:2",
        company="Anthropic",
        remote=False,
        posted_at=now - timedelta(days=30),
        now=now,
    )
    _add_job(db_session, source_id="s:1", company="Stripe", now=now)

    body = client.get("/v1/jobs/employers/anthropic").json()
    assert body["company"] == "Anthropic"
    assert body["slug"] == "anthropic"
    assert body["open_count"] == 2
    assert body["new_count"] == 1
    assert body["remote_count"] == 1
    assert body["sources"] == ["greenhouse"]
    # Oldest first_seen_at among their live postings: when we started tracking
    # them, not when the posting was published.
    assert body["first_seen_at"].startswith((now - timedelta(days=90)).strftime("%Y-%m-%d"))


def test_employer_profile_names_every_board_they_run(client, db_session):
    _add_job(db_session, source_id="a:1", company="Anthropic", source="greenhouse")
    _add_job(db_session, source_id="b:1", company="Anthropic", source="ashby")
    body = client.get("/v1/jobs/employers/anthropic").json()
    assert body["sources"] == ["ashby", "greenhouse"]


def test_employer_profile_reports_where_and_what_they_hire(client, db_session):
    _add_job(
        db_session,
        source_id="a:1",
        company="Anthropic",
        title="Machine Learning Engineer",
        location="San Francisco, CA",
        remote=False,
    )
    _add_job(
        db_session,
        source_id="a:2",
        company="Anthropic",
        title="Senior Backend Engineer",
        location="San Francisco, CA",
        remote=False,
    )
    enrich_jobs(db_session)

    body = client.get("/v1/jobs/employers/anthropic").json()
    assert [row["value"] for row in body["cities"]][:1] == ["San Francisco"]
    assert body["cities"][0]["count"] == 2
    functions = {row["value"]: row["count"] for row in body["functions"]}
    assert functions.get("ml") == 1
    assert sum(functions.values()) == 2
    assert [row["value"] for row in body["countries"]] == ["US"]


def test_employer_profile_404s_for_an_unknown_slug(client, db_session):
    _add_job(db_session, source_id="s:1", company="Stripe")
    assert client.get("/v1/jobs/employers/nobody").status_code == 404


def test_employer_profile_404s_when_only_closed_postings_remain(client, db_session):
    _add_job(db_session, source_id="g:1", company="Gone", is_active=False)
    assert client.get("/v1/jobs/employers/gone").status_code == 404


def test_employer_profile_404s_for_a_slug_two_employers_claim(client, db_session):
    _add_job(db_session, source_id="w:1", company="Wise")
    _add_job(db_session, source_id="w:2", company="WISE")
    assert client.get("/v1/jobs/employers/wise").status_code == 404


def test_employers_path_is_not_captured_as_a_job_id(client, db_session):
    # The literal routes must stay declared above GET /v1/jobs/{job_id}.
    _add_job(db_session, source_id="s:1", company="Stripe")
    assert client.get("/v1/jobs/employers").status_code == 200
    assert "items" in client.get("/v1/jobs/employers").json()
