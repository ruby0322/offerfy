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
    PostingText,
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
              resume=("Python",), posting=("Python",)),
        _item(reason="Your six years of Python match a senior role at Initech.",
              resume=("Python",), posting=("Initech",)),
    ]
    for item in ok:
        checked = check([item], facts=FACTS, scored_ids={"a"}, postings=_posting())
        assert checked.dropped == [], item.reason


def test_a_posting_cite_must_correspond_to_the_posting_when_it_is_given():
    item = _item(posting=("Kubernetes",))
    assert check([item], facts=FACTS, scored_ids={"a"},
                 postings=_posting(requirements="Python")).dropped == [("a", "uncited")]
    assert check([_item()], facts=FACTS, scored_ids={"a"},
                 postings=_posting(requirements="Python services")).dropped == []


def _posting(company="Initech", title="Engineer", location="", requirements="Python"):
    return {"a": PostingText(company=company, title=title, location=location,
                             requirements=requirements)}


def test_names_from_company_and_location_are_not_claims_about_the_person():
    cases = [
        ("Your Python work fits the role in Taipei.", _posting(location="Taipei, Taiwan")),
        ("Your Python work fits the Remote role.", _posting(location="Remote")),
        ("Your Python work matches the Backend requirements with AWS.",
         _posting(company="AWS")),
    ]
    for reason, postings in cases:
        item = _item(reason=reason, resume=("Python",), posting=("Python",))
        checked = check([item], facts=FACTS, scored_ids={"a"}, postings=postings)
        assert checked.dropped == [], reason


def test_a_requirement_mirrored_as_experience_is_still_rejected():
    # Docker is in the posting's requirements, not on the resume, not declared.
    item = _item(reason="Your Python and Docker experience fits.", resume=("Python",), posting=("Python",))
    postings = _posting(requirements="Python and Docker")
    assert check([item], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
        ("a", "undeclared")
    ]
    # ...and a requirement sitting only in the requirements cannot ride in as "location".
    item = _item(reason="Your Python work matches the AWS requirements.", resume=("Python",), posting=("Python",))
    postings = _posting(requirements="Python and AWS")
    assert check([item], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
        ("a", "undeclared")
    ]


def test_disclaiming_a_requirement_belongs_in_the_gap_not_the_reason():
    postings = _posting(requirements="Python and Docker")
    as_reason = _item(reason="Your Python work fits their Docker setup, though Docker is new.",
                    posting=("Python",))
    assert check([as_reason], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
        ("a", "undeclared")
    ]
    as_gap = _item(reason="Your Python work fits their stack.",
                   gap="Docker is new for you.", gap_missing="Docker", posting=("Python",))
    checked = check([as_gap], facts=FACTS, scored_ids={"a"}, postings=postings)
    assert checked.dropped == [] and checked.gaps_rejected == []
    assert checked.kept[0].gap == "Docker is new for you."


def test_a_posting_cite_never_exempts_a_requirement_token():
    # The model listing "Docker" as a posting cite must not launder it.
    for tech in ("Docker", "AWS"):
        postings = _posting(requirements=f"Python and {tech}")
        item = _item(reason=f"Your Python work and {tech} experience fits their stack.",
                     resume=("Python",), posting=(tech,))
        assert check([item], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
            ("a", "undeclared")
        ], tech


