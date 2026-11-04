"""verify_render: did the compiled PDF actually show what the source says?

Three fixtures anchor this module, per the task brief:
- `REPRO_SOURCE` is the *exact* real bug, reproduced byte-for-byte from
  `.superpowers/sdd/2026-11-04-resume-import-progress/t.typ`: an unescaped
  `/*` opens a Typst block comment that runs to end-of-file. It must score
  far below the threshold.
- `CLEAN_SOURCE` is an ordinary, correctly-escaped import result. It must
  pass.
- `NOISY_SOURCE` has the kind of thing that is *not* a bug -- a long
  wrapped line, a trailing short fragment -- and must still pass, because
  `ok` is a threshold, not "every candidate present".

These need the real `typst` binary and are skipped if it is not on the
runner, matching the rest of the suite's existing pattern
(`tests/test_typst_edit.py`'s `pytest.mark.skipif`).
"""

from pathlib import Path

import pytest

from app.services.import_qa import (
    RENDER_OK_THRESHOLD,
    _candidates,
    _normalise,
    verify_render,
)
from app.services.typst_compile import typst_available

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

REPRO_PATH = (
    Path(__file__).resolve().parents[3]
    / ".superpowers"
    / "sdd"
    / "2026-11-04-resume-import-progress"
    / "t.typ"
)
REPRO_SOURCE = REPRO_PATH.read_text(encoding="utf-8")

CLEAN_SOURCE = """\
#import "@preview/basic-resume:0.2.9": *

#let name = "Alice Chen"
#let location = "City, Country"
#let email = "alice.chen@example.com"

#show: resume.with(
  author: name,
  location: location,
  email: email,
  accent-color: "#f4be82",
  font: "New Computer Modern",
  paper: "a4",
  lang: "en",
  author-position: left,
  personal-info-position: left,
)

== Education

#edu(
  institution: "National Taiwan University",
  location: "City, Country",
  dates: dates-helper(start-date: "2018-09", end-date: "2022-06"),
  degree: "B.S. Computer Science",
)

== Experience

#work(
  title: "Software Engineer",
  location: "City, Country",
  company: "Example Labs",
  dates: dates-helper(start-date: "2022-07", end-date: "Present"),
)
- Led a phased re-architecture from bespoke payment infra to Coinbase \\@x402 standard libraries
- Extracted IDRX adapter logic into published npm packages

== Skills

Python, TypeScript, Rust
"""

# Ordinary formatting noise: a long bullet Typst will wrap across several
# PDF lines, plus a short trailing fragment that is weak signal on its own
# (and would be dropped by MIN_CANDIDATE_WORDS besides). Nothing here is a
# content-loss bug; coverage should still clear the threshold.
NOISY_SOURCE = """\
#import "@preview/basic-resume:0.2.9": *

#let name = "Bob Lee"
#let location = "City, Country"
#let email = "bob.lee@example.com"

#show: resume.with(
  author: name,
  location: location,
  email: email,
  accent-color: "#f4be82",
  font: "New Computer Modern",
  paper: "a4",
  lang: "en",
  author-position: left,
  personal-info-position: left,
)

== Experience

#work(
  title: "Software Engineer",
  location: "City, Country",
  company: "Example Labs",
  dates: dates-helper(start-date: "2022-07", end-date: "Present"),
)
- Designed and shipped a resilient event-driven billing pipeline that reconciles payment provider webhooks against internal ledgers, cutting manual finance review time by more than half across three currencies
- OK
"""


def test_the_real_repro_scores_far_below_threshold():
    """The exact bug: `\\@x402/*` opens a block comment that swallows
    everything after it. `typst compile` exits 0, but Education and Skills
    never make it onto the page.
    """
    result = verify_render(REPRO_SOURCE)
    assert result["compile_error"] is None, "the repro must still compile cleanly"
    assert result["coverage"] < 0.3
    assert result["ok"] is False
    assert result["missing"]
    assert result["cliff_at"] is not None


def test_a_clean_import_passes():
    result = verify_render(CLEAN_SOURCE)
    assert result["compile_error"] is None
    assert result["ok"] is True
    assert result["coverage"] >= RENDER_OK_THRESHOLD
    assert result["cliff_at"] is None


def test_ordinary_formatting_noise_still_passes():
    """A long wrapped bullet and a short trailing fragment are not the bug;
    `ok` must not punish them. This is the threshold's whole reason to
    exist: "every candidate present" would be too strict for real output.
    """
    result = verify_render(NOISY_SOURCE)
    assert result["ok"] is True


