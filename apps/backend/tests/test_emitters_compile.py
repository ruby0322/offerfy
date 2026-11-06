"""Compile every registered emitter's own output through the real binary.

`tests/test_emitters.py` checks what an emitter wrote; this checks what
the writing actually produces -- gated on `typst_available()`, matching
`tests/test_import_qa.py`'s pattern (that module documents why: a compile
that exits 0 can still have silently lost content, which only rendering
and re-reading the page can catch).

Parametrized over `deterministic_pairs()` rather than naming
`("basic-resume", "0.2.9")` directly: this is the one test a later
emitter (Tasks 9-11) must not be able to forget to add itself to, and the
registry already enumerates exactly what to iterate.
"""

import string
from io import BytesIO

import pytest
from pdfminer.high_level import extract_pages

from app.services.ats import extract_pdf_text
from app.services.emitters import deterministic_pairs, emitter_for
from app.services.import_qa import verify_render
from app.services.resume_content import Contact, ResumeContent, WorkEntry
from app.services.typst_compile import compile_typst, typst_available
from tests.fixtures.golden_content import GOLDEN

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

PAIRS = sorted(deterministic_pairs())


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_compiles_in_one_page(pair):
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    pdf_bytes = compile_typst(source, "pdf")
    pages = sum(1 for _ in extract_pages(BytesIO(pdf_bytes)))
    assert pages == 1


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_keeps_the_content(pair):
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    report = verify_render(source)
    assert report["ok"] is True, report


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_preserves_a_literal_backslash(pair):
    # Critical 1's end-to-end proof: `verify_render` above normalises both
    # sides of its comparison, which strips every backslash on both sides
    # (see import_qa._normalise and its own module docstring) -- it
    # cannot see a swallowed backslash by construction, coverage-threshold
    # or not. This reads the actual rendered PDF text back and checks the
    # literal, unescaped substring from GOLDEN's own config-path bullet
    # survived character for character.
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    pdf_bytes = compile_typst(source, "pdf")
    pdf_text = extract_pdf_text(pdf_bytes)
    assert "C:\\configs\\prod" in pdf_text


# A test built from the characters an earlier round happened to escape
# cannot fail on a character it did not think of -- which is exactly how
# `#` slipped past a hostile string that covered the eleven characters a
# previous version of `markup_value` did handle. This exhaustive version
# does not curate: it runs through *every* printable ASCII punctuation
# character (`string.punctuation`, all 32 of them), in both an embedded
# ("X{ch}Y") and a leading ("{ch}Y") context, in one document, through
# the real registered emitter and the real binary.
#
# Two characters are deliberately excluded from the exact-match
# assertions, not missed: `"` and `'` are Typst's smart-quote
# substitution (`"hi"` -> `“hi”`), the same "settled, not content loss"
# typography the module docstring excludes dash/ellipsis runs from --
# verified above (`_SMART_QUOTE_CHARS`) to be the *only* two of the 32
# that do not round-trip exactly; every other one of the 32, including
# ones no prior round named (`%`, `&`, `(`, `)`, `,`, `:`, `;`, `?`,
# `{`, `|`, `}`, `^`), already does.
_SMART_QUOTE_CHARS = frozenset("\"'")

# Digraphs no single character in the loop above can represent. `//`,
# `/*`, and `*/` are genuine Typst syntax hazards (a line comment, an
# unterminated block comment -- the exact bug `app.services.import_qa`'s
# module docstring documents for LLM-written source) that must round-trip
# exactly now that every `/` and `*` is escaped unconditionally, with no
# digraph able to form. `--`, `---`, and `...` are Typst's own en/em
# dash and ellipsis shorthand -- settled typography, not asserted exactly
# (see `markup_value`'s own docstring for the disclosed trade-off: a
# `--`/`---` run renders as literal hyphens once it passes through this
# function, since there is no run-length exception carved out for it);
# they are still included in the document, to prove the whole thing
# still compiles cleanly with them present, just not pinned to one exact
# rendering.
_HAZARD_DIGRAPHS = ("//", "/*", "*/")
_SETTLED_DIGRAPHS = ("--", "---", "...")

# A newline embedded in one field: see `markup_value`'s own docstring for
# why this is normalised to a single space rather than passed through.
_EMBEDDED_NEWLINE_CASE = "line one\nline two"
_EMBEDDED_NEWLINE_EXPECTED = "line one line two"


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_round_trips_every_printable_ascii_punctuation_character(pair):
    name, version = pair
    bullets: list[str] = []
    exact_cases: list[str] = []
    for ch in string.punctuation:
        embedded, leading = f"X{ch}Y", f"{ch}Y"
        bullets.extend([embedded, leading])
        if ch not in _SMART_QUOTE_CHARS:
            exact_cases.extend([embedded, leading])
    bullets.extend(_HAZARD_DIGRAPHS)
    exact_cases.extend(_HAZARD_DIGRAPHS)
    bullets.extend(_SETTLED_DIGRAPHS)
    bullets.append(_EMBEDDED_NEWLINE_CASE)

    content = ResumeContent(
        name="Exhaustive Punctuation Case",
        contact=Contact(email="exhaustive@example.com"),
        work=[WorkEntry(title="Exhaustive", company="Punctuation Co", bullets=bullets)],
    )
    source = emitter_for(name, version).emit(content).source
    pdf_bytes = compile_typst(source, "pdf")
    pdf_text = extract_pdf_text(pdf_bytes)
    for case in exact_cases:
        assert case in pdf_text, case
    assert _EMBEDDED_NEWLINE_EXPECTED in pdf_text


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_round_trips_a_hazard_character_in_a_bare_paragraph(pair):
    # `ResumeContent.summary` is the one field rendered as a bare
    # paragraph with no fixed prefix (`"- "`, `"== "`) protecting its
    # first character at all. Unconditional escaping does not need this
    # case to be special -- there is no position check left to get
    # wrong -- but it is still the context most exposed if one ever
    # crept back in, so it stays covered on its own.
    name, version = pair
    for marker in "-+/=":
        summary = f"{marker} leads this summary line"
        content = ResumeContent(
            name="Hostile Case",
            contact=Contact(email="hostile@example.com"),
            summary=summary,
        )
        source = emitter_for(name, version).emit(content).source
        pdf_bytes = compile_typst(source, "pdf")
        pdf_text = extract_pdf_text(pdf_bytes)
        assert summary in pdf_text, (marker, pdf_text)
