from app.services.jobs.boilerplate import (
    BOILERPLATE_MIN_POSTINGS,
    boilerplate_hashes,
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


# Review Focus 3
def test_cjk_description_without_terminators_survives_as_one_sentence():
    cjk = "我們正在尋找一位資深後端工程師負責支付系統的設計與維護工作"
    assert split_sentences(cjk) == [cjk]
    assert strip_boilerplate(cjk, frozenset()) == cjk
