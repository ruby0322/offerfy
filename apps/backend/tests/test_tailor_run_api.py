"""The HTTP surface for starting, reading and streaming a tailor pass.

Route behaviour only -- status codes, the snapshot shape, ownership, and the
stream's termination contract. The runner is covered in
tests/test_tailor_runner.py, so every POST here disables the scheduling seam
(`app.routers.resumes._schedule_tailor`) to stay deterministic and off the
provider.
"""

from datetime import timedelta

import pytest

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import TailorRun, User, _utcnow
from app.services.tailor_run import STALE_AFTER_SECONDS, finish, latest_run

from tests.test_chat import parse_sse
from tests.test_jobs_api import _add_job


@pytest.fixture(autouse=True)
def _no_scheduling(monkeypatch):
    """No real pass may start under a route test -- see the module docstring."""
    monkeypatch.setattr("app.routers.resumes._schedule_tailor", lambda *a, **k: None)


def _signed_in(client, db_session, sub="sub-tailor-api"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _master(client):
    return client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()


def _tailor(client, master_id, **body):
    payload = {"title": "T", **body}
    return client.post(f"/v1/resumes/{master_id}/tailors", json=payload).json()


# --- starting ---------------------------------------------------------------


def test_a_master_resume_has_no_tailor_pass(client):
    master = _master(client)
    res = client.post(f"/v1/resumes/{master['id']}/tailor-run")
    assert res.status_code == 400


def test_starting_a_pass_returns_the_first_snapshot(client):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    res = client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "running"
    assert body["phase"] == "read_resume"
    assert body["version"] == 0
    assert body["stale"] is False
    assert body["job"] is None
    assert body["source_hash"]


def test_a_second_start_while_one_runs_is_409(client):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    assert client.post(f"/v1/resumes/{tailor['id']}/tailor-run").status_code == 201
    assert client.post(f"/v1/resumes/{tailor['id']}/tailor-run").status_code == 409


def test_a_finished_pass_can_be_started_again(client, db_session):
    """"Run it again" has to work, or a stale or failed pass is a dead end."""
    master = _master(client)
    tailor = _tailor(client, master["id"])
    client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    finish(db_session, latest_run(db_session, tailor["id"]))
    assert client.post(f"/v1/resumes/{tailor['id']}/tailor-run").status_code == 201


def test_the_route_schedules_the_pass(client, monkeypatch):
    """The seam the other tests disable is really wired to the route."""
    seen = []
    monkeypatch.setattr(
        "app.routers.resumes._schedule_tailor",
        lambda resume_id, run_id, *a, **k: seen.append((resume_id, run_id)),
    )
    master = _master(client)
    tailor = _tailor(client, master["id"])
    body = client.post(f"/v1/resumes/{tailor['id']}/tailor-run").json()
    assert seen == [(tailor["id"], body["run_id"])]


def test_the_posting_is_resolved_at_start_so_the_first_frame_names_it(
    client, db_session
):
    """The card draws the employer mark from this response; resolving it in
    the runner instead would leave a frame with a run and nothing to show."""
    _signed_in(client, db_session)
    job = _add_job(db_session, source_id="s:1", company="Stripe", title="Payments Engineer")
    master = _master(client)
    tailor = _tailor(client, master["id"], job_id=job.id)
    body = client.post(f"/v1/resumes/{tailor['id']}/tailor-run").json()
    assert body["job_id"] == job.id
    assert body["job"]["company"] == "Stripe"
    assert body["job"]["title"] == "Payments Engineer"


# --- reading ----------------------------------------------------------------


def test_no_pass_reads_404_not_an_empty_object(client):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    assert client.get(f"/v1/resumes/{tailor['id']}/tailor-run").status_code == 404


def test_the_snapshot_is_readable_after_a_start(client):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    started = client.post(f"/v1/resumes/{tailor['id']}/tailor-run").json()
    read = client.get(f"/v1/resumes/{tailor['id']}/tailor-run").json()
    assert read["run_id"] == started["run_id"]


def test_a_get_writes_nothing(client, db_session):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    before = latest_run(db_session, tailor["id"]).version
    client.get(f"/v1/resumes/{tailor['id']}/tailor-run")
    db_session.expire_all()
    assert latest_run(db_session, tailor["id"]).version == before


def test_another_owners_pass_is_not_reachable(client, db_session):
    """404, not 403: an owner mismatch must not confirm the resume exists."""
    first = _signed_in(client, db_session, sub="sub-one")
    master = _master(client)
    tailor = _tailor(client, master["id"])
    client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    assert first is not None

    client.cookies.clear()
    _signed_in(client, db_session, sub="sub-two")
    assert client.get(f"/v1/resumes/{tailor['id']}/tailor-run").status_code == 404
    assert client.post(f"/v1/resumes/{tailor['id']}/tailor-run").status_code == 404


# --- streaming --------------------------------------------------------------


def test_the_stream_404s_when_there_is_no_pass(client):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    assert client.get(f"/v1/resumes/{tailor['id']}/tailor-run/stream").status_code == 404


def test_the_stream_emits_a_terminal_frame_and_ends(client, db_session):
    """A stream that never terminates leaks a connection per reload."""
    master = _master(client)
    tailor = _tailor(client, master["id"])
    client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    finish(db_session, latest_run(db_session, tailor["id"]))

    with client.stream("GET", f"/v1/resumes/{tailor['id']}/tailor-run/stream") as res:
        assert res.status_code == 200
        frames = parse_sse("".join(res.iter_text()))

    assert frames
    assert frames[-1]["status"] == "done"


def test_a_stale_pass_terminates_the_stream_too(client, db_session):
    """Nothing reaps a run orphaned by an API restart, so without this the
    loop would poll Postgres forever while the card said "interrupted", and
    every reload would add another immortal stream."""
    master = _master(client)
    tailor = _tailor(client, master["id"])
    client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    run = latest_run(db_session, tailor["id"])
    run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.commit()

    with client.stream("GET", f"/v1/resumes/{tailor['id']}/tailor-run/stream") as res:
        frames = parse_sse("".join(res.iter_text()))

    assert frames[-1]["stale"] is True
    assert frames[-1]["status"] == "running"


def test_the_stream_is_not_reachable_for_another_owner(client, db_session):
    _signed_in(client, db_session, sub="sub-a")
    master = _master(client)
    tailor = _tailor(client, master["id"])
    client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    client.cookies.clear()
    _signed_in(client, db_session, sub="sub-b")
    res = client.get(f"/v1/resumes/{tailor['id']}/tailor-run/stream")
    assert res.status_code == 404


def test_only_one_running_row_can_exist_per_resume(client, db_session):
    """The DB-level guard, not just start_run's query: two uvicorn workers
    share only Postgres, so a check-then-insert race has to be caught here."""
    master = _master(client)
    tailor = _tailor(client, master["id"])
    client.post(f"/v1/resumes/{tailor['id']}/tailor-run")
    rows = (
        db_session.query(TailorRun)
        .filter(TailorRun.resume_id == tailor["id"], TailorRun.status == "running")
        .count()
    )
    assert rows == 1
