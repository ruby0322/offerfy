"""The scheduled digest build: who it runs for, what it writes, where it stops.

No test reaches a live provider. The two provider seams the build consumes --
`score_candidates` and `write_reasons` -- are monkeypatched on the build module
itself, which is also the point: they are the only two places this job spends
money, and a test that had to stub httpx to reach them would be testing the
wrong module. The two tests that do exercise the real scoring path stub
`httpx.AsyncClient` the way `test_jobs_score.py` does.

The clock is passed in everywhere (`now=`), never read: eligibility reads the
week boundary and the ceiling reads the spend window, and a test that let those
drift onto the wall clock would pass on a Sunday and fail on a Monday.
"""

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.jobs import build_digests
from app.jobs.build_digests import (
    CANDIDATE_CAP,
    BuildReport,
    build_all,
    build_for_user,
    is_eligible,
    main,
)
from app.models import (
    Digest,
    DigestItem,
    Job,
    JobFacets,
    JobPreference,
    LlmCall,
    Resume,
    ResumeFacts,
    UsageCounter,
    User,
)
from app.services import usage
from app.services.jobs.digest import (
    ITEMS_PER_TRACK,
    STATUSES,
    TRACKS,
    UNRANKED_TRACK,
)
from app.services.jobs.reasons import Prose, ProseRun
from app.services.jobs.score import ScoreRun, Scored
from app.services.jobs.store import freshness_cutoff
from app.services.resume_facts import bundle, input_hash
from app.services.revisions import pin_current_draft, set_draft_source

pytestmark = pytest.mark.anyio

_seq = itertools.count()

# A Monday, so period_start is a real window_start and NOW sits inside the week
# being built -- the arrangement the build documents for a backfill.
PERIOD = datetime(2026, 11, 9, 16, 0, tzinfo=timezone.utc)
NOW = PERIOD + timedelta(days=2)
PERIOD_ARG = PERIOD.isoformat()
# Older than `new_this_week` reaches back (NEW_WINDOW, seven days before
# PERIOD), so a posting first seen then can only arrive through strong_fit.
BEFORE_WINDOW = PERIOD - timedelta(days=10)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --------------------------------------------------------------------------
# fixtures of the world
# --------------------------------------------------------------------------


def _user(db, uid=None):
    n = next(_seq)
    user = User(google_sub=f"sub-digest-{n}", email=f"d{n}@x.test", locale="en")
    if uid is not None:
        user.id = uid
    db.add(user)
    db.commit()
    return user


def _pref(db, user, *, activity="actively_looking", functions="|backend|",
          locations="|tw|"):
    row = JobPreference(
        user_id=user.id, target_functions=functions, target_locations=locations,
        target_seniority="mid", remote_required=None, activity=activity,
    )
    db.add(row)
    db.commit()
    return row


def _facts(db, user):
    """A published master resume and the fresh ResumeFacts row built from it.

    Fresh by construction rather than by assertion: the row's input_hash is
    computed with the same `bundle` + `input_hash` pair `facts_are_stale`
    reads, so the staleness answer here is the production one.
    """
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


def _posting(db, *, first_seen=None, function="backend", seniority="mid", country="TW"):
    n = next(_seq)
    job = Job(
        source="greenhouse", source_id=f"dg-{n}", company="Acme", title=f"Engineer {n}",
        apply_url="https://x/a", source_url="https://x/s",
        description_text="Requirements\n- Python",
        first_seen_at=first_seen or (freshness_cutoff() + timedelta(days=1)),
        last_seen_at=freshness_cutoff() + timedelta(days=3),
        is_active=True,
    )
    db.add(job)
    db.flush()
    db.add(JobFacets(job_id=job.id, country=country, seniority=seniority,
                     function=function, description_hash=f"h{n}"))
    db.commit()
    return job


def _prior(db, user, *, period, opened=False):
    row = Digest(
        user_id=user.id, period_start=period, built_at=period, model="gpt-6-luna",
        input_hash="a" * 64, status="full", candidate_count=3,
        opened_at=period + timedelta(hours=1) if opened else None,
    )
    db.add(row)
    db.commit()
    return row


def _ready(db, uid=None):
    """A user who is eligible, has a preference, fresh facts and one match."""
    user = _user(db, uid)
    _pref(db, user)
    _facts(db, user)
    return user


# --------------------------------------------------------------------------
# the two provider seams, stubbed
# --------------------------------------------------------------------------


