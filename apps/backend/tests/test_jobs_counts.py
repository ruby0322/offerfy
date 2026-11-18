"""`/v1/jobs/counts`: several filters counted in one request.

The endpoint exists to collapse the curated board chips from six parallel
requests into one, so the tests that matter are the ones pinning that it still
answers *the same numbers* the six separate requests did -- a cheaper wrong
number would be worse than the fan-out it replaces.
"""

from datetime import datetime, timezone

from app.models import Job
from app.routers.jobs import COUNT_SPECS_MAX
from app.services.jobs.enrich import enrich_jobs


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
    db_session.add(job)
    db_session.commit()
    return job


def _counts(client, specs: list[str]) -> dict[str, int]:
    response = client.get("/v1/jobs/counts", params=[("spec", s) for s in specs])
    assert response.status_code == 200, response.text
    return {row["spec"]: row["total"] for row in response.json()["items"]}


def _seed_boards(db_session):
    """One posting per curated board axis, plus a decoy for each."""
    _add_job(db_session, source_id="s:1", title="Software Engineer Intern", location="Taipei")
    _add_job(db_session, source_id="s:2", title="Staff Engineer", location="Taipei")
    _add_job(db_session, source_id="s:3", title="Engineer", remote=False, location="Hsinchu")
    enrich_jobs(db_session)
    db_session.commit()


def test_one_request_answers_every_board(client, db_session):
    _seed_boards(db_session)
    counts = _counts(client, ["seniority=intern&limit=1", "remote=true&limit=1"])
    assert counts == {"seniority=intern&limit=1": 1, "remote=true&limit=1": 2}


def test_a_count_matches_the_same_filter_on_the_list_endpoint(client, db_session):
    """The chip and the board page it links to must never disagree.

    Both numbers come from `filtered_query`; this is the test that fails if one
    of the two surfaces ever grows a filter the other does not apply.
    """
    _seed_boards(db_session)
    for spec in ("seniority=intern", "remote=true", "seniority=staff_plus", "country=TW"):
        aggregate = _counts(client, [spec])[spec]
        listed = client.get(f"/v1/jobs?{spec}&limit=1").json()["total"]
        assert aggregate == listed, spec


def test_a_multi_rung_seniority_spec_is_read_as_a_list(client, db_session):
    """`new-grad` is two rungs in one board, so the comma must survive the
    round trip through the spec rather than being counted as one bad value."""
    _seed_boards(db_session)
    spec = "seniority=intern,staff_plus"
    assert _counts(client, [spec])[spec] == 2


def test_repeated_list_params_are_not_collapsed(client, db_session):
    """`?function=ml&function=backend` is two values, not one."""
    _add_job(db_session, source_id="s:1", title="Machine Learning Engineer")
    _add_job(db_session, source_id="s:2", title="Backend Engineer")
    enrich_jobs(db_session)
    db_session.commit()
    spec = "function=ml&function=backend"
    assert _counts(client, [spec])[spec] == 2


def test_inactive_postings_are_never_counted(client, db_session):
    """A board shows live postings only, and the chip has to say the same."""
    _add_job(db_session, source_id="s:1", title="Engineer Intern")
    _add_job(db_session, source_id="s:2", title="Engineer Intern", is_active=False)
    enrich_jobs(db_session)
    db_session.commit()
    assert _counts(client, ["seniority=intern"])["seniority=intern"] == 1


def test_the_spec_is_echoed_so_counts_map_by_filter_not_position(client, db_session):
    _seed_boards(db_session)
    specs = ["remote=true", "seniority=intern"]
    body = client.get("/v1/jobs/counts", params=[("spec", s) for s in specs]).json()
    assert [row["spec"] for row in body["items"]] == specs


def test_a_duplicate_spec_costs_one_count(client, db_session):
    _seed_boards(db_session)
    body = client.get(
        "/v1/jobs/counts", params=[("spec", "seniority=intern")] * 3
    ).json()
    assert body["items"] == [{"spec": "seniority=intern", "total": 1}]


def test_limit_and_cursor_are_ignored_rather_than_rejected(client, db_session):
    """The caller builds a spec with the same serializer it uses for /v1/jobs,
    which always sets a limit. A count of 1 would be the serializer leaking
    into the answer."""
    _seed_boards(db_session)
    spec = "remote=true&limit=1&cursor=abc"
    assert _counts(client, [spec])[spec] == 2


def test_no_specs_is_an_empty_answer_not_a_count_of_everything(client, db_session):
    """An empty `items` is the honest answer. Counting every posting would hand
    back a number the caller never asked for."""
    _seed_boards(db_session)
    body = client.get("/v1/jobs/counts").json()
    assert body == {"items": []}


def test_too_many_specs_is_refused(client):
    """The cap is the whole reason this endpoint is not a load amplifier."""
    response = client.get(
        "/v1/jobs/counts",
        params=[("spec", f"q=x{n}") for n in range(COUNT_SPECS_MAX + 1)],
    )
    assert response.status_code == 422
    assert "At most" in response.text


def test_an_unknown_filter_is_refused_rather_than_ignored(client):
    """This endpoint takes a filter string from the caller, so a parameter it
    does not understand must not pass as a silently wider count."""
    response = client.get("/v1/jobs/counts", params={"spec": "is_active=false"})
    assert response.status_code == 422
    assert "is_active" in response.text


def test_source_cannot_be_smuggled_in_through_a_spec(client, db_session):
    """`source` is a real /v1/jobs filter deliberately left out of the spec
    vocabulary: a board is never defined by which ATS it came from."""
    response = client.get("/v1/jobs/counts", params={"spec": "source=greenhouse"})
    assert response.status_code == 422


def test_a_bad_remote_value_is_a_422(client):
    response = client.get("/v1/jobs/counts", params={"spec": "remote=yes"})
    assert response.status_code == 422


def test_a_bad_posted_within_days_is_a_422(client):
    for bad in ("nope", "0", "91"):
        response = client.get("/v1/jobs/counts", params={"spec": f"posted_within_days={bad}"})
        assert response.status_code == 422, bad


def test_an_invalid_facet_value_is_still_a_422(client):
    """`filtered_query` raises ValueError on bad vocabulary and the list
    endpoint turns that into a 422; this endpoint must not turn it into a 500."""
    response = client.get("/v1/jobs/counts", params={"spec": "seniority=emperor"})
    assert response.status_code == 422


def test_counts_endpoint_is_not_shadowed_by_the_job_detail_route(client, db_session):
    """`/v1/jobs/{job_id}` would swallow this path if it were declared first.
    A 200 here is the ordering guarantee; a 404 would mean "no such posting".
    """
    _seed_boards(db_session)
    assert client.get("/v1/jobs/counts").status_code == 200
