"""The first run: what it starts, what it refuses, and what it reports.

The paid seams are stubbed exactly as test_score_sweep stubs them
(`Seams`), so "scored" means rows written by the real sweep path.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_session_factory
from app.deps import SESSION_COOKIE, _sign
from app.jobs.score_sweep import SWEEP_CAP
from app.main import app
from app.models import JobScore
from app.routers import matches

from tests.test_job_preferences_api import _signed_in
from tests.test_score_sweep import Seams, _ceiling, _facts, _posting, _pref, _spend, _user


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ready_user(db, client, postings=3):
    user = _signed_in(client, db, sub=f"sub-fr-{time.monotonic_ns()}")
    _pref(db, user)
    _facts(db, user)
    for i in range(postings):
        _posting(db, age_minutes=i)
    return user


def _count(user_id):
    s = get_session_factory()()
    try:
        return s.query(JobScore).filter(JobScore.user_id == user_id).count()
    finally:
        s.close()


def _wait_for(user_id, n, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _count(user_id) >= n:
            return True
        time.sleep(0.05)
    return False


def test_the_first_put_reports_first_save_and_later_ones_do_not(client, db_session):
    _signed_in(client, db_session, sub="sub-fs")
    body = {"target_functions": ["backend"], "target_locations": ["tw"],
            "target_seniority": "mid", "remote_required": None, "activity": "actively_looking"}
    assert client.put("/v1/me/job-preferences", json=body).json()["first_save"] is True
    assert client.put("/v1/me/job-preferences", json=body).json()["first_save"] is False
    assert client.get("/v1/me/job-preferences").json()["first_save"] is False


@pytest.mark.anyio
async def test_the_first_run_scores_the_whole_eligible_pool(db_session, client, monkeypatch):
    user = _user(db_session)
    _pref(db_session, user)
    _facts(db_session, user)
    for i in range(3):
        _posting(db_session, age_minutes=i)
    Seams(monkeypatch)
    # The detached body, on the session of its own it builds for itself. The
    # test's session is never handed to it.
    await matches._run(user.id)
    assert _count(user.id) == 3


def test_a_post_starts_a_detached_run_that_commits_its_scores(client, db_session, monkeypatch):
    user = _ready_user(db_session, client)
    Seams(monkeypatch)
    resp = client.post("/v1/me/matches/first-run")
    assert resp.json() == {"started": True, "reason": None}
    # Counted from a session other than the run's: it committed.
    assert _wait_for(user.id, 3)


def test_the_first_run_is_refused_once_scores_exist(client, db_session, monkeypatch):
    user = _ready_user(db_session, client)
    Seams(monkeypatch)
    client.post("/v1/me/matches/first-run")
    assert _wait_for(user.id, 3)
    again = client.post("/v1/me/matches/first-run").json()
    assert again == {"started": False, "reason": "already_scored"}


def test_progress_counts_rows_against_the_expected_pool(client, db_session, monkeypatch):
    user = _ready_user(db_session, client, postings=3)
    before = client.get("/v1/me/matches/progress").json()
    assert before == {"scored": 0, "expected": 3, "running": True}
    Seams(monkeypatch)
    client.post("/v1/me/matches/first-run")
    assert _wait_for(user.id, 3)
    assert client.get("/v1/me/matches/progress").json() == {
        "scored": 3, "expected": 3, "running": False,
    }


def test_expected_is_capped_at_the_run_cap(client, db_session):
    _ready_user(db_session, client, postings=SWEEP_CAP + 2)
    assert client.get("/v1/me/matches/progress").json()["expected"] == SWEEP_CAP


def test_progress_survives_a_reload(client, db_session, monkeypatch):
    user = _ready_user(db_session, client)
    Seams(monkeypatch)
    client.post("/v1/me/matches/first-run")
    assert _wait_for(user.id, 3)
    # Nothing is held in this process: forget it all, and a fresh client agrees.
    matches._running.clear()
    with TestClient(app) as fresh:
        fresh.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
        assert fresh.get("/v1/me/matches/progress").json()["scored"] == 3


def test_a_user_with_no_publishable_resume_is_told_why(client, db_session, monkeypatch):
    user = _signed_in(client, db_session, sub="sub-noresume")
    _pref(db_session, user)
    _posting(db_session)
    seams = Seams(monkeypatch)
    resp = client.post("/v1/me/matches/first-run")
    assert resp.status_code == 200
    assert resp.json() == {"started": False, "reason": "no_resume"}
    assert seams.scored_jobs == []


def test_a_user_with_no_saved_preference_is_told_why(client, db_session):
    _signed_in(client, db_session, sub="sub-nopref")
    resp = client.post("/v1/me/matches/first-run")
    assert resp.json() == {"started": False, "reason": "no_preference"}


def test_over_the_ceiling_it_says_paused_and_promises_no_time(client, db_session, monkeypatch):
    user = _ready_user(db_session, client)
    seams = Seams(monkeypatch)
    _ceiling(monkeypatch, 1_000_000)
    _spend(db_session, uusd=1_000_000)
    db_session.commit()
    resp = client.post("/v1/me/matches/first-run")
    assert resp.json() == {"started": False, "reason": "budget_paused"}
    text = resp.text.lower()
    # No promise: nothing about the sweep, hours, or coming soon.
    for word in ("sweep", "hour", "soon", "coming", "later"):
        assert word not in text
    assert seams.scored_jobs == []
    assert _count(user.id) == 0


@pytest.mark.anyio
async def test_two_runs_racing_for_the_same_pairs_leave_one_row_each(db_session, client, monkeypatch):
    user = _user(db_session)
    _pref(db_session, user)
    _facts(db_session, user)
    for i in range(3):
        _posting(db_session, age_minutes=i)
    Seams(monkeypatch)
    await asyncio.gather(matches._run(user.id), matches._run(user.id))
    assert _count(user.id) == 3