COMPONENTS = {
    "requirements_met": 0.81, "seniority_fit": 0.72,
    "location_fit": 0.63, "direction_fit": 0.54,
}


def _scored(job_id, tier="Strong fit", fit=0.9):
    return Scored(job_id=job_id, tier=tier, fit_score=fit, components=dict(COMPONENTS))


class Seams:
    """Records what the build asked of the two paid calls, in order."""

    def __init__(self, monkeypatch, *, score_error=None, prose_error=None,
                 prose_raises=None, on_score=None, tier="Strong fit"):
        self.trace: list[str] = []
        self.scored_jobs: list[list[str]] = []
        self.prose_jobs: list[list[str]] = []
        self.facts_seen: list[object] = []

        async def fake_score(db, *, user, facts, jobs, concurrency=4):
            self.trace.append(f"score:{user.id}")
            self.scored_jobs.append([j.id for j in jobs])
            self.facts_seen.append(facts)
            if on_score is not None:
                on_score(db, user, jobs)
            run = ScoreRun(model="gpt-6-luna")
            if score_error is not None:
                run.error = score_error
                return run
            run.scored = [_scored(j.id, tier) for j in jobs]
            run.usage = {"input_tokens": 1200}
            return run

        async def fake_prose(db, *, user, facts, scored, jobs):
            self.trace.append(f"prose:{user.id}")
            self.prose_jobs.append([s.job_id for s in scored])
            if prose_raises is not None:
                raise prose_raises
            run = ProseRun(model="gpt-6-luna")
            if prose_error is not None:
                run.error = prose_error
                return run
            run.prose = [
                Prose(job_id=s.job_id, reason=f"Reason for {s.job_id}.", gap=None)
                for s in scored
            ]
            return run

        monkeypatch.setattr(build_digests, "score_candidates", fake_score)
        monkeypatch.setattr(build_digests, "write_reasons", fake_prose)


def _ceiling(monkeypatch, uusd):
    monkeypatch.setenv("GLOBAL_WEEKLY_CEILING_UUSD", str(uusd))
    get_settings.cache_clear()


def _spend(db, *, uusd, window=None, commit=True):
    """Put real money in the window the ceiling reads."""
    db.add(UsageCounter(
        subject_kind="user", subject_id=f"spender-{next(_seq)}",
        window_start=window or usage.window_start(NOW),
        uusd=uusd, turns=0, unmeasured=0,
    ))
    # flush, not commit, when this stands in for a mid-user provider bill: the
    # real meter commits on its own session, and committing the build's one
    # here would settle a transaction the build has not finished.
    db.commit() if commit else db.flush()


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------


async def test_no_preference_row_means_no_digest(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _user(db_session)
    _facts(db_session, user)
    _posting(db_session)
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is None
    assert db_session.query(Digest).count() == 0
    # Nothing was scored: a person with no stated preference costs nothing.
    assert seams.trace == []


async def test_a_paused_preference_is_skipped(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _user(db_session)
    _pref(db_session, user, activity="paused")
    _facts(db_session, user)
    _posting(db_session)
    assert is_eligible(db_session, user=user, now=NOW) is False
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is None
    assert db_session.query(Digest).count() == 0 and seams.trace == []


async def test_a_first_time_user_is_always_built_for_once(db_session, monkeypatch):
    Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session)
    assert is_eligible(db_session, user=user, now=NOW) is True
    first = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert first is not None and first.status == "full"

    # Once. The digest they were given and never opened is their whole history
    # next week, so the second week is where the engagement rule bites.
    later = PERIOD + timedelta(days=7)
    assert is_eligible(db_session, user=user, now=later + timedelta(days=2)) is False
    assert await build_for_user(
        db_session, user=user, period_start=later, now=later + timedelta(days=2)
    ) is None
    assert db_session.query(Digest).count() == 1


async def test_a_user_who_opened_neither_of_their_last_two_is_skipped(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session)
    # Opened three weeks ago, ignored twice since: the window is the last two,
    # so an older open does not keep someone alive forever.
    _prior(db_session, user, period=PERIOD - timedelta(days=21), opened=True)
    _prior(db_session, user, period=PERIOD - timedelta(days=14))
    _prior(db_session, user, period=PERIOD - timedelta(days=7))
    assert is_eligible(db_session, user=user, now=NOW) is False
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is None
    assert seams.trace == []


async def test_a_user_who_opened_one_of_the_last_two_is_built_for(db_session, monkeypatch):
    Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session)
    _prior(db_session, user, period=PERIOD - timedelta(days=14), opened=True)
    _prior(db_session, user, period=PERIOD - timedelta(days=7))
    assert is_eligible(db_session, user=user, now=NOW) is True
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is not None


