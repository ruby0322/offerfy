"""Scoring one resume against one posting on /v1/decisions.

Every payload here is recorded in the shape layout.py already parses; no test
reaches a live provider.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models import User
from app.services.jobs import score as score_mod
from app.services.jobs.score import FIT_RUBRIC, PREDICATES, score_candidates

pytestmark = pytest.mark.anyio

LEVELS = ["Strong fit", "Good fit", "Worth a look", "Not a fit"]


def _facts():
    payload = {
        "headline": "Backend engineer",
        "years_experience": 6,
        "locations": ["Taipei"],
        "skills": ["Python", "Postgres"],
        "roles": [{"employer": "Acme", "title": "Engineer", "start": "2019-01", "end": "Present",
                   "highlights": ["Ran the billing service"]}],
    }
    return SimpleNamespace(facts_json=json.dumps(payload))


def _job(job_id, **kwargs):
    base = dict(
        id=job_id, title=f"Posting-{job_id}-title", company="Initech", location="Taipei",
        description_text="About us. We are great.\nRequirements\n- Python\n- Postgres",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _answers(top="Strong fit", predicate=0.8, probs=None):
    probs = probs or {top: 0.7, **{l: 0.1 for l in LEVELS if l != top}}
    return {
        "answers": [
            {"name": FIT_RUBRIC, "type": "score", "score": 0.9,
             "probabilities": [{"label": l, "probability": probs[l]} for l in LEVELS]},
            *[{"name": p, "type": "predicate", "probability": predicate} for p in PREDICATES],
        ],
        "usage": {"input_tokens": 1200},
    }


class StubResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class StubDecisions:
    """Stands in for httpx.AsyncClient. `respond` maps a request body to a payload."""

    def __init__(self, respond, status_code=200, delay=0.0):
        self.respond = respond
        self.status_code = status_code
        self.delay = delay
        self.calls: list[dict] = []
        self.in_flight = 0
        self.peak = 0

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "body": json})
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return StubResponse(self.respond(json), self.status_code)
        finally:
            self.in_flight -= 1


def _install(monkeypatch, stub):
    monkeypatch.setattr("app.services.jobs.score.httpx.AsyncClient", stub)
    return stub


def _user(db_session):
    user = User(google_sub="sub-score", email="score@x.test", locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def _text_of(body):
    return json.dumps(body["input"])


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_one_call_per_pair_not_one_call_for_many(openai_enabled, monkeypatch, db_session):
    stub = _install(monkeypatch, StubDecisions(lambda body: _answers()))
    jobs = [_job("a"), _job("b"), _job("c")]
    run = await score_candidates(db_session, user=_user(db_session), facts=_facts(), jobs=jobs)
    assert len(stub.calls) == 3
    for call, job in zip(sorted(stub.calls, key=lambda c: _text_of(c["body"])), jobs):
        text = _text_of(call["body"])
        assert f"Posting-{job.id}-title" in text
        # exactly one posting per request
        assert sum(f"Posting-{j.id}-title" in text for j in jobs) == 1
    assert stub.calls[0]["url"] == "https://api.openai.com/v1/decisions"
    assert stub.calls[0]["body"]["model"] == "gpt-6-luna"
    names = [q["name"] for q in stub.calls[0]["body"]["questions"]]
    assert names == [FIT_RUBRIC, *PREDICATES]
    assert run.error is None and {s.job_id for s in run.scored} == {"a", "b", "c"}


async def test_the_four_levels_map_to_a_tier_and_a_score(openai_enabled, monkeypatch, db_session):
    by_title = {
        "Posting-a-title": _answers("Strong fit", probs={"Strong fit": 1.0, "Good fit": 0, "Worth a look": 0, "Not a fit": 0}),
        "Posting-b-title": _answers("Good fit"),
        "Posting-c-title": _answers("Worth a look"),
        "Posting-d-title": _answers("Not a fit"),
    }
    _install(monkeypatch, StubDecisions(
        lambda body: next(v for k, v in by_title.items() if k in _text_of(body))))
    jobs = [_job(i) for i in "abcd"]
    run = await score_candidates(db_session, user=_user(db_session), facts=_facts(), jobs=jobs)
    got = {s.job_id: s for s in run.scored}
    assert [got[i].tier for i in "abcd"] == LEVELS
    assert got["a"].fit_score == pytest.approx(1.0)
    assert got["a"].fit_score > got["b"].fit_score > got["c"].fit_score > got["d"].fit_score
    assert all(0.0 <= s.fit_score <= 1.0 for s in run.scored)
    assert set(got["b"].components) == set(PREDICATES)
    assert got["b"].components["seniority_fit"] == pytest.approx(0.8)


async def test_a_refusal_drops_that_candidate_and_never_scores_it_zero(
    openai_enabled, monkeypatch, db_session
):
    def respond(body):
        if "Posting-b-title" in _text_of(body):
            return {"answers": [{"name": FIT_RUBRIC, "type": "refusal", "reason": "declined"}],
                    "usage": {"input_tokens": 900}}
        return _answers()

    _install(monkeypatch, StubDecisions(respond))
    run = await score_candidates(
        db_session, user=_user(db_session), facts=_facts(), jobs=[_job("a"), _job("b"), _job("c")]
    )
    assert [s.job_id for s in run.scored] == ["a", "c"]
    assert run.refused == ["b"]
    assert run.error is None
    assert all(s.fit_score > 0 for s in run.scored)


async def test_a_refused_component_also_drops_the_pair(openai_enabled, monkeypatch, db_session):
    payload = _answers()
    payload["answers"][2] = {"name": PREDICATES[1], "type": "refusal"}
    _install(monkeypatch, StubDecisions(lambda body: payload))
    run = await score_candidates(db_session, user=_user(db_session), facts=_facts(), jobs=[_job("a")])
    assert run.scored == [] and run.refused == ["a"] and run.error is None


async def test_a_missing_probabilities_list_is_an_error_not_a_zero(
    openai_enabled, monkeypatch, db_session
):
    payload = _answers()
    del payload["answers"][0]["probabilities"]
    _install(monkeypatch, StubDecisions(lambda body: payload))
    run = await score_candidates(db_session, user=_user(db_session), facts=_facts(), jobs=[_job("a")])
    assert run.error and run.scored == [] and run.refused == []


async def test_a_job_id_absent_from_the_candidate_set_is_dropped(
    openai_enabled, monkeypatch, db_session
):
    # An id-less row or a repeated id cannot be attributed, so it is neither
    # sent (money) nor scored.
    stub = _install(monkeypatch, StubDecisions(lambda body: _answers()))
    jobs = [_job("a"), _job(None), _job(""), _job("a")]
    run = await score_candidates(db_session, user=_user(db_session), facts=_facts(), jobs=jobs)
    assert [s.job_id for s in run.scored] == ["a"]
    assert len(stub.calls) == 1


async def test_a_provider_4xx_sets_error_and_scores_nothing(openai_enabled, monkeypatch, db_session):
    _install(monkeypatch, StubDecisions(
        lambda body: {"error": {"message": "Unknown endpoint /v1/decisions"}}, status_code=404))
    run = await score_candidates(
        db_session, user=_user(db_session), facts=_facts(), jobs=[_job("a"), _job("b")]
    )
    assert run.error == "Unknown endpoint /v1/decisions" and run.scored == []


async def test_no_key_is_an_error_not_a_crash(monkeypatch, db_session):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    stub = _install(monkeypatch, StubDecisions(lambda body: _answers()))
    run = await score_candidates(db_session, user=_user(db_session), facts=_facts(), jobs=[_job("a")])
    assert run.error and run.scored == [] and stub.calls == []


async def test_usage_records_input_tokens_only(openai_enabled, monkeypatch, db_session):
    recorded = []
    real = score_mod.metered

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def spy(guard):
        async with real(guard) as meter:
            original = meter.record
            meter.record = lambda event: (recorded.append(event), original(event))[1]
            assert guard.purpose == "match" and guard.exempt_reason
            yield meter

    monkeypatch.setattr(score_mod, "metered", spy)
    _install(monkeypatch, StubDecisions(lambda body: _answers()))
    run = await score_candidates(
        db_session, user=_user(db_session), facts=_facts(), jobs=[_job("a"), _job("b")]
    )
    assert len(recorded) == 2
    for event in recorded:
        assert event["purpose"] == "match" and event["model"] == "gpt-6-luna"
        assert event["input_tokens"] == 1200
        for field in ("cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens"):
            assert event[field] is None
    assert run.usage["input_tokens"] == 2400
    assert run.usage["output_tokens"] is None and run.usage["cached_tokens"] is None
    assert run.model == "gpt-6-luna"


async def test_the_posting_text_is_trimmed_to_the_spec_budget(openai_enabled, monkeypatch, db_session):
    stub = _install(monkeypatch, StubDecisions(lambda body: _answers()))
    long = "Requirements\n" + ("Python. " * 1000) + "TAIL-MARKER"
    job = _job("a", description_text="Intro blurb.\n" + long)
    await score_candidates(db_session, user=_user(db_session), facts=_facts(), jobs=[job])
    text = _text_of(stub.calls[0]["body"])
    assert "Posting-a-title" in text and "Initech" in text and "Taipei" in text
    assert "TAIL-MARKER" not in text
    assert "Intro blurb" not in text  # started at the requirements section
    assert score_mod.REQUIREMENTS_CHARS == 1500
    posting = stub.calls[0]["body"]["input"][0]["content"][-1]["text"]
    assert len(posting) < 1500 + 400


async def test_a_posting_with_no_requirements_heading_uses_its_opening(
    openai_enabled, monkeypatch, db_session
):
    stub = _install(monkeypatch, StubDecisions(lambda body: _answers()))
    await score_candidates(
        db_session, user=_user(db_session), facts=_facts(),
        jobs=[_job("a", description_text="Build services in Go. " * 200)],
    )
    assert "Build services in Go." in _text_of(stub.calls[0]["body"])


async def test_concurrency_is_bounded(openai_enabled, monkeypatch, db_session):
    stub = _install(monkeypatch, StubDecisions(lambda body: _answers(), delay=0.01))
    jobs = [_job(str(i)) for i in range(10)]
    run = await score_candidates(
        db_session, user=_user(db_session), facts=_facts(), jobs=jobs, concurrency=3
    )
    assert len(run.scored) == 10
    assert stub.peak == 3
