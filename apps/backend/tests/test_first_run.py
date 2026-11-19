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
from app.models import JobScore, User
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
    assert before == {"scored": 0, "expected": 3}
    Seams(monkeypatch)
    client.post("/v1/me/matches/first-run")
    assert _wait_for(user.id, 3)
    assert client.get("/v1/me/matches/progress").json() == {
        "scored": 3, "expected": 3,
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
async def test_a_run_that_cannot_build_facts_logs_the_user(db_session, client, monkeypatch, caplog):
    user = _user(db_session)
    _pref(db_session, user)
    _posting(db_session)

    async def no_facts(db, user, **kw):
        return None

    monkeypatch.setattr(matches, "sweep_user", no_facts)
    with caplog.at_level("WARNING"):
        await matches._run(user.id)
    assert any(user.id in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
    assert _count(user.id) == 0


def test_the_second_of_two_tabs_starts_nothing(client, db_session, monkeypatch):
    """Two tabs, one run. The loser is told so and buys nothing.

    This replaces a test that asserted BOTH runs bought, which was the
    design's blessing of the duplicate ("the loser writes the same answer").
    On Postgres the loser's first insert blocks on the winner's unique key
    until the winner's whole pass commits, and that block is a synchronous
    call on the API's event loop -- the worker serves nothing, /healthz
    included. So the duplicate is refused instead.

    Both tabs see zero job_scores rows, which is why `already_scored` cannot
    tell them apart: the run here is held open so that no rows exist when the
    second POST lands.
    """
    user = _ready_user(db_session, client)
    started = asyncio.Event()
    release = asyncio.Event()
    passes = 0

    async def held(db, *, user, cap=None):
        nonlocal passes
        passes += 1
        started.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return 0

    monkeypatch.setattr(matches, "sweep_user", held)
    assert client.post("/v1/me/matches/first-run").json() == {
        "started": True, "reason": None,
    }
    assert _count(user.id) == 0, "nothing is written yet, so not already_scored"
    assert client.post("/v1/me/matches/first-run").json() == {
        "started": False, "reason": "already_running",
    }
    release.set()
    assert passes <= 1, "the second POST must not have scheduled a pass"


def test_a_claim_is_one_winner_and_expires(db_session, monkeypatch):
    """The claim itself: one statement decides, and a dead run does not
    lock the person out for good."""
    from datetime import datetime, timedelta, timezone

    from app.routers.matches import FIRST_RUN_CLAIM_TTL, _claim_first_run

    user = _user(db_session)
    now = datetime.now(timezone.utc)
    assert _claim_first_run(db_session, user, now) is True
    assert _claim_first_run(db_session, user, now) is False
    # Still held a moment before it goes stale...
    assert _claim_first_run(db_session, user, now + FIRST_RUN_CLAIM_TTL) is False
    # ...and claimable once it has.
    later = now + FIRST_RUN_CLAIM_TTL + timedelta(seconds=1)
    assert _claim_first_run(db_session, user, later) is True
    db_session.expire_all()
    stamped = db_session.get(User, user.id).first_run_started_at
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    assert stamped == later


def test_a_refusal_leaves_the_claim_for_the_retry_it_invites(client, db_session):
    # no_preference tells the person to go and save one. If the refusal had
    # taken the claim, the run they were just told to make would be refused
    # as already_running.
    user = _signed_in(client, db_session, sub="sub-fr-claimfree")
    assert client.post("/v1/me/matches/first-run").json()["reason"] == "no_preference"
    db_session.expire_all()
    assert db_session.get(User, user.id).first_run_started_at is None


@pytest.mark.anyio
async def test_a_racing_write_back_still_leaves_one_row(
    db_session, client, monkeypatch, caplog
):
    """The upsert's conflict path stays correct, because the claim does not
    cover everything.

    A first run can still overlap a `score_sweep` pass for the same person in
    another process, which no claim on this endpoint can prevent. So the pair
    of runs is still exercised at the `write_back` level: both classify every
    pair as stale before either writes, which is what reaches the conflict.
    The barrier holds each scorer until both have arrived.
    """
    from app.services.jobs import score_cache

    user = _user(db_session)
    _pref(db_session, user)
    _facts(db_session, user)
    for i in range(3):
        _posting(db_session, age_minutes=i)
    seams = Seams(monkeypatch)
    inner = score_cache.score_candidates
    arrived = 0
    both_here = asyncio.Event()

    async def barrier(db, **kw):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_here.set()
        await asyncio.wait_for(both_here.wait(), timeout=5)
        return await inner(db, **kw)

    monkeypatch.setattr(score_cache, "score_candidates", barrier)
    await asyncio.gather(matches._run(user.id), matches._run(user.id))
    # _run swallows a fault into a log line, so a conflict that raised would
    # still leave the winner's rows behind. Neither run may have failed.
    assert not [r for r in caplog.records if "first run failed" in r.getMessage()]
    assert len(seams.scored_jobs) == 2, "both really reached the provider"
    assert _count(user.id) == 3, "one row per pair, not two"