async def test_all_users_bypasses_the_audience_gate(db_session, monkeypatch):
    """The gate is a parameter, so redirecting it is a call-site change."""
    Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session)
    _prior(db_session, user, period=PERIOD - timedelta(days=14))
    _prior(db_session, user, period=PERIOD - timedelta(days=7))
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is None
    built = await build_for_user(
        db_session, user=user, period_start=PERIOD, now=NOW, require_eligible=False
    )
    assert built is not None
    # Paused is not an audience question, and --all-users does not revive it:
    # the person asked for the mail to stop.
    other = _ready(db_session)
    db_session.query(JobPreference).filter(
        JobPreference.user_id == other.id
    ).one().activity = "paused"
    db_session.commit()
    assert await build_for_user(
        db_session, user=other, period_start=PERIOD, now=NOW, require_eligible=False
    ) is None


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------


async def test_stale_facts_are_rebuilt_before_scoring_not_after(db_session, monkeypatch):
    """Order, and which object reached the scorer.

    Scoring first and refreshing after would produce a digest judged on text
    the person has since changed -- which reads as a bug in the score, not in
    the schedule.
    """
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session)
    rebuilt = ResumeFacts(
        user_id=user.id, source_resume_id=db_session.query(Resume).first().id,
        source_revision_id="rev2", input_hash="n" * 64,
        facts_json=json.dumps({"roles": [], "locations": []}), model="gpt-6-luna",
    )

    monkeypatch.setattr(build_digests, "facts_are_stale", lambda db, user, facts: True)

    async def fake_build(db, user):
        seams.trace.append(f"facts:{user.id}")
        return build_digests.FactsBuildResult(
            facts=rebuilt, model="gpt-6-luna", usage={"input_tokens": 900},
            resume_id="r2",
        )

    monkeypatch.setattr(build_digests, "build_facts", fake_build)

    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert seams.trace == [f"facts:{user.id}", f"score:{user.id}", f"prose:{user.id}"]
    assert seams.facts_seen == [rebuilt]


async def test_unbuildable_resume_facts_means_no_digest(db_session, monkeypatch):
    """The fifth row of the spec's failure table.

    No ResumeFacts row and nothing to build one from: the real `build_facts`
    runs, fails, and the build stops. A digest built on absent facts would be
    scored against nothing, and the dashboard's resume-health card already has
    the reason.
    """
    seams = Seams(monkeypatch)
    user = _user(db_session)
    _pref(db_session, user)
    _posting(db_session)
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is None
    assert db_session.query(Digest).count() == 0
    assert seams.trace == []


async def test_a_failed_rebuild_of_stale_facts_means_no_digest(db_session, monkeypatch):
    """Same ruling when the facts exist but could not be refreshed.

    Deliberately not "score against the stale ones": the whole point of
    rebuilding first is that the person's resume has moved, and judging them
    against text they have replaced is the failure the order exists to stop.
    """
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session)
    monkeypatch.setattr(build_digests, "facts_are_stale", lambda db, user, facts: True)

    async def fake_build(db, user):
        seams.trace.append("facts")
        return build_digests.FactsBuildResult(error="llm_not_configured")

    monkeypatch.setattr(build_digests, "build_facts", fake_build)
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is None
    assert seams.trace == ["facts"]
    assert db_session.query(Digest).count() == 0


# --------------------------------------------------------------------------
# what gets written
# --------------------------------------------------------------------------


