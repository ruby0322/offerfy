"""The score sweep: who it runs for, what it buys, and where the money stops.

No test reaches a live provider. The paid seams are monkeypatched where the
sweep reaches them: `score_candidates` inside `score_cache` (which the sweep
reads through), and `write_reasons` and `build_facts` on the sweep module. They
are the three places this job spends money, and each stub records what it was
asked, so "was not bought" is an assertion on calls rather than on a side
effect. The clock is passed in everywhere (`now=`), never read.
"""

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.jobs import score_sweep
from app.jobs.score_sweep import SWEEP_CAP, SweepReport, main, prune, sweep_all, sweep_user
from app.models import (
    Digest,
    Job,
    JobFacets,
    JobPreference,
    JobScore,
    Resume,
    ResumeFacts,
    UsageCounter,
    User,
)
from app.services import usage
from app.services.jobs import score_cache
from app.services.jobs.reasons import Prose, ProseRun
from app.services.jobs.score import Scored, ScoreRun
from app.services.jobs.store import freshness_cutoff
from app.services.resume_facts import FactsBuildResult, bundle, input_hash
from app.services.revisions import pin_current_draft, set_draft_source

pytestmark = pytest.mark.anyio

_seq = itertools.count()
PERIOD = usage.window_start()
NOW = PERIOD + timedelta(days=2)
REBUILT_HASH = "n" * 64
COMPONENTS = {
    "requirements_met": 0.81, "seniority_fit": 0.72,
    "location_fit": 0.63, "direction_fit": 0.54,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --------------------------------------------------------------------------
# the world
# --------------------------------------------------------------------------


def _user(db, uid=None):
    n = next(_seq)
    user = User(google_sub=f"sub-sweep-{n}", email=f"s{n}@x.test", locale="en")
    if uid is not None:
        user.id = uid
    db.add(user)
    db.commit()
    return user


def _pref(db, user, *, activity="actively_looking"):
    db.add(JobPreference(
        user_id=user.id, target_functions="|backend|", target_locations="|tw|",
        target_seniority="mid", remote_required=None, activity=activity,
    ))
    db.commit()


def _facts(db, user):
    """A published master resume and the fresh ResumeFacts row built from it."""
    resume = Resume(
        title="Master", typst_source="= CV\nBackend engineer", source="create",
        locale="en", user_id=user.id, kind="master",
    )
    db.add(resume)
    db.flush()
    set_draft_source(db, resume, "= CV\nBackend engineer")
    pin_current_draft(db, resume)
    db.commit()
    found = bundle(db, user, fall_back=True)
    row = ResumeFacts(
        user_id=user.id, source_resume_id=found[0].id, source_revision_id=found[1],
        input_hash=input_hash(found[1], found[2], found[3]),
        facts_json=json.dumps({"headline": "Backend engineer", "roles": [], "locations": []}),
        model="gpt-6-luna",
    )
    db.add(row)
    db.commit()
    return row


def _posting(db, *, age_minutes=0, active=True, last_seen=None):
    """`age_minutes` larger means older, so first_seen orders the pool."""
    n = next(_seq)
    base = freshness_cutoff() + timedelta(days=2)
    job = Job(
        source="greenhouse", source_id=f"sw-{n}", company="Acme", title=f"Engineer {n}",
        apply_url="https://x/a", source_url="https://x/s",
        description_text="Requirements\n- Python",
        first_seen_at=base - timedelta(minutes=age_minutes),
        last_seen_at=last_seen or (freshness_cutoff() + timedelta(days=3)),
        is_active=active,
    )
    db.add(job)
    db.flush()
    db.add(JobFacets(job_id=job.id, country="TW", seniority="mid", function="backend",
                     description_hash=f"h{n}"))
    db.commit()
    return job


def _ready(db, uid=None):
    user = _user(db, uid)
    _pref(db, user)
    _facts(db, user)
    return user


def _scored(job_id, tier="Strong fit"):
    return Scored(job_id=job_id, tier=tier, fit_score=0.9, components=dict(COMPONENTS))


class Seams:
    """Records what the sweep asked of the paid calls, in order."""

    def __init__(self, monkeypatch, *, tier="Strong fit", prose_dropped=(),
                 prose_error=None, on_score=None, score_raises_for=()):
        self.trace: list[str] = []
        self.scored_jobs: list[list[str]] = []
        self.prose_jobs: list[list[str]] = []
        self.facts_seen: list[object] = []
        self.rebuilds = 0

        async def fake_score(db, *, user, facts, jobs, concurrency=4):
            self.trace.append(f"score:{user.id}")
            if user.id in score_raises_for:
                raise RuntimeError("database fault")
            self.scored_jobs.append([j.id for j in jobs])
            self.facts_seen.append(facts)
            if on_score is not None:
                on_score(db, user, jobs)
            run = ScoreRun(model="gpt-6-luna")
            run.scored = [_scored(j.id, tier) for j in jobs]
            return run

        async def fake_prose(db, *, user, facts, scored, jobs):
            self.trace.append(f"prose:{user.id}")
            self.prose_jobs.append([s.job_id for s in scored])
            run = ProseRun(model="gpt-6-luna")
            if prose_error is not None:
                run.error = prose_error
                return run
            run.dropped = [(s.job_id, "uncited") for s in scored if s.job_id in prose_dropped]
            run.prose = [
                Prose(job_id=s.job_id, reason=f"Reason for {s.job_id}.", gap=None)
                for s in scored if s.job_id not in prose_dropped
            ]
            return run

        async def forbid_rebuild(db, user):
            raise AssertionError("facts were rebuilt")

        monkeypatch.setattr(score_cache, "score_candidates", fake_score)
        monkeypatch.setattr(score_sweep, "write_reasons", fake_prose)
        monkeypatch.setattr(score_sweep, "build_facts", forbid_rebuild)


def _ceiling(monkeypatch, uusd):
    monkeypatch.setenv("GLOBAL_WEEKLY_CEILING_UUSD", str(uusd))
    get_settings.cache_clear()


def _spend(db, *, uusd):
    db.add(UsageCounter(
        subject_kind="user", subject_id=f"spender-{next(_seq)}",
        window_start=usage.window_start(NOW), uusd=uusd, turns=0, unmeasured=0,
    ))
    db.flush()


def _rows(db, user):
    return db.query(JobScore).filter(JobScore.user_id == user.id).all()


# --------------------------------------------------------------------------
# audience
# --------------------------------------------------------------------------


async def test_only_eligible_users_are_swept(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    ignored = _ready(db_session)
    live = _ready(db_session)
    _posting(db_session)
    for week in (14, 7):
        db_session.add(Digest(
            user_id=ignored.id, period_start=PERIOD - timedelta(days=week),
            built_at=PERIOD, model="m", input_hash="a" * 64, status="full",
            candidate_count=1, opened_at=None,
        ))
    db_session.commit()
    report = await sweep_all(db_session, now=NOW)
    assert report.swept == [live.id]
    assert report.skipped_not_engaged == [ignored.id]
    assert seams.trace == [f"score:{live.id}", f"prose:{live.id}"]


async def test_a_paused_preference_is_skipped(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _user(db_session)
    _pref(db_session, user, activity="paused")
    _facts(db_session, user)
    _posting(db_session)
    report = await sweep_all(db_session, now=NOW)
    assert report.skipped_not_engaged == [user.id] and report.swept == []
    assert seams.trace == []


async def test_a_user_with_no_preference_row_is_skipped(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _user(db_session)
    _facts(db_session, user)
    _posting(db_session)
    report = await sweep_all(db_session, now=NOW)
    assert report.skipped_no_preference == [user.id] and report.swept == []
    assert seams.trace == []


async def test_a_first_timer_with_a_preference_row_is_swept(db_session, monkeypatch):
    Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session)
    report = await sweep_all(db_session, now=NOW)
    assert report.swept == [user.id] and report.bought == 1


# --------------------------------------------------------------------------
# the cap
# --------------------------------------------------------------------------


async def test_the_default_cap_is_thirty(db_session, monkeypatch):
    assert SWEEP_CAP == 30
    seams = Seams(monkeypatch, tier="Worth a look")
    user = _ready(db_session)
    for i in range(32):
        _posting(db_session, age_minutes=i)
    report = await sweep_all(db_session, now=NOW)
    assert report.bought == 30 and report.bought_by_user == {user.id: 30}
    assert len(seams.scored_jobs[0]) == 30


async def test_the_cap_bounds_purchases_per_user_per_pass(db_session, monkeypatch):
    seams = Seams(monkeypatch, tier="Worth a look")
    user = _ready(db_session)
    jobs = [_posting(db_session, age_minutes=i) for i in range(5)]
    report = await sweep_all(db_session, now=NOW, cap=2)
    assert report.bought == 2
    # Newest first: age_minutes 0 and 1.
    assert seams.scored_jobs == [[jobs[0].id, jobs[1].id]]
    assert len(_rows(db_session, user)) == 2


async def test_a_broad_preference_fills_in_over_several_passes(db_session, monkeypatch):
    seams = Seams(monkeypatch, tier="Worth a look")
    user = _ready(db_session)
    jobs = [_posting(db_session, age_minutes=i) for i in range(5)]
    bought = [(await sweep_all(db_session, now=NOW, cap=2)).bought for _ in range(4)]
    # 2 + 2 + 1, then a pass over a finished pool costs nothing: cached pairs
    # do not use up the allowance and are not bought twice.
    assert bought == [2, 2, 1, 0]
    assert seams.scored_jobs == [
        [jobs[0].id, jobs[1].id], [jobs[2].id, jobs[3].id], [jobs[4].id]
    ]
    assert len(_rows(db_session, user)) == 5


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


def _stale_until_rebuilt(monkeypatch, seams):
    """facts_are_stale answers by the row's hash; build_facts rewrites it."""
    monkeypatch.setattr(
        score_sweep, "facts_are_stale",
        lambda db, user, facts: facts.input_hash != REBUILT_HASH,
    )

    async def fake_build(db, user):
        seams.rebuilds += 1
        seams.trace.append(f"facts:{user.id}")
        row = db.query(ResumeFacts).filter(ResumeFacts.user_id == user.id).one()
        row.input_hash = REBUILT_HASH
        return FactsBuildResult(
            facts=row, model="gpt-6-luna", usage={"input_tokens": 900}, resume_id="r2"
        )

    monkeypatch.setattr(score_sweep, "build_facts", fake_build)


async def test_stale_facts_are_rebuilt_before_scoring(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    _stale_until_rebuilt(monkeypatch, seams)
    user = _ready(db_session)
    _posting(db_session)
    await sweep_all(db_session, now=NOW)
    assert seams.trace == [f"facts:{user.id}", f"score:{user.id}", f"prose:{user.id}"]
    assert seams.facts_seen[0].input_hash == REBUILT_HASH


async def test_the_rebuilt_facts_hash_is_what_gets_recorded(db_session, monkeypatch):
    """The row carries the REBUILT hash, so the next pass sees its work as fresh."""
    seams = Seams(monkeypatch)
    _stale_until_rebuilt(monkeypatch, seams)
    user = _ready(db_session)
    _posting(db_session)
    await sweep_all(db_session, now=NOW)
    assert {r.facts_input_hash for r in _rows(db_session, user)} == {REBUILT_HASH}
    seams.scored_jobs.clear()
    again = await sweep_all(db_session, now=NOW)
    # One extraction per resume edit, not one per pass; and nothing re-bought.
    assert seams.rebuilds == 1
    assert again.bought == 0 and seams.scored_jobs == []


async def test_facts_are_not_rebuilt_when_there_is_nothing_to_judge(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    _stale_until_rebuilt(monkeypatch, seams)
    user = _ready(db_session)  # no postings at all
    report = await sweep_all(db_session, now=NOW)
    assert seams.rebuilds == 0 and seams.trace == []
    assert report.swept == [user.id] and report.bought == 0


async def test_a_failed_rebuild_means_that_user_is_not_swept(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    monkeypatch.setattr(score_sweep, "facts_are_stale", lambda db, user, facts: True)

    async def failing(db, user):
        seams.trace.append("facts")
        return FactsBuildResult(error="llm_not_configured")

    monkeypatch.setattr(score_sweep, "build_facts", failing)
    _ready(db_session)
    _posting(db_session)
    report = await sweep_all(db_session, now=NOW)
    # Never scored against a resume we know they have replaced.
    assert seams.trace == ["facts"] and report.swept == [] and db_session.query(JobScore).count() == 0


# --------------------------------------------------------------------------
# prose
# --------------------------------------------------------------------------


async def test_prose_is_bought_for_strong_tiers_only(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    strong, weak = _posting(db_session, age_minutes=0), _posting(db_session, age_minutes=1)

    async def mixed(db, *, user, facts, jobs, concurrency=4):
        run = ScoreRun(model="gpt-6-luna")
        run.scored = [
            _scored(strong.id, "Good fit"), _scored(weak.id, "Worth a look"),
        ]
        return run

    monkeypatch.setattr(score_cache, "score_candidates", mixed)
    await sweep_all(db_session, now=NOW)
    assert seams.prose_jobs == [[strong.id]]
    by_job = {r.job_id: r for r in _rows(db_session, user)}
    assert by_job[strong.id].reason == f"Reason for {strong.id}."
    assert by_job[strong.id].prose_model == "gpt-6-luna"
    assert by_job[weak.id].reason is None


async def test_prose_is_only_for_arrivals_of_this_pass(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    first = _posting(db_session, age_minutes=5)
    await sweep_all(db_session, now=NOW)
    second = _posting(db_session, age_minutes=0)
    seams.prose_jobs.clear()
    await sweep_all(db_session, now=NOW)
    assert seams.prose_jobs == [[second.id]]
    assert first.id not in {j for batch in seams.prose_jobs for j in batch}
    assert len(_rows(db_session, user)) == 2


async def test_prose_rejected_by_the_validator_is_recorded_and_not_rebought(
    db_session, monkeypatch
):
    user = _ready(db_session)
    job = _posting(db_session)
    seams = Seams(monkeypatch, prose_dropped=(job.id,))
    await sweep_all(db_session, now=NOW)
    row = _rows(db_session, user)[0]
    assert row.outcome == "prose_rejected" and row.reason is None and row.tier == "Strong fit"
    seams.trace.clear()
    await sweep_all(db_session, now=NOW)
    # The score stands and neither it nor the sentence is bought again.
    assert seams.trace == []


async def test_a_failed_prose_call_keeps_the_score(db_session, monkeypatch):
    seams = Seams(monkeypatch, prose_error="invalid_payload")
    user = _ready(db_session)
    _posting(db_session)
    await sweep_all(db_session, now=NOW)
    row = _rows(db_session, user)[0]
    assert row.outcome == "scored" and row.tier == "Strong fit" and row.reason is None
    assert seams.trace.count(f"prose:{user.id}") == 1


# --------------------------------------------------------------------------
# the ceiling
# --------------------------------------------------------------------------


async def test_the_ceiling_stops_cleanly_and_lists_who_got_nothing(db_session, monkeypatch):
    def overspend(db, user, jobs):
        _spend(db, uusd=10)  # the meter's bill for this user, landing mid-user

    seams = Seams(monkeypatch, on_score=overspend)
    _ceiling(monkeypatch, 1_000_000)
    a, b, c = (_ready(db_session, uid) for uid in ("u-aaa", "u-bbb", "u-ccc"))
    _posting(db_session)
    _spend(db_session, uusd=999_999)
    db_session.commit()
    report = await sweep_all(db_session, now=NOW)
    assert report.swept == ["u-aaa"]
    assert report.skipped_over_ceiling == ["u-bbb", "u-ccc"]
    assert len(_rows(db_session, a)) == 1 and _rows(db_session, b) == [] and _rows(db_session, c) == []
    assert [t for t in seams.trace if t.startswith("score:")] == ["score:u-aaa"]


async def test_a_user_who_starts_always_finishes(db_session, monkeypatch):
    def overspend(db, user, jobs):
        _spend(db, uusd=2_000_000)

    Seams(monkeypatch, on_score=overspend, tier="Worth a look")
    _ceiling(monkeypatch, 1_000_000)
    a, b = _ready(db_session, "u-aaa"), _ready(db_session, "u-bbb")
    for i in range(4):
        _posting(db_session, age_minutes=i)
    report = await sweep_all(db_session, now=NOW)
    # The ceiling was crossed during u-aaa's pass, and it still scored the
    # whole pool: no half-scored pool is ever left behind.
    assert len(_rows(db_session, a)) == 4 and _rows(db_session, b) == []
    assert report.skipped_over_ceiling == ["u-bbb"]


async def test_a_zero_ceiling_is_disabled_not_a_stop(db_session, monkeypatch):
    Seams(monkeypatch)
    _ceiling(monkeypatch, 0)
    _ready(db_session)
    _posting(db_session)
    report = await sweep_all(db_session, now=NOW)
    assert report.bought == 1 and report.skipped_over_ceiling == []


async def test_longest_unswept_goes_first(db_session, monkeypatch):
    seams = Seams(monkeypatch, tier="Worth a look")
    recent, stale, never = (_ready(db_session, uid) for uid in ("u-1", "u-2", "u-3"))
    old = _posting(db_session, age_minutes=100)
    for user, days in ((recent, 1), (stale, 20)):
        row = score_cache.write_back(
            db_session, user=user, facts=_facts_of(db_session, user), job=old,
            model="m", scored=_scored(old.id, "Worth a look"),
        )
        row.scored_at = NOW - timedelta(days=days)
    db_session.commit()
    _posting(db_session, age_minutes=0)
    await sweep_all(db_session, now=NOW)
    assert [t for t in seams.trace if t.startswith("score:")] == [
        "score:u-3", "score:u-2", "score:u-1",
    ]
    assert never.id == "u-3"


def _facts_of(db, user):
    return db.query(ResumeFacts).filter(ResumeFacts.user_id == user.id).one()


async def test_one_users_crash_does_not_cost_the_others_the_pass(db_session, monkeypatch):
    Seams(monkeypatch, score_raises_for=("u-aaa",))
    a, b = _ready(db_session, "u-aaa"), _ready(db_session, "u-bbb")
    _posting(db_session)
    report = await sweep_all(db_session, now=NOW)
    assert report.swept == ["u-bbb"] and not hasattr(report, "failed")
    assert _rows(db_session, a) == [] and len(_rows(db_session, b)) == 1


# --------------------------------------------------------------------------
# pruning
# --------------------------------------------------------------------------


def _row_for(db, user, job):
    row = score_cache.write_back(
        db, user=user, facts=_facts_of(db, user), job=job, model="m",
        scored=_scored(job.id, "Worth a look"),
    )
    db.commit()
    return row


async def test_prune_deletes_dead_rows_and_keeps_live_ones(db_session, monkeypatch):
    user = _ready(db_session)
    live = _posting(db_session)
    gone = _posting(db_session, active=False)
    expired = _posting(db_session, last_seen=freshness_cutoff() - timedelta(days=1))
    for job in (live, gone, expired):
        _row_for(db_session, user, job)
    assert prune(db_session) == 2
    assert [r.job_id for r in _rows(db_session, user)] == [live.id]


async def test_pruning_runs_outside_the_user_loop(db_session, monkeypatch):
    """A user who has fallen out of is_eligible still has dead rows pruned."""
    Seams(monkeypatch)
    user = _ready(db_session)
    gone = _posting(db_session, active=False)
    _row_for(db_session, user, gone)
    for week in (14, 7):
        db_session.add(Digest(
            user_id=user.id, period_start=PERIOD - timedelta(days=week),
            built_at=PERIOD, model="m", input_hash="a" * 64, status="full",
            candidate_count=1, opened_at=None,
        ))
    db_session.commit()
    report = await sweep_all(db_session, now=NOW)
    assert report.skipped_not_engaged == [user.id]
    assert report.pruned == 1 and _rows(db_session, user) == []


async def test_pruning_runs_on_a_pass_that_scores_nothing(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _user(db_session)  # no preference: nobody gets scored
    _facts(db_session, user)
    _row_for(db_session, user, _posting(db_session, active=False))
    report = await sweep_all(db_session, now=NOW)
    assert seams.trace == [] and report.swept == [] and report.pruned == 1


async def test_pruning_runs_when_the_ceiling_has_stopped_scoring(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    _ceiling(monkeypatch, 1_000_000)
    _spend(db_session, uusd=2_000_000)
    user = _ready(db_session)
    _posting(db_session)
    _row_for(db_session, user, _posting(db_session, active=False))
    report = await sweep_all(db_session, now=NOW)
    assert report.skipped_over_ceiling == [user.id] and seams.trace == []
    assert report.pruned == 1


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------


async def test_dry_run_reports_the_pass_size_and_buys_nothing(db_session, monkeypatch, capsys):
    seams = Seams(monkeypatch)
    _ceiling(monkeypatch, 1_000_000)
    _spend(db_session, uusd=2_000_000)  # a real pass would stop; the number is still wanted
    a, b = _ready(db_session, "u-aaa"), _ready(db_session, "u-bbb")
    idle = _ready(db_session, "u-ccc")
    for i in range(3):
        _posting(db_session, age_minutes=i)
    gone = _posting(db_session, active=False)
    _row_for(db_session, idle, gone)
    for job in db_session.query(Job).filter(Job.is_active.is_(True)).all():
        _row_for(db_session, idle, job)
    before = db_session.query(JobScore).count()
    report = await sweep_all(db_session, now=NOW, cap=2, dry_run=True)
    assert report.bought == 4 and report.bought_by_user == {"u-aaa": 2, "u-bbb": 2, "u-ccc": 0}
    assert report.pruned == 1
    assert seams.trace == [] and db_session.query(JobScore).count() == before


async def test_dry_run_counts_a_stale_person_at_their_whole_capped_pool(db_session, monkeypatch):
    Seams(monkeypatch)
    monkeypatch.setattr(score_sweep, "facts_are_stale", lambda db, user, facts: True)
    user = _ready(db_session)
    for i in range(3):
        _posting(db_session, age_minutes=i)
    report = await sweep_all(db_session, now=NOW, dry_run=True)
    assert report.bought == 3 and report.would_rebuild_facts == [user.id]


def test_main_dry_run_prints_pairs_and_people(db_session, monkeypatch, capsys):
    Seams(monkeypatch)
    _ready(db_session)
    _posting(db_session)
    monkeypatch.setattr(score_sweep, "get_engine", lambda: None)
    monkeypatch.setattr(score_sweep, "get_session_factory", lambda: (lambda: db_session))
    monkeypatch.setattr(db_session, "close", lambda: None)
    assert main(["--dry-run"]) == 0
    assert capsys.readouterr().out.startswith("would buy 1 pairs for 1 people")
    assert db_session.query(JobScore).count() == 0
