"""score_cache: a score is bought once and remembered.

score_candidates is stubbed throughout; no test reaches a provider. The stub
records what it was asked to buy, so "was not bought again" is an assertion on
calls, not on a side effect.
"""

import itertools
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import get_session_factory
from app.models import Job, JobFacets, JobScore, Resume, ResumeFacts, User
from app.services.jobs import score_cache
from app.services.jobs.score import Scored, ScoreRun

pytestmark = pytest.mark.anyio

_seq = itertools.count(1)
COMPONENTS = {
    "requirements_met": 0.9,
    "seniority_fit": 0.8,
    "location_fit": 0.7,
    "direction_fit": 0.6,
}


def _user(db):
    n = next(_seq)
    user = User(google_sub=f"sub-cache-{n}", email=f"c{n}@example.com", locale="en", name="Mei")
    db.add(user)
    db.commit()
    return user


def _facts(db, user, input_hash="h" * 64):
    resume = Resume(user_id=user.id, title="CV", typst_source="= CV", source="create", locale="en", kind="master")
    db.add(resume)
    db.commit()
    row = ResumeFacts(
        user_id=user.id, source_resume_id=resume.id, source_revision_id="rev1",
        input_hash=input_hash, facts_json=json.dumps({"roles": [], "locations": []}),
        model="gpt-6-luna",
    )
    db.add(row)
    db.commit()
    return row


def _job(db, description_hash="d" * 64):
    n = next(_seq)
    job = Job(
        source="greenhouse", source_id=f"cache-{n}", company="Acme", title=f"Engineer {n}",
        apply_url="https://example.com/apply", source_url="https://example.com/job",
    )
    db.add(job)
    db.commit()
    db.add(JobFacets(
        job_id=job.id, seniority="mid", function="backend", description_hash=description_hash,
    ))
    db.commit()
    return job


class Provider:
    """Stands in for score_candidates and remembers every pool it was sent."""

    def __init__(self, *, refuse=(), error=None, model="decisions-model"):
        self.refuse = set(refuse)
        self.error = error
        self.model = model
        self.calls: list[list[str]] = []

    async def __call__(self, db, *, user, facts, jobs, concurrency=4):
        ids = [str(j.id) for j in jobs]
        self.calls.append(ids)
        run = ScoreRun(model=self.model, usage={"input_tokens": 100 * len(ids)})
        if self.error:
            run.error = self.error
            return run
        for job_id in ids:
            if job_id in self.refuse:
                run.refused.append(job_id)
            else:
                run.scored.append(Scored(job_id, "strong", 0.8, dict(COMPONENTS)))
        return run

    @property
    def bought(self):
        return sum(len(c) for c in self.calls)


@pytest.fixture
def provider(monkeypatch):
    stub = Provider()
    monkeypatch.setattr(score_cache, "score_candidates", stub)
    return stub


def _rows(db, user):
    db.expire_all()
    return db.scalars(select(JobScore).where(JobScore.user_id == user.id)).all()


async def test_a_fresh_cached_row_is_not_bought_again(db_session, monkeypatch):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    first = Provider()
    monkeypatch.setattr(score_cache, "score_candidates", first)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)

    async def boom(*a, **k):
        raise AssertionError("a fresh row must not reach the provider")

    monkeypatch.setattr(score_cache, "score_candidates", boom)
    run = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert [s.job_id for s in run.scored] == [job.id]
    assert run.scored[0].tier == "strong"
    assert run.scored[0].fit_score == 0.8
    assert run.scored[0].components == COMPONENTS
    assert run.error is None
    assert score_cache.stale_pairs(db_session, user=user, facts=facts, jobs=[job]) == []


async def test_a_miss_is_bought_and_written_back(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session, description_hash="e" * 64)
    run = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert provider.calls == [[job.id]]
    assert [s.job_id for s in run.scored] == [job.id]
    assert run.usage == {"input_tokens": 100}
    assert run.model == "decisions-model"
    (row,) = _rows(db_session, user)
    assert (row.job_id, row.outcome, row.tier, row.fit_score) == (job.id, "scored", "strong", 0.8)
    assert row.facts_input_hash == facts.input_hash
    assert row.description_hash == "e" * 64
    assert row.model == "decisions-model"
    assert row.requirements_met == 0.9 and row.direction_fit == 0.6
    assert row.scored_at is not None