async def test_the_candidate_set_is_capped_and_ordered_by_recency(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    base = freshness_cutoff() + timedelta(days=1)
    jobs = [_posting(db_session, first_seen=base + timedelta(minutes=i))
            for i in range(CANDIDATE_CAP + 3)]
    await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    asked = seams.scored_jobs[0]
    assert len(asked) == CANDIDATE_CAP
    # Freshest first, and the three oldest never cost anything.
    assert asked == [j.id for j in reversed(jobs)][:CANDIDATE_CAP]


async def test_new_this_week_comes_from_first_seen_at_in_the_window(db_session, monkeypatch):
    Seams(monkeypatch, tier="Worth a look")
    user = _ready(db_session)
    fresh = _posting(db_session, first_seen=PERIOD + timedelta(hours=3))
    _posting(db_session, first_seen=BEFORE_WINDOW)
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    # "Worth a look" is in new_this_week and out of strong_fit, so the only
    # items are the ones the window admitted.
    assert {(i.track, i.job_id) for i in built.items} == {("new_this_week", fresh.id)}


async def test_new_this_week_covers_a_week_at_production_timing(
    db_session, monkeypatch
):
    """The timer fires Monday 05:00; period_start is Monday 00:00.

    So at build time the period is five hours old and the ingest has made one
    pass inside it. A window opening at `period_start` would head roughly 3% of
    the week's new postings with "New this week" -- and the digest is frozen
    once written, so Tuesday's postings never join it. The window therefore
    looks BACK a week from `period_start`, with no upper bound, so the track
    means what its heading says and still picks up whatever arrived in the
    hours before the run.
    """
    Seams(monkeypatch, tier="Worth a look")
    user = _ready(db_session)
    run_at = PERIOD + timedelta(hours=5)  # what the timer actually produces
    wednesday = _posting(db_session, first_seen=PERIOD - timedelta(days=5))
    friday = _posting(db_session, first_seen=PERIOD - timedelta(days=3))
    since_midnight = _posting(db_session, first_seen=PERIOD + timedelta(hours=2))
    last_month = _posting(db_session, first_seen=PERIOD - timedelta(days=30))

    built = await build_for_user(
        db_session, user=user, period_start=PERIOD, now=run_at
    )
    fresh = {i.job_id for i in built.items if i.track == "new_this_week"}
    assert fresh == {wednesday.id, friday.id, since_midnight.id}
    assert last_month.id not in fresh


async def test_items_carry_the_tier_score_components_and_prose(db_session, monkeypatch):
    Seams(monkeypatch)
    user = _ready(db_session)
    job = _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert built.status == "full" and built.candidate_count == 1
    assert built.model == "gpt-6-luna" and len(built.input_hash) == 64
    # One posting, both tracks: a fresh strong fit is legitimately in each.
    assert sorted(i.track for i in built.items) == ["new_this_week", "strong_fit"]
    for item in built.items:
        assert item.job_id == job.id and item.rank == 1
        assert item.tier == "Strong fit" and item.fit_score == 0.9
        assert item.reason == f"Reason for {job.id}."
        assert item.gap is None
        assert item.requirements_met == COMPONENTS["requirements_met"]
        assert item.seniority_fit == COMPONENTS["seniority_fit"]
        assert item.location_fit == COMPONENTS["location_fit"]
        assert item.direction_fit == COMPONENTS["direction_fit"]


async def test_prose_is_only_bought_for_the_items_that_will_be_shown(db_session, monkeypatch):
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    for _ in range(ITEMS_PER_TRACK + 4):
        _posting(db_session, first_seen=BEFORE_WINDOW)
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert len(built.items) == ITEMS_PER_TRACK
    # Nine scored, five shown, five written about: the prose call is priced per
    # posting it carries.
    assert len(seams.scored_jobs[0]) == ITEMS_PER_TRACK + 4
    assert len(seams.prose_jobs[0]) == ITEMS_PER_TRACK


async def test_the_digest_size_is_a_parameter(db_session, monkeypatch):
    Seams(monkeypatch)
    user = _ready(db_session)
    for _ in range(4):
        _posting(db_session, first_seen=BEFORE_WINDOW)
    built = await build_for_user(
        db_session, user=user, period_start=PERIOD, now=NOW, items_per_track=2
    )
    assert len(built.items) == 2


async def test_decisions_absent_writes_a_degraded_digest(db_session, monkeypatch):
    """Row one of the failure table: prefilter order, and no invented judgement."""
    seams = Seams(monkeypatch, score_error="llm_not_configured")
    user = _ready(db_session)
    base = freshness_cutoff() + timedelta(days=1)
    jobs = [_posting(db_session, first_seen=base + timedelta(minutes=i)) for i in range(7)]
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert built.status == "degraded" and built.candidate_count == 7
    # The items are still there, in the one order the prefilter can defend.
    assert [i.job_id for i in built.items] == [j.id for j in reversed(jobs)][:ITEMS_PER_TRACK]
    assert [i.rank for i in built.items] == [1, 2, 3, 4, 5]
    # Under a track of its own, never one of the two that make a claim: these
    # postings were first seen weeks ago, so "new_this_week" would be false of
    # them, and nothing judged them, so "strong_fit" would be worse.
    assert {i.track for i in built.items} == {UNRANKED_TRACK}
    assert UNRANKED_TRACK not in TRACKS
    for item in built.items:
        assert item.tier is None and item.fit_score is None
        assert item.reason is None and item.gap is None
        assert item.requirements_met is None and item.seniority_fit is None
        assert item.location_fit is None and item.direction_fit is None
    # No tiers means nothing to write prose about, and prose is not free.
    assert seams.trace == [f"score:{user.id}"]


async def test_a_prose_failure_keeps_tiers_and_scores(db_session, monkeypatch):
    """Row three: the ranking is real, so the digest is not degraded -- just bare."""
    Seams(monkeypatch, prose_error="no_output")
    user = _ready(db_session)
    _posting(db_session, first_seen=BEFORE_WINDOW)
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert built.status == "full" and len(built.items) == 1
    item = built.items[0]
    assert item.tier == "Strong fit" and item.fit_score == 0.9
    assert item.requirements_met == COMPONENTS["requirements_met"]
    assert item.reason is None and item.gap is None


async def test_an_empty_prefilter_writes_status_empty(db_session, monkeypatch):
    """Row four. Nothing matched, so nothing was scored and nothing was paid for."""
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session, function="design")
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert built is not None
    assert built.status == "empty" and built.candidate_count == 0
    assert built.items == []
    assert seams.trace == []


