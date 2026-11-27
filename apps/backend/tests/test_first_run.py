"""The first run: what it starts, what it refuses, and what it reports.

The paid seams are stubbed exactly as test_score_sweep stubs them
(`Seams`), so "scored" means rows written by the real sweep path.
"""

import asyncio
import time
from datetime import datetime, timezone

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


def _wait_for_digest(user_id, seconds=5):
    from app.models import Digest

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        s = get_session_factory()()
        try:
            if s.query(Digest).filter(Digest.user_id == user_id).count():
                return True
        finally:
            s.close()
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
    # `_run` now carries the timestamp it claimed, so it can give the claim
    # back without clobbering a newer one. These tests drive it directly and
    # never took a claim, so any timestamp releases nothing.
    await matches._run(user.id, datetime.now(timezone.utc))
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
    assert before == {"scored": 0, "expected": 3, "running": False}
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


def test_a_paused_preference_is_honoured_here_too(client, db_session, monkeypatch):
    # "The person asked for this to stop" holds on every scoring path:
    # sweep_all skips them through is_eligible, and build_digests refuses even
    # under --all-users. This endpoint was the one that ignored it.
    from app.models import JobPreference

    user = _ready_user(db_session, client)
    db_session.query(JobPreference).filter_by(user_id=user.id).update(
        {"activity": "paused"}
    )
    db_session.commit()
    seams = Seams(monkeypatch)
    assert client.post("/v1/me/matches/first-run").json() == {
        "started": False, "reason": "activity_paused",
    }
    assert seams.scored_jobs == [] and _count(user.id) == 0
    db_session.expire_all()
    # And the refusal leaves the claim alone, so switching it back on works.
    assert db_session.get(User, user.id).first_run_started_at is None


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
        await matches._run(user.id, datetime.now(timezone.utc))
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
    claimed = datetime.now(timezone.utc)
    await asyncio.gather(matches._run(user.id, claimed), matches._run(user.id, claimed))
    # _run swallows a fault into a log line, so a conflict that raised would
    # still leave the winner's rows behind. Neither run may have failed.
    assert not [r for r in caplog.records if "first run failed" in r.getMessage()]
    assert len(seams.scored_jobs) == 2, "both really reached the provider"
    assert _count(user.id) == 3, "one row per pair, not two"


def test_the_claim_is_released_when_the_pass_ends(db_session, monkeypatch):
    """A run that bought nothing must not leave the next POST told
    `already_running` -- a sentence that would be false for ten minutes."""
    import asyncio
    from app.routers import matches as mod

    user = _user(db_session)
    now = datetime.now(timezone.utc)
    assert mod._claim_first_run(db_session, user, now) is True

    # The pass itself is not what is under test; a run that finds nothing to
    # build facts from is the case that used to strand the claim.
    async def nothing(db, *, user, cap):
        return None

    monkeypatch.setattr(mod, "sweep_user", nothing)
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        mod._run(user.id, now)
    )
    db_session.expire_all()
    assert db_session.get(User, user.id).first_run_started_at is None


def test_a_crashed_pass_still_releases_the_claim(db_session, monkeypatch):
    import asyncio
    from app.routers import matches as mod

    user = _user(db_session)
    now = datetime.now(timezone.utc)
    assert mod._claim_first_run(db_session, user, now) is True

    async def boom(db, *, user, cap):
        raise RuntimeError("provider down")

    monkeypatch.setattr(mod, "sweep_user", boom)
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        mod._run(user.id, now)
    )
    db_session.expire_all()
    assert db_session.get(User, user.id).first_run_started_at is None


def test_releasing_does_not_clear_a_newer_claim(db_session):
    """The reason the release carries its timestamp.

    A run that outlived the TTL may have had its claim taken by a newer one.
    Clearing that would leave the new owner running unclaimed and let a third
    tab start a duplicate -- worse than the stranded claim this fixes.
    """
    from datetime import datetime, timedelta, timezone

    from app.routers.matches import FIRST_RUN_CLAIM_TTL, _claim_first_run, _release_first_run

    user = _user(db_session)
    old = datetime.now(timezone.utc)
    assert _claim_first_run(db_session, user, old) is True
    newer = old + FIRST_RUN_CLAIM_TTL + timedelta(seconds=1)
    assert _claim_first_run(db_session, user, newer) is True

    # The slow first run finally finishes and tries to tidy up after itself.
    assert _release_first_run(db_session, user.id, old) is False
    db_session.expire_all()
    assert db_session.get(User, user.id).first_run_started_at is not None


# --------------------------------------------------------------------------
# the digest half
# --------------------------------------------------------------------------


def _digest_seam(monkeypatch):
    """Stub the digest's own prose call.

    `Seams` patches the sweep's `write_reasons`; `build_digests` imported the
    same name into its own module, so the digest's call is a second seam and
    an unpatched one would reach a provider.
    """
    from app.jobs import build_digests
    from app.services.jobs.reasons import Prose, ProseRun

    async def fake_prose(db, *, user, facts, scored, jobs):
        run = ProseRun(model="gpt-6-luna")
        run.prose = [
            Prose(job_id=s.job_id, reason=f"Reason for {s.job_id}.", gap=None)
            for s in scored
        ]
        return run

    monkeypatch.setattr(build_digests, "write_reasons", fake_prose)


