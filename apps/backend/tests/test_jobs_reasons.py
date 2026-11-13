"""Match prose and the validator that stops it claiming what the resume lacks.

`validate` is pure, so most tests need no provider. The ones that do record a
Responses-API payload and monkeypatch httpx; none reaches a live provider.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.models import User
from app.services.jobs import reasons as reasons_mod
from app.services.jobs.reasons import (
    GAP_CHARS,
    REASON_CHARS,
    ProseItem,
    check,
    validate,
    write_reasons,
)
from app.services.jobs.score import Scored

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


FACTS = SimpleNamespace(
    facts_json=json.dumps(
        {
            "headline": "Backend engineer",
            "years_experience": 6,
            "skills": ["Python", "Postgres"],
            "roles": [{"employer": "Acme", "title": "Engineer", "start": "2019-01", "end": "Present",
                       "highlights": []}],
        }
    ),
    skills_text="|python|postgres|",
    search_text="backend engineer acme engineer python postgres",
)


def _item(job_id="a", reason="Your Python work fits their stack.", gap=None, gap_missing=None,
          resume=("Python",), posting=("Python services",)):
    return ProseItem(
        job_id=job_id, reason=reason, gap=gap, gap_missing=gap_missing,
        resume_cites=list(resume), posting_cites=list(posting),
    )


def test_an_unknown_job_id_is_dropped():
    kept, dropped = validate([_item("zzz")], facts=FACTS, scored_ids={"a"})
    assert kept == [] and dropped == [("zzz", "unknown_job")]


def test_a_reason_citing_an_absent_skill_loses_its_sentence_not_its_item():
    # The Prose record is dropped, so no sentence attaches; the digest item
    # (built later from Scored + optional Prose) keeps its tier and score.
    item = _item(reason="Your Rust experience is a direct match.", resume=("Rust",))
    kept, dropped = validate([item], facts=FACTS, scored_ids={"a"})
    assert kept == [] and dropped == [("a", "uncited")]


def test_a_reason_citing_a_real_skill_survives():
    kept, dropped = validate([_item()], facts=FACTS, scored_ids={"a"})
    assert dropped == [] and [(p.job_id, p.gap) for p in kept] == [("a", None)]
    assert kept[0].reason == "Your Python work fits their stack."


def test_a_cite_must_appear_in_the_sentence_it_vouches_for():
    item = _item(reason="A strong match for this team.", resume=("Python",))
    assert validate([item], facts=FACTS, scored_ids={"a"})[1] == [("a", "uncited")]


def test_a_cite_is_a_word_not_a_fragment():
    # "java" must not be vouched for by "javascript".
    facts = SimpleNamespace(skills_text="|javascript|", search_text="javascript developer")
    item = _item(reason="Your Java background fits.", resume=("Java",))
    assert validate([item], facts=facts, scored_ids={"a"})[1] == [("a", "uncited")]


def test_a_reason_citing_a_real_employer_survives():
    item = _item(reason="Your years at Acme map onto this role.", resume=("Acme",))
    kept, dropped = validate([item], facts=FACTS, scored_ids={"a"})
    assert dropped == [] and len(kept) == 1


def test_a_reason_with_no_posting_cite_declared_is_uncited():
    item = _item(posting=())
    assert validate([item], facts=FACTS, scored_ids={"a"})[1] == [("a", "uncited")]


def test_a_manufactured_gap_is_rejected_but_a_null_gap_is_fine():
    # Claims a gap in Postgres, which the resume lists: the gap goes, the reason stays.
    fake = _item(gap="You have no Postgres experience.", gap_missing="Postgres")
    kept, _ = validate([fake], facts=FACTS, scored_ids={"a"})
    assert len(kept) == 1 and kept[0].gap is None
    # A gap with nothing named is unverifiable, so it goes too.
    vague = _item(gap="Seems light on some things.", gap_missing=None)
    assert validate([vague], facts=FACTS, scored_ids={"a"})[0][0].gap is None
    # A real gap (the resume has no Kubernetes) survives.
    real = _item(gap="The posting wants Kubernetes.", gap_missing="Kubernetes")
    assert validate([real], facts=FACTS, scored_ids={"a"})[0][0].gap == "The posting wants Kubernetes."
    assert validate([_item()], facts=FACTS, scored_ids={"a"})[0][0].gap is None


def test_a_long_reason_is_clamped_not_rejected():
    long_reason = "Your Python work fits. " + "More. " * 200
    gap = "The posting wants Kubernetes. " + "More. " * 200
    item = _item(reason=long_reason, gap=gap, gap_missing="Kubernetes")
    kept, dropped = validate([item], facts=FACTS, scored_ids={"a"})
    assert dropped == [] and len(kept[0].reason) <= REASON_CHARS and len(kept[0].gap) <= GAP_CHARS


def test_fewer_than_five_is_allowed_and_so_is_none():
    assert validate([], facts=FACTS, scored_ids={"a", "b"}) == ([], [])
    kept, _ = validate([_item("a")], facts=FACTS, scored_ids={"a", "b"})
    assert len(kept) == 1


def test_a_repeated_job_id_keeps_the_first():
    kept, dropped = validate([_item("a"), _item("a")], facts=FACTS, scored_ids={"a"})
    assert len(kept) == 1 and dropped == [("a", "duplicate")]


def test_one_off_resume_cite_among_valid_ones_rejects_the_item():
    # "Rust" must not be discarded quietly while "Python" vouches for the sentence.
    item = _item(reason="Your Python and Rust experience fits.", resume=("Python", "Rust"))
    kept, dropped = validate([item], facts=FACTS, scored_ids={"a"})
    assert kept == [] and dropped == [("a", "uncited")]


def test_a_name_in_the_sentence_that_was_never_declared_is_rejected():
    item = _item(reason="Your Python and Rust experience fits.", resume=("Python",))
    kept, dropped = validate([item], facts=FACTS, scored_ids={"a"})
    assert kept == [] and dropped == [("a", "undeclared")]


def test_the_undeclared_scan_leaves_ordinary_sentences_alone():
    ok = [
        _item(reason="Your Python work at Acme fits their Postgres stack.",
              resume=("Python", "Acme", "Postgres"), posting=("Initech",)),
        _item(reason="Strong backend experience, and Python is what they use.",
              resume=("Python",)),
        _item(reason="Your six years of Python match a senior role at Initech.",
              resume=("Python",), posting=("Initech",)),
    ]
    for item in ok:
        assert validate([item], facts=FACTS, scored_ids={"a"})[1] == [], item.reason


def test_a_posting_cite_must_correspond_to_the_posting_when_it_is_given():
    item = _item(posting=("Kubernetes",))
    assert check([item], facts=FACTS, scored_ids={"a"},
                 postings={"a": "Requirements Python"}).dropped == [("a", "uncited")]
    assert check([_item()], facts=FACTS, scored_ids={"a"},
                 postings={"a": "Requirements Python services"}).dropped == []


def test_a_valid_retry_is_not_shadowed_by_an_earlier_bad_copy():
    bad = _item("a", reason="Your Rust work fits.", resume=("Rust",))
    kept, dropped = validate([bad, _item("a")], facts=FACTS, scored_ids={"a"})
    assert [p.job_id for p in kept] == ["a"] and dropped == [("a", "uncited")]


def test_no_more_than_five_prose_records_are_kept():
    ids = {str(n) for n in range(8)}
    kept, dropped = validate([_item(i) for i in sorted(ids)], facts=FACTS, scored_ids=ids)
    assert len(kept) == 5 and [why for _, why in dropped] == ["over_cap"] * 3


def test_a_rejected_gap_is_traced_but_not_a_dropped_record():
    fake = _item("a", gap="You have no Postgres experience.", gap_missing="Postgres")
    checked = check([fake, _item("b")], facts=FACTS, scored_ids={"a", "b"})
    assert checked.gaps_rejected == ["a"] and checked.dropped == []
    assert [p.gap for p in checked.kept] == [None, None]


# --- the provider call -------------------------------------------------------


def _scored(job_id):
    return Scored(job_id=job_id, tier="Good fit", fit_score=0.6,
                  components={"requirements_met": 0.2, "seniority_fit": 0.9,
                              "location_fit": 0.9, "direction_fit": 0.8})


def _job(job_id):
    return SimpleNamespace(id=job_id, title=f"Posting-{job_id}-title", company="Initech",
                           location="Taipei", description_text="Requirements\n- Python")


def _payload(*items, usage=None):
    return {"output_text": json.dumps({"items": list(items)}),
            "usage": usage or {"input_tokens": 900, "output_tokens": 120}}


def _wire(job_id, **kw):
    base = dict(job_id=job_id, reason="Your Python work fits their stack.", gap=None,
                gap_missing=None, resume_cites=["Python"], posting_cites=["Python"])
    base.update(kw)
    return base


class StubResponse:
    def __init__(self, payload, status_code=200):
        self.status_code, self._payload = status_code, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class StubClient:
    def __init__(self, payload, status_code=200):
        self.payload, self.status_code, self.calls = payload, status_code, []

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "body": json})
        return StubResponse(self.payload, self.status_code)


def _install(monkeypatch, stub):
    monkeypatch.setattr("app.services.jobs.reasons.httpx.AsyncClient", stub)
    return stub


def _user(db_session):
    user = User(google_sub="sub-reasons", email="reasons@x.test", locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def _recording(monkeypatch):
    recorded = []
    real = reasons_mod.metered

    @asynccontextmanager
    async def spy(guard):
        async with real(guard) as meter:
            original = meter.record
            meter.record = lambda event: (recorded.append(event), original(event))[1]
            yield meter

    monkeypatch.setattr(reasons_mod, "metered", spy)
    return recorded


async def test_one_call_per_digest_not_one_per_item(openai_enabled, monkeypatch, db_session):
    stub = _install(monkeypatch, StubClient(_payload(_wire("a"), _wire("b"), _wire("c"))))
    recorded = _recording(monkeypatch)
    run = await write_reasons(db_session, user=_user(db_session), facts=FACTS,
                              scored=[_scored("a"), _scored("b"), _scored("c")],
                              jobs=[_job("a"), _job("b"), _job("c")])
    assert len(stub.calls) == 1 and run.error is None
    assert [p.job_id for p in run.prose] == ["a", "b", "c"]
    body = json.dumps(stub.calls[0]["body"]["input"])
    assert all(f"Posting-{j}-title" in body for j in "abc")
    assert stub.calls[0]["body"]["text"]["format"]["strict"] is True
    assert [e["purpose"] for e in recorded] == ["digest"]
    assert run.usage["input_tokens"] == 900


async def test_the_weak_component_is_handed_to_the_model(openai_enabled, monkeypatch, db_session):
    stub = _install(monkeypatch, StubClient(_payload()))
    await write_reasons(db_session, user=_user(db_session), facts=FACTS,
                        scored=[_scored("a")], jobs=[_job("a")])
    assert "requirements_met" in json.dumps(stub.calls[0]["body"]["input"])


async def test_no_survivors_means_no_call(openai_enabled, monkeypatch, db_session):
    stub = _install(monkeypatch, StubClient(_payload()))
    run = await write_reasons(db_session, user=_user(db_session), facts=FACTS, scored=[], jobs=[])
    assert stub.calls == [] and run.prose == [] and run.error is None


async def test_the_model_may_return_none(openai_enabled, monkeypatch, db_session):
    _install(monkeypatch, StubClient(_payload()))
    run = await write_reasons(db_session, user=_user(db_session), facts=FACTS,
                              scored=[_scored("a")], jobs=[_job("a")])
    assert run.prose == [] and run.error is None


async def test_a_provider_failure_keeps_the_scores_and_drops_the_prose(
    openai_enabled, monkeypatch, db_session
):
    # Rows render bare: the caller still holds its Scored list untouched.
    _install(monkeypatch, StubClient({"error": {"message": "overloaded"}}, status_code=503))
    scored = [_scored("a")]
    run = await write_reasons(db_session, user=_user(db_session), facts=FACTS,
                              scored=scored, jobs=[_job("a")])
    assert run.prose == [] and run.error == "provider_error"
    assert scored == [_scored("a")]


async def test_a_billed_call_that_fails_to_parse_is_still_recorded(
    openai_enabled, monkeypatch, db_session
):
    _install(monkeypatch, StubClient({"output_text": "not json", "usage": {"input_tokens": 777}}))
    recorded = _recording(monkeypatch)
    run = await write_reasons(db_session, user=_user(db_session), facts=FACTS,
                              scored=[_scored("a")], jobs=[_job("a")])
    assert run.error == "invalid_payload" and run.prose == []
    assert [e["input_tokens"] for e in recorded] == [777]


async def test_a_billed_5xx_is_recorded_and_an_unbilled_one_is_not(
    openai_enabled, monkeypatch, db_session
):
    recorded = _recording(monkeypatch)
    user = _user(db_session)
    _install(monkeypatch, StubClient({"error": {"message": "x"}, "usage": {"input_tokens": 50}}, 500))
    await write_reasons(db_session, user=user, facts=FACTS,
                        scored=[_scored("a")], jobs=[_job("a")])
    assert [e["input_tokens"] for e in recorded] == [50]
    recorded.clear()
    _install(monkeypatch, StubClient({"error": {"message": "x"}}, 500))
    await write_reasons(db_session, user=user, facts=FACTS,
                        scored=[_scored("a")], jobs=[_job("a")])
    assert recorded == []


async def test_an_invented_skill_in_the_payload_is_dropped_end_to_end(
    openai_enabled, monkeypatch, db_session
):
    bad = _wire("b", reason="Your Rust work fits.", resume_cites=["Rust"])
    _install(monkeypatch, StubClient(_payload(_wire("a"), bad)))
    run = await write_reasons(db_session, user=_user(db_session), facts=FACTS,
                              scored=[_scored("a"), _scored("b")], jobs=[_job("a"), _job("b")])
    assert [p.job_id for p in run.prose] == ["a"] and run.dropped == [("b", "uncited")]


async def test_without_a_key_nothing_is_called(monkeypatch, db_session):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        stub = _install(monkeypatch, StubClient(_payload()))
        run = await write_reasons(db_session, user=_user(db_session), facts=FACTS,
                                  scored=[_scored("a")], jobs=[_job("a")])
        assert run.error == "llm_not_configured" and stub.calls == []
    finally:
        get_settings.cache_clear()