async def test_an_empty_prefilter_does_not_pay_to_rebuild_stale_facts(
    db_session, monkeypatch
):
    """The rebuild is a provider call; the candidate set is free SQL.

    A person with nothing matching their filters would otherwise buy a facts
    rebuild to produce `status="empty"`, and that spend comes out of the
    global ceiling that decides who else gets a digest at all this week.
    """
    seams = Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session, function="design")  # matches nothing they asked for
    monkeypatch.setattr(build_digests, "facts_are_stale", lambda db, user, facts: True)

    async def fake_build(db, user):
        seams.trace.append("facts")
        return build_digests.FactsBuildResult()

    monkeypatch.setattr(build_digests, "build_facts", fake_build)
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert built is not None and built.status == "empty"
    assert seams.trace == []


async def test_no_facts_and_no_candidates_costs_nothing_to_discover(
    db_session, monkeypatch
):
    """Still no digest -- the spec's fifth row -- but nothing billed to learn it."""
    seams = Seams(monkeypatch)
    user = _user(db_session)
    _pref(db_session, user)
    _posting(db_session, function="design")

    async def fake_build(db, user):
        seams.trace.append("facts")
        return build_digests.FactsBuildResult()

    monkeypatch.setattr(build_digests, "build_facts", fake_build)
    assert await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW) is None
    assert seams.trace == [] and db_session.query(Digest).count() == 0


async def test_nothing_qualifying_is_also_empty(db_session, monkeypatch):
    Seams(monkeypatch, tier="Not a fit")
    user = _ready(db_session)
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert built.status == "empty" and built.candidate_count == 1 and built.items == []


async def test_every_status_the_build_writes_is_in_the_vocabulary(db_session, monkeypatch):
    """The vocabulary lives in digest.py, with its one writer -- which is here."""
    Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    full = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    Seams(monkeypatch, score_error="llm_not_configured")
    other = _ready(db_session)
    degraded = await build_for_user(db_session, user=other, period_start=PERIOD, now=NOW)
    empty = await build_for_user(
        db_session, user=_ready(db_session), period_start=PERIOD, now=NOW,
        candidate_cap=0,
    )
    assert {full.status, degraded.status, empty.status} == set(STATUSES)


async def test_a_digest_is_never_partially_written(db_session, monkeypatch):
    """One transaction per user.

    The failure is injected after scoring has succeeded, which is exactly where
    an implementation that inserted the digest row and then filled it in would
    leave a header with no items -- a digest that renders as "we found nothing
    for you" when in fact the build crashed.
    """
    Seams(monkeypatch, prose_raises=RuntimeError("provider exploded"))
    user = _ready(db_session)
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    with pytest.raises(RuntimeError):
        await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    db_session.rollback()
    assert db_session.query(Digest).count() == 0
    assert db_session.query(DigestItem).count() == 0


async def test_rebuilding_the_same_period_does_not_duplicate(db_session, monkeypatch):
    Seams(monkeypatch)
    user = _ready(db_session)
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    first = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    first_id = first.id
    second = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert second is not None
    assert db_session.query(Digest).count() == 1
    assert db_session.query(DigestItem).count() == 2
    # Replaced, not appended to: the first digest and its items are gone, and
    # the two items that exist hang off the second one.
    assert db_session.query(DigestItem).filter(
        DigestItem.digest_id == first_id
    ).count() == 0
    assert second.id != first_id