def test_the_first_run_builds_this_week_s_digest_too(client, db_session, monkeypatch):
    """The left column of the overview is the digest, and it was weekly only.

    A person who saves a preference on a Friday had an empty "this week's
    picks" until the following Monday 05:00 -- four days of a card that looks
    broken, on the one screen the whole first-run design exists to make pay
    off. The scores the sweep just wrote are exactly what the digest ranks, so
    building it here costs the prose call and nothing else.
    """
    from app.models import Digest
    from app.services import usage

    user = _ready_user(db_session, client)
    Seams(monkeypatch)
    _digest_seam(monkeypatch)
    assert client.post("/v1/me/matches/first-run").json()["started"] is True
    assert _wait_for(user.id, 3)
    assert _wait_for_digest(user.id)

    session = get_session_factory()()
    try:
        rows = session.query(Digest).filter(Digest.user_id == user.id).all()
        assert len(rows) == 1
        # This week's, not a week of its own: `_write` replaces on
        # (user_id, period_start), so Monday's run rebuilds over it rather
        # than colliding with it or leaving two records of one week.
        # Compared without zones: SQLite hands back what it was given, so the
        # stored value is naive while window_start() is aware. The same trap
        # _claim_is_live documents.
        stored = rows[0].period_start
        stored = stored if stored.tzinfo is None else stored.astimezone(timezone.utc).replace(tzinfo=None)
        assert stored == usage.window_start().replace(tzinfo=None)
        assert rows[0].status == "full"
    finally:
        session.close()


def test_a_digest_that_cannot_be_built_does_not_lose_the_scores(
    db_session, monkeypatch, caplog
):
    """The two halves of the run are independent, and the paid half comes first.

    The sweep commits its own scores before this is reached, so a digest that
    raises must not take them with it -- and must not strand the claim either.
    """
    import logging

    from app.routers import matches as mod

    user = _user(db_session)
    _pref(db_session, user)
    _facts(db_session, user)
    _posting(db_session)
    Seams(monkeypatch)

    async def bought_two(db, *, user, cap):
        db.add(JobScore(
            user_id=user.id, job_id=_posting(db).id, facts_input_hash="h" * 64,
            description_hash="d" * 64, model="m", tier="Strong fit", fit_score=0.9,
            outcome="scored", scored_at=datetime.now(timezone.utc),
        ))
        db.commit()
        return 1

    async def boom(db, **kwargs):
        raise RuntimeError("digest down")

    monkeypatch.setattr(mod, "sweep_user", bought_two)
    monkeypatch.setattr(mod, "build_for_user", boom)

    now = datetime.now(timezone.utc)
    assert mod._claim_first_run(db_session, user, now) is True
    with caplog.at_level(logging.WARNING):
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            mod._run(user.id, now)
        )
    db_session.expire_all()
    # The scores the sweep committed are still there...
    assert db_session.query(JobScore).filter(JobScore.user_id == user.id).count() == 1
    # ...the claim is back, so a retry is possible...
    assert db_session.get(User, user.id).first_run_started_at is None
    # ...and the failure is not silent.
    assert any("digest" in r.message.lower() for r in caplog.records), caplog.text


def test_a_claim_older_than_the_ttl_is_not_reported_as_running(db_session, client):
    """Otherwise the client waits on a worker that is never coming back.

    A claim this old is one `_claim_first_run` would hand to the next caller,
    so calling it live would be a different answer from the one the endpoint
    that takes claims gives. That is the bound on how wrong `running` can be.
    """
    from datetime import timedelta

    from app.routers import matches as mod

    user = _signed_in(client, db_session, sub=f"sub-fr-ttl-{time.monotonic_ns()}")
    _pref(db_session, user)
    stale = datetime.now(timezone.utc) - mod.FIRST_RUN_CLAIM_TTL - timedelta(seconds=1)
    user.first_run_started_at = stale
    db_session.commit()
    assert client.get("/v1/me/matches/progress").json()["running"] is False
    # And the endpoint that hands out claims agrees it is free.
    assert mod._claim_first_run(db_session, user, datetime.now(timezone.utc)) is True


def test_progress_says_whether_the_claim_is_still_held(db_session, monkeypatch, client):
    """What the client stops on.

    It used to stop on `scored >= expected`, which is true the moment the
    SCORES land -- before the digest is built. The client then reloaded into a
    dashboard whose left column was still empty, which is the bug this pair of
    changes exists to close. The claim covers both halves: it is taken before
    the task is scheduled and released in its `finally`.
    """
    user = _signed_in(client, db_session, sub=f"sub-fr-run-{time.monotonic_ns()}")
    _pref(db_session, user)
    assert client.get("/v1/me/matches/progress").json()["running"] is False

    from app.routers import matches as mod

    assert mod._claim_first_run(db_session, user, datetime.now(timezone.utc)) is True
    assert client.get("/v1/me/matches/progress").json()["running"] is True

    mod._release_first_run(db_session, user.id, db_session.get(User, user.id).first_run_started_at)
    assert client.get("/v1/me/matches/progress").json()["running"] is False