def test_coverage_is_found_over_checked():
    result = verify_render(CLEAN_SOURCE)
    assert result["found"] == result["checked"] - len(result["missing"])
    if result["checked"]:
        assert abs(result["coverage"] - result["found"] / result["checked"]) < 1e-9


def test_a_document_with_nothing_to_check_is_vacuously_ok():
    # No bullets, no headings, no #let literals long enough to matter --
    # just the template skeleton with its placeholders.
    source = (
        '#import "@preview/basic-resume:0.2.9": *\n\n'
        '#let name = ""\n'
        '#let email = ""\n'
    )
    result = verify_render(source)
    assert result["checked"] == 0
    assert result["ok"] is True
    assert result["coverage"] == 1.0


# ---------------------------------------------------------------------------
# _candidates -- the exclusions, each tested on its own
# ---------------------------------------------------------------------------


def test_import_lines_contribute_no_candidates():
    """The package specifier on #import is never visible text."""
    out = _candidates('#import "@preview/basic-resume:0.2.9": *\n- Real bullet content here')
    assert all("basic-resume" not in c for c in out)


def test_let_literals_are_candidates_but_short_ones_are_not():
    out = _candidates('#let email = "alice.chen@example.com"\n#let name = "Al"')
    assert "alice.chen@example.com" in out
    assert "Al" not in out


def test_url_shaped_let_literals_are_excluded():
    out = _candidates('#let personal-site = "https://alice.dev/portfolio-and-more"')
    assert out == []


def test_directive_lines_and_their_multiline_arguments_are_skipped():
    """#edu(...)'s keyword arguments -- institution, degree, etc. -- are
    real visible content but span multiple lines inside one set of
    parens; `#show: resume.with(...)`'s style parameters never render at
    all. Both must be excluded, and the exclusion must survive the
    multi-line span.
    """
    source = (
        "#show: resume.with(\n"
        '  accent-color: "#f4be82",\n'
        '  font: "New Computer Modern",\n'
        ")\n\n"
        "#edu(\n"
        '  institution: "National Taiwan University",\n'
        '  degree: "B.S. Computer Science",\n'
        ")\n"
        "- A real achievement bullet with enough words in it"
    )
    out = _candidates(source)
    assert out == ["A real achievement bullet with enough words in it"]


def test_a_url_shaped_bullet_line_is_excluded():
    out = _candidates("- https://example.com/totally/a/url/not/prose")
    assert out == []


def test_line_comments_are_excluded():
    out = _candidates("// internal note, never rendered, ignore this please\n- Real content here")
    assert out == ["Real content here"]


def test_self_contained_block_comments_are_excluded():
    out = _candidates("/* a deliberate, closed, one-line note to self here */\n- Real content here")
    assert out == ["Real content here"]


def test_heading_and_bullet_markers_are_stripped():
    out = _candidates("== Experience section heading\n- a bullet with a leading dash removed")
    assert out == ["Experience section heading", "a bullet with a leading dash removed"]


def test_an_unterminated_block_comment_is_not_specially_excluded():
    """This is the bug itself -- the opposite of exclusion. The prose
    before the `/*` and the prose on the same line after it are one
    candidate (the whole line); candidates are not special-cased on
    partial-line structure.
    """
    out = _candidates("- Safe prefix \\@x402/* everything after this is gone")
    assert out == ["Safe prefix \\@x402/* everything after this is gone"]


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------


def test_normalise_unescapes_backslash_escapes():
    assert _normalise("Coinbase \\@x402") == "coinbase x402"


def test_normalise_dehyphenates_across_line_breaks():
    assert _normalise("archi-\ntecture") == "architecture"


def test_normalise_tokenises_every_cjk_character_separately():
    # No inter-word spaces in Chinese prose; each character is its own
    # token, the same way pdfminer extracts CJK text.
    assert _normalise("黃淑芬") == "黃 淑 芬"


def test_normalise_strips_punctuation_and_collapses_whitespace():
    assert _normalise("City,   Country!!") == "city country"


# ---------------------------------------------------------------------------
# Typst unavailable: skip, don't fail (matches compile_status's convention)
# ---------------------------------------------------------------------------


def test_render_check_is_skipped_when_typst_is_unavailable(monkeypatch):
    import app.services.import_qa as import_qa

    monkeypatch.setattr(import_qa, "typst_available", lambda: False)
    result = import_qa.verify_render(REPRO_SOURCE)
    assert result["ok"] is True
    assert result["checked"] == 0
    assert result["compile_error"] is None