# --------------------------------------------------------------------------
# the ceiling
# --------------------------------------------------------------------------


async def test_the_ceiling_finishes_the_user_in_flight_then_stops(db_session, monkeypatch):
    """Checked once before each user, never mid-user.

    That is what makes "finish the user in flight, then stop" fall out instead
    of needing a mechanism: once a user is started they always complete, so no
    digest can be half-scored.
    """
    _ceiling(monkeypatch, 1_000_000)
    # Ids, not creation order: the run walks users in a defined order and the
    # test has to know who was in flight when the money ran out.
    _ready(db_session, uid="u-aaa")
    second = _ready(db_session, uid="u-bbb")
    third = _ready(db_session, uid="u-ccc")
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))

    def spend_it_all(db, user, jobs):
        # What the real scoring pass does to the ledger, mid-user.
        _spend(db, uusd=2_000_000, commit=False)

    seams = Seams(monkeypatch, on_score=spend_it_all)
    report = await build_all(db_session, period_start=PERIOD, now=NOW)

    assert report.built == ["u-aaa"]
    assert report.skipped_over_ceiling == ["u-bbb", "u-ccc"]
    # The user in flight finished: scored, written about, and written down.
    assert seams.trace == ["score:u-aaa", "prose:u-aaa"]
    digests = db_session.query(Digest).all()
    assert len(digests) == 1 and digests[0].user_id == "u-aaa"
    built = digests[0]
    assert built.status == "full" and built.candidate_count == 1
    assert len(built.items) == 2
    assert all(i.tier and i.fit_score and i.reason for i in built.items)
    # Nobody is half-scored: the only rows that exist belong to the one digest.
    assert db_session.query(DigestItem).count() == 2
    assert {u for (u,) in db_session.query(Digest.user_id)} == {"u-aaa"}
    assert second.id not in report.built and third.id not in report.built


async def test_a_ceiling_already_reached_builds_for_nobody(db_session, monkeypatch):
    _ceiling(monkeypatch, 1_000_000)
    seams = Seams(monkeypatch)
    user = _ready(db_session, uid="u-zzz")
    _posting(db_session)
    _spend(db_session, uusd=1_000_000)
    report = await build_all(db_session, period_start=PERIOD, now=NOW)
    assert report.built == [] and report.skipped_over_ceiling == ["u-zzz"]
    assert seams.trace == [] and db_session.query(Digest).count() == 0


async def test_a_zero_ceiling_is_disabled_not_a_stop(db_session, monkeypatch):
    _ceiling(monkeypatch, 0)
    Seams(monkeypatch)
    user = _ready(db_session, uid="u-yyy")
    _posting(db_session)
    _spend(db_session, uusd=9_000_000_000)
    report = await build_all(db_session, period_start=PERIOD, now=NOW)
    assert report.built == ["u-yyy"] and report.skipped_over_ceiling == []


async def test_the_ceiling_reads_the_spend_window_not_all_time(db_session, monkeypatch):
    _ceiling(monkeypatch, 1_000_000)
    Seams(monkeypatch)
    _ready(db_session, uid="u-win")
    _posting(db_session)
    # Last week's money is last week's problem.
    _spend(db_session, uusd=5_000_000,
           window=usage.window_start(NOW) - timedelta(days=7))
    report = await build_all(db_session, period_start=PERIOD, now=NOW)
    assert report.built == ["u-win"]


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


async def test_build_all_sorts_every_user_into_the_report(db_session, monkeypatch):
    Seams(monkeypatch)
    built = _ready(db_session, uid="u-built")
    no_pref = _user(db_session, "u-nopref")
    _facts(db_session, no_pref)
    paused = _ready(db_session, uid="u-paused")
    db_session.query(JobPreference).filter(
        JobPreference.user_id == paused.id
    ).one().activity = "paused"
    churned = _ready(db_session, uid="u-churn")
    _prior(db_session, churned, period=PERIOD - timedelta(days=14))
    _prior(db_session, churned, period=PERIOD - timedelta(days=7))
    db_session.commit()
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))

    report = await build_all(db_session, period_start=PERIOD, now=NOW)
    assert isinstance(report, BuildReport)
    assert report.built == ["u-built"]
    assert report.skipped_no_preference == ["u-nopref"]
    assert sorted(report.skipped_not_engaged) == ["u-churn", "u-paused"]
    assert report.skipped_over_ceiling == []
    assert report.degraded == []