async def test_a_refusal_is_recorded_and_not_retried(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    provider.refuse = {job.id}
    first = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert first.refused == [job.id] and first.scored == []
    (row,) = _rows(db_session, user)
    assert row.outcome == "refused" and row.tier is None and row.fit_score is None

    second = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert provider.calls == [[job.id]]  # pass two made no provider call
    assert second.refused == [job.id] and second.scored == [] and second.error is None


async def test_a_changed_resume_hash_rescores_that_pair(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    facts.input_hash = "z" * 64  # the stored facts row was rebuilt
    db_session.commit()
    assert score_cache.stale_pairs(db_session, user=user, facts=facts, jobs=[job]) == [job]
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert provider.calls == [[job.id], [job.id]]
    (row,) = _rows(db_session, user)
    assert row.facts_input_hash == "z" * 64


async def test_the_pass_after_a_rescore_stops_buying(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    facts.input_hash = "z" * 64
    db_session.commit()
    for _ in range(3):
        await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert provider.bought == 2  # the original purchase and exactly one rescore


async def test_a_posting_rewritten_during_the_provider_call_is_rescored(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session, description_hash="old" + "0" * 61)
    inner = Provider()

    async def rewriting(db, **kw):
        run = await inner(db, **kw)
        db.get(JobFacets, job.id).description_hash = "new" + "0" * 61
        db.commit()  # enrich_jobs lands while the provider was thinking
        return run

    import app.services.jobs.score_cache as sc
    original = sc.score_candidates
    sc.score_candidates = rewriting
    try:
        await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    finally:
        sc.score_candidates = original
    (row,) = _rows(db_session, user)
    assert row.description_hash == "old" + "0" * 61
    sc.score_candidates = inner
    try:
        await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    finally:
        sc.score_candidates = original
    assert inner.bought == 2


async def test_cached_scores_leaves_the_commit_to_the_caller(db_session, provider, monkeypatch):
    # Spied rather than rolled back: pysqlite's savepoint handling can settle
    # rows on its own, so a rollback assertion would test the driver, not us.
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    commits = []
    monkeypatch.setattr(db_session, "commit", lambda: commits.append(1))
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert commits == []
    assert len(_rows(db_session, user)) == 1  # written and flushed, awaiting the caller


async def test_a_fault_with_nothing_cached_keeps_the_model_that_failed(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    provider.error = "Decisions request failed"
    run = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert run.error and run.model == "decisions-model"


async def test_a_refused_rows_model_does_not_make_the_pool_mixed(db_session, monkeypatch):
    user = _user(db_session)
    facts = _facts(db_session, user)
    declined, good = _job(db_session), _job(db_session)
    monkeypatch.setattr(score_cache, "score_candidates", Provider(refuse={declined.id}, model="a"))
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[declined], cap=10)
    monkeypatch.setattr(score_cache, "score_candidates", Provider(model="b"))
    run = await score_cache.cached_scores(
        db_session, user=user, facts=facts, jobs=[declined, good], cap=10
    )
    assert run.model == "b"


def test_record_prose_with_nothing_to_record_raises_and_keeps_the_prose(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m",
        scored=Scored(job.id, "strong", 0.8, dict(COMPONENTS)),
    )
    score_cache.record_prose(db_session, user=user, job=job, reason="Kept.", model="p")
    with pytest.raises(ValueError):
        score_cache.record_prose(db_session, user=user, job=job)
    with pytest.raises(ValueError):
        score_cache.record_prose(db_session, user=user, job=job, reason="x", rejected=True)
    (row,) = _rows(db_session, user)
    assert (row.reason, row.prose_model, row.outcome) == ("Kept.", "p", "scored")


async def test_a_rewritten_posting_rescores_that_pair(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    db_session.get(JobFacets, job.id).description_hash = "r" * 64
    db_session.commit()
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert provider.calls == [[job.id], [job.id]]
    (row,) = _rows(db_session, user)
    assert row.description_hash == "r" * 64


async def test_a_refused_pair_is_asked_again_when_the_posting_changes(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    provider.refuse = {job.id}
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    provider.refuse = set()
    db_session.get(JobFacets, job.id).description_hash = "r" * 64
    db_session.commit()
    run = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert [s.job_id for s in run.scored] == [job.id] and run.refused == []
    (row,) = _rows(db_session, user)
    assert row.outcome == "scored" and row.tier == "strong"


async def test_staleness_is_judged_against_the_stored_row_not_the_live_resume(
    db_session, provider, monkeypatch
):
    """The loop this module exists to avoid. The live resume has moved on (its
    recomputed hash differs from the stored row's), and the facts row has not
    been rebuilt yet. A pair scored against that row is fresh, however far the
    resume has moved; recomputing would rescore it on every pass."""
    from app.services import resume_facts

    def moved(*a, **k):
        raise AssertionError("staleness must not recompute the live resume's hash")

    monkeypatch.setattr(resume_facts, "input_hash", moved)
    monkeypatch.setattr(resume_facts, "facts_are_stale", moved)
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    for _ in range(3):
        await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert provider.bought == 1


async def test_a_refusal_does_not_set_error(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    good, declined = _job(db_session), _job(db_session)
    provider.refuse = {declined.id}
    run = await score_cache.cached_scores(
        db_session, user=user, facts=facts, jobs=[good, declined], cap=10
    )
    assert run.refused == [declined.id]
    assert [s.job_id for s in run.scored] == [good.id]
    assert run.error is None


async def test_a_provider_fault_sets_error_and_still_returns_cached_rows(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    cached, fresh_miss = _job(db_session), _job(db_session)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[cached], cap=10)
    provider.error = "Decisions request failed"
    run = await score_cache.cached_scores(
        db_session, user=user, facts=facts, jobs=[cached, fresh_miss], cap=10
    )
    assert run.error == "Decisions request failed"
    assert [s.job_id for s in run.scored] == [cached.id]
    assert [r.job_id for r in _rows(db_session, user)] == [cached.id]  # nothing half-written


async def test_cap_bounds_purchases_not_the_returned_pool(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    jobs = [_job(db_session) for _ in range(40)]
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=jobs[:10], cap=100)
    provider.calls.clear()
    run = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=jobs, cap=5)
    assert provider.bought == 5
    assert len(run.scored) == 15
    assert provider.calls == [[j.id for j in jobs[10:15]]]  # the caller's order decides
    assert len(score_cache.stale_pairs(db_session, user=user, facts=facts, jobs=jobs)) == 25


async def test_a_second_model_reads_as_mixed(db_session, monkeypatch):
    user = _user(db_session)
    facts = _facts(db_session, user)
    old, new = _job(db_session), _job(db_session)
    monkeypatch.setattr(score_cache, "score_candidates", Provider(model="model-a"))
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[old], cap=10)
    monkeypatch.setattr(score_cache, "score_candidates", Provider(model="model-b"))
    run = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[old, new], cap=10)
    assert run.model == "mixed"


def test_a_concurrent_double_write_leaves_one_row(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    item = Scored(job.id, "strong", 0.8, dict(COMPONENTS))
    other = get_session_factory()()
    try:
        # The other run committed first. This session's lookup has not seen it
        # yet, which is exactly the window the race lives in.
        score_cache.write_back(other, user=user, facts=facts, job=job, model="m", scored=item)
        other.commit()
        real_find = score_cache._find
        looks = []

        def blind_once(db, user_id, job_id):
            looks.append(1)
            return None if len(looks) == 1 else real_find(db, user_id, job_id)

        score_cache._find, original = blind_once, score_cache._find
        try:
            row = score_cache.write_back(
                db_session, user=user, facts=facts, job=job, model="m", scored=item
            )
        finally:
            score_cache._find = original
        db_session.commit()
        assert row.fit_score == 0.8
    finally:
        other.close()
    assert len(_rows(db_session, user)) == 1


def test_write_back_twice_is_an_update(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m",
        scored=Scored(job.id, "weak", 0.2, dict(COMPONENTS)),
    )
    score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m", refused=True
    )
    db_session.commit()
    (row,) = _rows(db_session, user)
    assert row.outcome == "refused" and row.tier is None and row.requirements_met is None


def test_a_rescore_moves_scored_at_and_leaves_created_at_alone(db_session):
    # The pair is new to the reader once. scored_at dates the judgement and
    # moves; created_at dates the pair and must not, or a resume edit
    # re-announces the whole match set as arrivals.
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    first = score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m",
        scored=Scored(job.id, "strong", 0.8, dict(COMPONENTS)),
    )
    db_session.commit()
    born, judged = first.created_at, first.scored_at
    assert born == judged, "a pair's first judgement is its creation"

    facts.input_hash = "z" * 64
    db_session.commit()
    again = score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m",
        scored=Scored(job.id, "weak", 0.2, dict(COMPONENTS)),
    )
    db_session.commit()
    assert again.created_at == born
    assert again.scored_at > judged
    # And a refusal written over a score is still the same pair.
    score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m", refused=True
    )
    db_session.commit()
    (row,) = _rows(db_session, user)
    assert row.created_at == born


