import json
from datetime import datetime, timezone

import pytest

from app.models import CompanyBoilerplate, Job

from app.services.jobs.boilerplate import (
    BOILERPLATE_MIN_POSTINGS,
    boilerplate_hashes,
    load_boilerplate,
    recompute_boilerplate,
    sentence_hash,
    split_sentences,
    strip_boilerplate,
)


def test_split_sentences_folds_and_drops_short_fragments():
    long_one = "This Sentence Is Comfortably Longer Than The Minimum."
    assert split_sentences(f"{long_one} Too short.") == [long_one.casefold()]


def test_boilerplate_is_the_sentence_in_at_least_half_the_corpus():
    mission = "Anduril is committed to bringing cutting-edge autonomy and computer vision."
    docs = [f"{mission} We need a {role}." for role in ("buyer", "thermal engineer")]
    docs.append("An unrelated posting with no shared sentence whatsoever here.")
    hashes = boilerplate_hashes(docs * 2)
    assert sentence_hash(mission.casefold()) in hashes
    assert len(hashes) == 1


def test_corpus_below_the_minimum_has_no_boilerplate():
    doc = "A sentence repeated across every single one of these few postings."
    assert boilerplate_hashes([doc] * (BOILERPLATE_MIN_POSTINGS - 1)) == frozenset()


def test_a_sentence_repeated_within_one_posting_counts_once():
    repeated = "This sentence is said many times inside a single posting."
    docs = [f"{repeated} {repeated} {repeated} {repeated}"]
    docs += [f"Distinct unrelated posting number {i} with its own text." for i in range(5)]
    assert boilerplate_hashes(docs) == frozenset()


def test_strip_removes_only_the_boilerplate_sentence():
    mission = "We bring cutting-edge autonomy and computer vision to the world."
    body = "You will own our payments ledger and its reconciliation batch."
    kept = strip_boilerplate(f"{mission} {body}", frozenset({sentence_hash(mission.casefold())}))
    assert "computer vision" not in kept
    assert "payments ledger" in kept


# Review Focus 1
def test_stripping_every_sentence_yields_empty_text_not_a_crash():
    only = "Every sentence in this posting is shared company boilerplate here."
    assert strip_boilerplate(only, frozenset({sentence_hash(only.casefold())})) == ""


# Review Focus 2 -- newline-separated bullets, which carry no terminator, so
# a space-join really would splice "computer" and "vision" into the needle.
def test_stripping_does_not_splice_a_needle_across_the_junction():
    first = "Tooling we use day to day includes a bespoke computer"
    middle = "This middle line is shared boilerplate and gets removed"
    last = "vision and robotics stack maintained by the platform team"
    kept = strip_boilerplate(
        "\n".join((first, middle, last)), frozenset({sentence_hash(middle.casefold())})
    )
    assert "computer vision" not in kept


# The strongest statement of what stripping is: a posting whose company has no
# boilerplate comes back byte-identical, every separator, capital and bullet
# where the board put it. This subsumes the padded-needle regression it
# replaced -- stripping rewrites no separator at all now, so no needle can
# appear or disappear at a segment edge. An absent row and an empty hash set
# are the same case here, which recompute_boilerplate's change reporting
# relies on.
@pytest.mark.parametrize(
    "text",
    [
        "Our stack:\nReact\nTypeScript\nNode",
        "Responsibilities:\n- Build services in Go\n- Own our SQL warehouse",
        "  Leading and trailing space is kept.   \n\n  Mixed CASE too.  ",
        "\u6211\u5011\u6b63\u5728\u5c0b\u627e\u4e00\u4f4d\u8cc7\u6df1\u5f8c\u7aef\u5de5\u7a0b\u5e2b",
        "",
    ],
)
def test_no_boilerplate_leaves_the_description_byte_identical(text):
    assert strip_boilerplate(text, frozenset()) == text
    unrelated = sentence_hash("a sentence from a completely different employer.")
    assert strip_boilerplate(text, frozenset({unrelated})) == text


# Review Focus 3
def test_cjk_description_without_terminators_survives_as_one_sentence():
    cjk = "我們正在尋找一位資深後端工程師負責支付系統的設計與維護工作"
    assert split_sentences(cjk) == [cjk]
    assert strip_boilerplate(cjk, frozenset()) == cjk


def test_company_boilerplate_round_trips_its_hash_array(db_session):
    db_session.add(CompanyBoilerplate(
        company="Anduril",
        sentence_hashes=json.dumps(["a" * 64, "b" * 64]),
        posting_count=2375,
        computed_at=datetime.now(timezone.utc),
    ))
    db_session.flush()
    row = db_session.get(CompanyBoilerplate, "Anduril")
    assert json.loads(row.sentence_hashes) == ["a" * 64, "b" * 64]


def _add_jobs(db_session, company, descriptions, *, title="Engineer"):
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    start = db_session.query(Job).count()
    jobs = []
    for offset, text in enumerate(descriptions):
        job = Job(
            source="greenhouse",
            source_id=f"bp-{start + offset}",
            company=company,
            title=title,
            location="Remote",
            remote=True,
            apply_url="https://example.com/apply",
            source_url="https://example.com/job",
            description_text=text,
            description_html=f"<p>{text}</p>",
            posted_at=now,
            first_seen_at=now,
            last_seen_at=now,
            is_active=True,
        )
        db_session.add(job)
        jobs.append(job)
    db_session.flush()
    return jobs


MISSION = "We bring cutting-edge autonomy and computer vision to every mission."
BODY = "You will own supplier contracts and the quarterly sourcing review."


def test_recompute_stores_the_shared_sentence_and_reports_the_change(db_session):
    _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    assert recompute_boilerplate(db_session) == {"anduril"}
    assert sentence_hash(MISSION.casefold()) in load_boilerplate(db_session, ["Anduril"])["anduril"]


def test_recompute_is_idempotent_and_reports_no_change_the_second_time(db_session):
    _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    recompute_boilerplate(db_session)
    assert recompute_boilerplate(db_session) == set()


def test_inactive_postings_are_not_part_of_the_corpus(db_session):
    jobs = _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    for job in jobs[:3]:
        job.is_active = False
    db_session.flush()
    recompute_boilerplate(db_session)
    assert load_boilerplate(db_session, ["Anduril"]) == {}


# Review Focus 5
def test_company_falling_below_the_minimum_loses_its_stale_row(db_session):
    jobs = _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    recompute_boilerplate(db_session)
    for job in jobs[:2]:
        job.is_active = False
    db_session.flush()
    assert recompute_boilerplate(db_session) == {"anduril"}
    assert load_boilerplate(db_session, ["Anduril"]) == {}
    assert db_session.get(CompanyBoilerplate, "anduril") is None


# Review Focus 4
def test_company_names_differing_by_case_or_space_share_one_row(db_session):
    # Three each: below BOILERPLATE_MIN_POSTINGS apart, at it together.
    _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(3)])
    _add_jobs(db_session, "  anduril ", [f"{MISSION} {BODY} Role {i}." for i in range(3, 6)])
    assert recompute_boilerplate(db_session) == {"anduril"}
    assert sentence_hash(MISSION.casefold()) in load_boilerplate(db_session, ["Anduril"])["anduril"]