async def test_a_degraded_build_is_reported_as_degraded(db_session, monkeypatch):
    Seams(monkeypatch, score_error="llm_not_configured")
    _ready(db_session, uid="u-deg")
    _posting(db_session)
    report = await build_all(db_session, period_start=PERIOD, now=NOW)
    assert report.built == ["u-deg"] and report.degraded == ["u-deg"]


async def test_one_users_failure_does_not_stop_the_run(db_session, monkeypatch):
    """A crash is per-user, like refresh_profiles: everyone else still gets built."""
    boom = {"u-fail"}

    def maybe_explode(db, user, jobs):
        if user.id in boom:
            raise RuntimeError("provider exploded")

    Seams(monkeypatch, on_score=maybe_explode)
    _ready(db_session, uid="u-fail")
    _ready(db_session, uid="u-ok")
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    report = await build_all(db_session, period_start=PERIOD, now=NOW)
    assert report.built == ["u-ok"]
    assert {u for (u,) in db_session.query(Digest.user_id)} == {"u-ok"}


async def test_a_database_fault_before_scoring_does_not_cost_everyone_else(
    db_session, monkeypatch
):
    """The per-user catch has to cover the per-user reads and the facts rebuild.

    `build_facts` documents that a database failure while writing still
    propagates, and it runs before the digest's own writes. A fault there used
    to leave the session needing a rollback nobody performed -- so the NEXT
    person in the loop died on `PendingRollbackError` before their first query,
    the exception escaped `build_all` entirely and `main` exited 1. One
    person's database fault cost everyone else their week, which is the exact
    opposite of what the per-user catch is for.

    The fault is a real one, not a raised sentinel: a failed flush, which is
    what actually leaves a Session refusing every later statement.
    """
    Seams(monkeypatch)
    # No facts, so the rebuild runs for this one and not for the other.
    broken = _user(db_session, "u-db-fault")
    _pref(db_session, broken)
    _ready(db_session, uid="u-db-ok")
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))

    async def fault(db, user):
        db.add(Digest(
            user_id="nobody-at-all", period_start=PERIOD, built_at=PERIOD,
            model="m", input_hash="z" * 64, status="full", candidate_count=0,
        ))
        db.flush()  # IntegrityError: the Session now refuses everything
        return build_digests.FactsBuildResult()

    monkeypatch.setattr(build_digests, "build_facts", fault)

    report = await build_all(db_session, period_start=PERIOD, now=NOW)
    assert report.built == ["u-db-ok"]
    assert {u for (u,) in db_session.query(Digest.user_id)} == {"u-db-ok"}


# --------------------------------------------------------------------------
# metering
# --------------------------------------------------------------------------


class StubResponse:
    def __init__(self, payload, status_code):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class StubClient:
    def __init__(self, status_code=500, payload=None):
        self.status_code = status_code
        self.payload = payload or {"error": {"message": "nope"}}
        self.calls = 0

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        if "/decisions" not in url:
            # The prose endpoint. Not what this stub is standing in for.
            return StubResponse({"error": {"message": "nope"}}, 500)
        self.calls += 1
        return StubResponse(self.payload, self.status_code)


LEVELS = ["Strong fit", "Good fit", "Worth a look", "Not a fit"]


def _decisions_payload():
    """One recorded /v1/decisions answer, in the shape score.py parses."""
    return {
        "answers": [
            {"name": "fit", "type": "score", "score": 0.9, "probabilities": [
                {"label": label, "probability": 0.7 if label == "Strong fit" else 0.1}
                for label in LEVELS
            ]},
            *[{"name": name, "type": "predicate", "probability": 0.8}
              for name in ("requirements_met", "seniority_fit", "location_fit",
                           "direction_fit")],
        ],
        "usage": {"input_tokens": 50_000},
    }