async def test_a_rescore_through_the_cache_keeps_the_pairs_creation_date(
    db_session, provider
):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    db_session.commit()
    (row,) = _rows(db_session, user)
    born = row.created_at
    facts.input_hash = "z" * 64
    db_session.commit()
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    db_session.commit()
    (row,) = _rows(db_session, user)
    assert row.created_at == born and row.scored_at >= born


def test_a_long_model_id_is_truncated_rather_than_raising(db_session):
    """JobScore.model and .prose_model are String(64) and the id is config.

    Digest.model is deliberately stored as model[:64]; these were not, so a
    configured id over 64 characters would raise on every write, for every
    user, forever -- after the scoring had been paid for.
    """
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    long_id = "a-very-long-provider-model-identifier-" + "x" * 80
    score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model=long_id,
        scored=Scored(job.id, "strong", 0.8, dict(COMPONENTS)),
    )
    score_cache.record_prose(
        db_session, user=user, job=job, reason="Because reasons.", model=long_id
    )
    db_session.commit()
    (row,) = _rows(db_session, user)
    assert row.model == long_id[:64] and len(row.model) == 64
    assert row.prose_model == long_id[:64]
    # A rejection records the model too, and it is the same length rule.
    score_cache.record_prose(db_session, user=user, job=job, rejected=True, model=long_id)
    db_session.commit()
    assert db_session.query(JobScore).one().prose_model == long_id[:64]