def test_a_technology_in_the_title_is_not_exempt():
    postings = _posting(title="Senior Terraform Engineer", requirements="Python")
    item = _item(reason="Your Python and Terraform experience fits this role.",
                 resume=("Python",), posting=("Python",))
    assert check([item], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
        ("a", "undeclared")
    ]
    ok = _item(reason="Your Python work fits this role.", resume=("Python",), posting=("Python",))
    assert check([ok], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == []


def test_a_gap_that_claims_the_person_did_the_missing_thing_is_rejected():
    postings = _posting(requirements="Python and Kubernetes")
    claim = _item(gap="You have shipped Kubernetes in prod.", gap_missing="Kubernetes",
                  posting=("Python",))
    checked = check([claim], facts=FACTS, scored_ids={"a"}, postings=postings)
    assert checked.kept[0].gap is None and checked.gaps_rejected == ["a"]


def test_a_gap_may_not_smuggle_in_a_second_invented_claim():
    postings = _posting(requirements="Python and Docker")
    sneaky = _item(gap="Docker experience is missing, you led Terraform migrations.",
                   gap_missing="Docker", posting=("Python",))
    checked = check([sneaky], facts=FACTS, scored_ids={"a"}, postings=postings)
    assert checked.kept[0].gap is None and checked.gaps_rejected == ["a"]
    fine = _item(gap="Docker experience is missing from your resume.", gap_missing="Docker",
                 posting=("Python",))
    assert check([fine], facts=FACTS, scored_ids={"a"}, postings=postings).kept[0].gap


def test_a_name_where_a_sentence_starts_is_still_an_invented_claim():
    # Position excuses nothing: only a stopword excuses a capitalised token.
    postings = _posting(requirements="Python and Terraform")
    first = _item(reason="Terraform and Python work fits this role.", posting=("Python",))
    assert check([first], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
        ("a", "undeclared")
    ]
    second = _item(reason="Your Python work fits. Terraform shipped in prod.",
                   posting=("Python",))
    assert check([second], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
        ("a", "undeclared")
    ]
    # ...and the same in a gap, where only gap_missing is excused.
    in_gap = _item(gap="Docker is missing. Kubernetes led by you.", gap_missing="Docker",
                   posting=("Python",))
    checked = check([in_gap], facts=FACTS, scored_ids={"a"},
                    postings=_posting(requirements="Python and Docker"))
    assert checked.kept[0].gap is None and checked.gaps_rejected == ["a"]


def test_the_stopword_list_leaves_ordinary_sentence_openers_alone():
    # Sentences a reviewer wrote that earlier rounds dropped over their first
    # word ("Based", "Fully") -- not over anything they claim.
    ok = [
        (_item(reason="Based in Berlin, you already work in Python, which Initech uses.",
               posting=("Initech",)), _posting(company="Initech", location="Berlin")),
        (_item(reason="Fully remote Python work in Europe matches your location.",
               posting=("Python",)), _posting(location="Remote, Europe")),
        (_item(reason="Based on your Python work, this is a good match.",
               posting=("Python",)), _posting()),
        (_item(reason="Join Initech to use your Python skills.", posting=("Initech",)),
         _posting(company="Initech")),
        (_item(reason="Both your CV and the posting name Python.", posting=("Python",)),
         _posting()),
    ]
    for item, postings in ok:
        assert check([item], facts=FACTS, scored_ids={"a"},
                     postings=postings).dropped == [], item.reason


def test_a_reason_may_not_narrate_the_person_s_history():
    # No capitalised token in any of these, so no name scan could reach them:
    # the shape is what gives them away.
    for reason in (
        "You managed 40 engineers, and your Python fits.",
        "Your Python fits and you have 12 years of it.",
        "Your Python fits; you saved $5M.",
        "Your Python fits; you cut latency by 90%.",
        "Your Python work fits and you worked there too.",
        "Since 2019 you have built Python services.",
    ):
        item = _item(reason=reason, posting=("Python",))
        assert check([item], facts=FACTS, scored_ids={"a"}, postings=_posting()).dropped == [
            ("a", "narrated")
        ], reason
    # Present-tense matching is what a reason is FOR, and is left alone.
    for reason in (
        "Python, which this role centres on, is on your resume.",
        "Your Python work fits their stack.",
        "Python is the language you use daily, and this role centres on it.",
    ):
        item = _item(reason=reason, posting=("Python",))
        assert check([item], facts=FACTS, scored_ids={"a"},
                     postings=_posting()).dropped == [], reason


def test_a_negated_possession_is_the_right_way_to_write_a_gap():
    # The absence rule rejected these, which was backwards.
    postings = _posting(requirements="Python and Kubernetes")
    for gap in (
        "You have not worked with Kubernetes.",
        "The posting wants Kubernetes; you have no listed Kubernetes work.",
        "Kubernetes is required, but you haven't listed it.",
    ):
        item = _item(gap=gap, gap_missing="Kubernetes", posting=("Python",))
        checked = check([item], facts=FACTS, scored_ids={"a"}, postings=postings)
        assert checked.kept[0].gap == gap and checked.gaps_rejected == [], gap
    # The positive form is still rejected.
    claim = _item(gap="You have Kubernetes experience.", gap_missing="Kubernetes",
                  posting=("Python",))
    assert check([claim], facts=FACTS, scored_ids={"a"}, postings=postings).kept[0].gap is None


def test_an_absence_needs_no_absence_verb_to_be_one():
    postings = _posting(requirements="Python and Kubernetes")
    for gap in (
        "The team uses Kubernetes, but your resume has none.",
        "Your resume has nothing on Kubernetes.",
        "The job needs three years of Kubernetes; your resume shows fewer.",
    ):
        item = _item(gap=gap, gap_missing="Kubernetes", posting=("Python",))
        assert check([item], facts=FACTS, scored_ids={"a"},
                     postings=postings).kept[0].gap == gap, gap


def test_a_gap_may_not_assert_possession_in_any_of_its_shapes():
    postings = _posting(requirements="Python and Docker")
    for gap in (
        "Docker is missing; you did Docker at Acme.",
        "Docker is missing, though you deployed Docker at Acme.",
        "Docker is missing, but you were Docker lead at Acme.",
        "Docker is missing, though you're a Docker expert.",
        "Docker is missing, but you'd shipped Docker.",
        "Docker is missing, but your production Docker experience is strong.",
        "Docker is missing, but your Docker expertise is strong.",
        "Docker is missing, but your Docker skills are strong.",
        # Relative clauses that used to walk through the who/that carve-out.
        "Docker is missing, but you are the one that has shipped Docker.",
        "Docker is missing, though you, who have shipped Docker, applied.",
    ):
        item = _item(gap=gap, gap_missing="Docker", posting=("Python",))
        checked = check([item], facts=FACTS, scored_ids={"a"}, postings=postings)
        assert checked.kept[0].gap is None and checked.gaps_rejected == ["a"], gap
    # A clause about the posting's ideal candidate is about nobody in
    # particular, and naming the absence of the person's own work is the point.
    for gap in (
        "They want someone who has shipped Docker; your resume shows none.",
        "Your Docker work is missing.",
    ):
        item = _item(gap=gap, gap_missing="Docker", posting=("Python",))
        assert check([item], facts=FACTS, scored_ids={"a"},
                     postings=postings).kept[0].gap == gap, gap


def test_two_letter_technology_names_are_not_invisible():
    # Go, JS and C# are languages; R and C are the one-character cases.
    for tech in ("Go", "JS", "C#", "R"):
        postings = _posting(requirements=f"Python and {tech}")
        item = _item(reason=f"Your Python work fits, and your {tech} work too.",
                     posting=("Python",))
        assert check([item], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
            ("a", "undeclared")
        ], tech


def test_a_gap_may_not_assert_that_the_person_has_the_thing():
    # An absence marker is not proof of absence: a demand verb says nothing
    # about the person, so possession is rejected whatever marker it carries.
    postings = _posting(requirements="Python and Kubernetes")
    for gap in (
        "You need Kubernetes experience and have shipped it in prod.",
        "You have Kubernetes experience, which the posting wants.",
    ):
        item = _item(gap=gap, gap_missing="Kubernetes", posting=("Python",))
        checked = check([item], facts=FACTS, scored_ids={"a"}, postings=postings)
        assert checked.kept[0].gap is None and checked.gaps_rejected == ["a"], gap
    # Absence wording and the posting's demand both still stand.
    fine = _item(gap="Docker experience is missing from your resume.", gap_missing="Docker",
                 posting=("Python",))
    assert check([fine], facts=FACTS, scored_ids={"a"},
                 postings=_posting(requirements="Python and Docker")).kept[0].gap
    demand = _item(gap="The posting wants Kubernetes.", gap_missing="Kubernetes",
                   posting=("Python",))
    assert check([demand], facts=FACTS, scored_ids={"a"}, postings=postings).kept[0].gap


def test_gap_missing_names_one_requirement_not_a_phrase():
    # An unbounded gap_missing exempts every name inside it from the scan.
    phrase = "Kubernetes production deployments at Google Scale"
    item = _item(gap=f"No {phrase}", gap_missing=phrase, posting=("Python",))
    checked = check([item], facts=FACTS, scored_ids={"a"},
                    postings=_posting(requirements="Python and Kubernetes"))
    assert checked.kept[0].gap is None and checked.gaps_rejected == ["a"]


def test_a_company_named_after_a_technology_is_exempt_only_as_a_mention():
    # Docker, Snowflake and Terraform are all employers.
    postings = _posting(company="Terraform", requirements="Python")
    attributed = _item(reason="Your Python and Terraform work fits this role.",
                       posting=("Python",))
    assert check([attributed], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
        ("a", "undeclared")
    ]
    mentioned = _item(reason="Your Python work fits Terraform.", posting=("Python",))
    assert check([mentioned], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == []


def test_a_posting_cite_matching_only_a_label_does_not_correspond():
    postings = _posting(location="Taipei")
    for cite in ("Requirements", "Location", "Title", "Company"):
        item = _item(posting=(cite,))
        assert check([item], facts=FACTS, scored_ids={"a"}, postings=postings).dropped == [
            ("a", "uncited")
        ], cite


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