async def test_the_build_never_consumes_the_users_edit_allowance(
    db_session, monkeypatch, openai_enabled
):
    """system_guard, not preflight, on every paid call the build makes.

    Three things, which are one promise: a digest is not something they asked
    for, so it must not cost them one.

    1. It must not *ask* their allowance for permission. `enforce_usage` is the
       only thing that reads it to refuse, and the one function a `preflight`
       anywhere in this path would reach -- so it is replaced with a landmine.
    2. It must not *spend* it. Real provider spend goes through the real
       scoring path here (50k input tokens, priced by the real meter), and
       every number the editor shows them is identical either side of it.
    3. It must still be on the books. The money lands under subject kind
       'system' with their id, which is what `window_totals` -- and so the
       global ceiling -- sums.
    """
    def landmine(*a, **kw):
        raise AssertionError("the build must not enforce the person's allowance")

    monkeypatch.setattr(usage, "enforce_usage", landmine)
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    # One stub for both calls, routed by URL: `score.httpx` and
    # `reasons.httpx` are the same module object, so patching one patches the
    # other. The prose half answers 500, so this stays one real billed call
    # and the digest is still complete enough to prove the point.
    scoring = StubClient(status_code=200, payload=_decisions_payload())
    monkeypatch.setattr("app.services.jobs.score.httpx.AsyncClient", scoring)

    user = _ready(db_session, uid="u-meter")
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    before = usage.balance(db_session, user, None)
    edits_before = usage.approx_edits_left(db_session, user, None, before)

    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert scoring.calls == 1 and built is not None and built.status == "full"

    # 1 + 2: nothing the person can feel has moved.
    after = usage.balance(db_session, user, None)
    assert (after.used_uusd, after.remaining_uusd, after.turns_used) == (
        before.used_uusd, before.remaining_uusd, before.turns_used
    )
    assert usage.approx_edits_left(db_session, user, None, after) == edits_before
    assert db_session.query(UsageCounter).filter(
        UsageCounter.subject_kind == "user", UsageCounter.subject_id == user.id
    ).count() == 0

    # 3: and it is on the books, attributed to them, where the ceiling reads it.
    system = db_session.query(UsageCounter).filter(
        UsageCounter.subject_kind == "system", UsageCounter.subject_id == user.id
    ).one()
    assert system.uusd > 0 and system.turns == 0
    assert usage.window_totals(
        db_session, window_start=usage.window_start(NOW)
    ) == system.uusd


async def test_a_build_leaves_no_reconciliation_gap(
    db_session, monkeypatch, openai_enabled
):
    """Both halves of every billed round, or the admin overview grows an alarm
    nobody can read.

    `usage.reconcile` compares the counters against the LlmCall rows per
    subject per window. Both digest purposes moved the counter from the day
    they shipped while `llm_calls.purpose` could not admit them, so every
    window a digest ran in reported a gap that no action could clear. The
    telemetry row is written beside the counter now (score.record_round), and
    the vocabulary admits the purposes (models.LLM_CALL_PURPOSES).
    """
    scoring = StubClient(status_code=200, payload=_decisions_payload())
    monkeypatch.setattr("app.services.jobs.score.httpx.AsyncClient", scoring)

    user = _ready(db_session, uid="u-recon")
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    built = await build_for_user(db_session, user=user, period_start=PERIOD, now=NOW)
    assert built is not None and scoring.calls == 1

    rows = db_session.query(LlmCall).all()
    assert [r.purpose for r in rows] == ["match"]
    assert rows[0].user_id == user.id and rows[0].input_tokens == 50_000
    assert usage.reconcile(db_session, window_start=usage.window_start(NOW)) == []


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def test_main_builds_only_the_named_user(db_session, monkeypatch):
    Seams(monkeypatch)
    mine = _ready(db_session, uid="u-cli-mine")
    _ready(db_session, uid="u-cli-other")
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    assert main(["--user-id", mine.id, "--period-start", PERIOD_ARG]) == 0
    db_session.expire_all()
    assert {u for (u,) in db_session.query(Digest.user_id)} == {"u-cli-mine"}


def test_main_reports_an_unparseable_period_start(db_session, monkeypatch):
    """A message and a 1, like every other failure this CLI has."""
    Seams(monkeypatch)
    assert main(["--period-start", "not-a-date"]) == 1


def test_main_reports_an_unknown_user_id(db_session, monkeypatch):
    Seams(monkeypatch)
    assert main(["--user-id", "nobody"]) == 1


def test_main_all_users_bypasses_the_gate(db_session, monkeypatch):
    Seams(monkeypatch)
    user = _ready(db_session, uid="u-cli-churn")
    _prior(db_session, user, period=PERIOD - timedelta(days=14))
    _prior(db_session, user, period=PERIOD - timedelta(days=7))
    _posting(db_session, first_seen=PERIOD + timedelta(hours=1))
    assert main(["--period-start", PERIOD_ARG]) == 0
    db_session.expire_all()
    assert db_session.query(Digest).filter(Digest.period_start == PERIOD).count() == 0
    assert main(["--period-start", PERIOD_ARG, "--all-users"]) == 0
    db_session.expire_all()
    assert db_session.query(Digest).filter(Digest.period_start == PERIOD).count() == 1