def test_write_back_wants_exactly_one_answer(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    with pytest.raises(ValueError):
        score_cache.write_back(db_session, user=user, facts=facts, job=job, model="m")


def test_record_prose_keeps_the_score_and_stores_the_sentence(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    row = score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m",
        scored=Scored(job.id, "strong", 0.8, dict(COMPONENTS)),
    )
    stamp = row.scored_at
    out = score_cache.record_prose(
        db_session, user=user, job=job, reason="You ran billing.", gap="No Kafka.", model="prose-m"
    )
    db_session.commit()
    assert (out.outcome, out.reason, out.gap, out.prose_model) == (
        "scored", "You ran billing.", "No Kafka.", "prose-m",
    )
    assert out.tier == "strong" and out.fit_score == 0.8 and out.scored_at.replace(tzinfo=None) == stamp.replace(tzinfo=None)


def test_record_prose_rejected_is_remembered_without_a_sentence(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    score_cache.write_back(
        db_session, user=user, facts=facts, job=job, model="m",
        scored=Scored(job.id, "strong", 0.8, dict(COMPONENTS)),
    )
    out = score_cache.record_prose(db_session, user=user, job=job, model="prose-m", rejected=True)
    assert out.outcome == "prose_rejected" and out.reason is None and out.gap is None
    assert out.tier == "strong"


async def test_a_prose_rejected_row_is_still_a_fresh_score(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    score_cache.record_prose(db_session, user=user, job=job, rejected=True)
    db_session.commit()
    run = await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    assert provider.bought == 1 and [s.job_id for s in run.scored] == [job.id]


async def test_a_rescore_discards_prose_about_the_old_score(db_session, provider):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    score_cache.record_prose(db_session, user=user, job=job, reason="Old.", gap="Old.", model="p")
    db_session.commit()
    db_session.get(JobFacets, job.id).description_hash = "r" * 64
    db_session.commit()
    await score_cache.cached_scores(db_session, user=user, facts=facts, jobs=[job], cap=10)
    (row,) = _rows(db_session, user)
    assert (row.outcome, row.reason, row.gap, row.prose_model) == ("scored", None, None, None)


def test_record_prose_needs_a_score_to_be_about(db_session):
    user = _user(db_session)
    facts = _facts(db_session, user)
    job = _job(db_session)
    with pytest.raises(LookupError):
        score_cache.record_prose(db_session, user=user, job=job, reason="x")
    score_cache.write_back(db_session, user=user, facts=facts, job=job, model="m", refused=True)
    with pytest.raises(ValueError):
        score_cache.record_prose(db_session, user=user, job=job, reason="x")
