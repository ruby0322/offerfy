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
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import resolve1

from app.services.ats import extract_pdf_text
from app.services.emitters import deterministic_pairs, emitter_for
from app.services.import_qa import verify_render
from app.services.resume_content import (
    Contact,
    ProjectEntry,
    ResumeContent,
    WorkEntry,
)
from app.services.template_probe import font_warnings
from app.services.typst_compile import (
    compile_typst,
    compile_typst_with_stderr,
    typst_available,
)
from tests.fixtures.golden_content import GOLDEN, MINIMAL

pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

PAIRS = sorted(deterministic_pairs())

# Every test below is parametrized over `PAIRS`, so an emptied registry
# would collect nothing at all and turn this whole compile gate green by
# vacuity rather than by passing. Asserted at import time, once, the same
# way the rest of this suite refuses to let a shrinking fixture stand in
# for a passing assertion.
assert PAIRS, "deterministic_pairs() is empty -- every gate below would collect nothing"


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_compiles_in_one_page(pair):
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    pdf_bytes = compile_typst(source, "pdf")
    pages = sum(1 for _ in extract_pages(BytesIO(pdf_bytes)))
    assert pages == 1


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_has_no_missing_font_warning(pair):
    # Every emitter in this registry sets its target's font explicitly
    # (_common.font_literal) rather than trusting the package's own
    # default -- acorn-resume's own `resume()` defaults to `font:
    # "Calibri"`, which this host does not have, and its vendor example
    # fails this exact gate in `data/template_support.json` for that
    # reason (see acorn_resume.py's module docstring). Parametrized over
    # every registered pair, not just acorn-resume: a clean font probe is
    # the whole reason this emitter's output -- not the vendor example --
    # is what a later survey run offers the pair on.
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    _pdf_bytes, stderr = compile_typst_with_stderr(source, "pdf")
    assert font_warnings(stderr) == ()


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_output_keeps_the_content(pair):
    name, version = pair
    source = emitter_for(name, version).emit(GOLDEN).source
    report = verify_render(source)
    assert report["ok"] is True, report


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_renders_a_dateless_projects_url(pair):
    # Task 9's review found this: ats-friendly-resume's own project()
    # component (resume.typ) shows a project's `url:` only inside
    # `if url != "" and dates != "" [...]` -- a project with a URL and no
    # dates (GOLDEN.projects[1], see golden_content.py's own docstring on
    # why this is a separate fixture entry from the first, dated project)
    # silently lost its URL from the rendered page even though the
    # emitted source passed `url:` as an argument, and `verify_render`'s
    # own content-survival check above cannot catch this class of bug by
    # construction (import_qa.verify_render re-extracts a different
    # thing than "did a specific field survive"; see that module). This
    # reads the actual rendered PDF text back, not the emitted source, so
    # a template-layout bug that hides something from the page -- while
    # the source otherwise looks correct -- cannot hide from this test
    # either.
    name, version = pair
    dateless = GOLDEN.projects[1]
    assert dateless.url and not (dateless.start or dateless.end)
    source = emitter_for(name, version).emit(GOLDEN).source
    pdf_bytes = compile_typst(source, "pdf")
    pdf_text = extract_pdf_text(pdf_bytes)
    assert dateless.url in pdf_text


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
# character (`string.punctuation`, all 32 of them -- exhaustive over
# that specific, closed set, which is what matters: nothing in Typst's
# markup grammar is non-ASCII, so ASCII punctuation is the complete
# hazard class, even though it is not every character in the alphabet),
# in both an embedded and a leading context, in one document, through
# the real registered emitter and the real binary.
#
# The two contexts are deliberately non-overlapping and each shaped so
# it can actually fail if the escaping regresses:
# - `embedded = f"mid{ch}mid"` has no shared substring with `leading`.
# - `leading = f"{ch} lead"` has a trailing space, which the leading
#   case needs to even be *capable* of exhibiting a list/heading marker
#   -- `{ch}Y` with no space after it renders as a literal character
#   whether `ch` is escaped or not (verified against the real binary),
#   so it could never have failed regardless of what this module did,
#   and `{ch}Y` is also a substring of `X{ch}Y` for every one of these
#   32 characters, so that assertion was already satisfied by the
#   embedded case alone. Both holes made the previous version of this
#   test unable to fail even with the positional bug from the last
#   review round reintroduced.
#
# Two characters are deliberately excluded from the exact-match
# assertions, not missed: `"` and `'` are Typst's smart-quote
# substitution (`"hi"` -> `“hi”`), the same settled typography the
# module docstring's "Settled" note excludes dash/ellipsis runs from --
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
# digraph able to form -- asserted exactly, same as the single characters.
#
# `--`/`---` (Typst's en/em dash shorthand) and `...` (ellipsis) are the
# module docstring's "Settled" note: intentional typography, not content
# loss. `--`/`---` happen to round-trip exactly too, once every `-` is
# escaped unconditionally (see `markup_value`'s docstring for why this is
# a disclosed trade-off, not a design goal) -- asserted, but it is worth
# being exact about *what* that assertion pins, because an earlier
# version of this comment claimed more than it delivers. In New Computer
# Modern, the face every emitter pins, the escaped and unescaped forms
# shape to the same glyph (the font's own `--` -> endash ligature applies
# either way; measured, one 5.50pt glyph both times), so this assertion
# is not evidence about how the page *looks*. What it reads back is the
# PDF's ToUnicode mapping: `--` for the escaped run, `–` for the
# unescaped one. A regression that stopped escaping `-` therefore does
# fail here -- verified by removing `-` from `_MARKUP_ALWAYS_ESCAPE` --
# but as a text-layer difference (the one ATS parsing sees), not a
# visible one. The leading-position cases above (`f"{ch} lead"`) are what
# guard the *visible* half: with `-` unescaped, `- lead` inside a bullet
# renders as a nested list item instead of text.
# `...` is excluded from the exact-match set on purpose: `.` was never in
# `_MARKUP_ALWAYS_ESCAPE`, so `...` still becomes a single ellipsis
# character, which is correct and not asserted against here -- it is
# still in the document, to prove the whole thing compiles cleanly with
# it present, just not pinned to one exact rendering.
_HAZARD_DIGRAPHS = ("//", "/*", "*/")
_SETTLED_DASH_RUNS = ("--", "---")
_SETTLED_ELLIPSIS = "..."

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
        embedded, leading = f"mid{ch}mid", f"{ch} lead"
        bullets.extend([embedded, leading])
        if ch not in _SMART_QUOTE_CHARS:
            exact_cases.extend([embedded, leading])
    bullets.extend(_HAZARD_DIGRAPHS)
    exact_cases.extend(_HAZARD_DIGRAPHS)
    bullets.extend(_SETTLED_DASH_RUNS)
    exact_cases.extend(_SETTLED_DASH_RUNS)
    bullets.append(_SETTLED_ELLIPSIS)
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


def _pdf_uris(pdf_bytes: bytes) -> list[str]:
    """Every `/URI` an annotation in this PDF actually points at.

    The rendered *text* of a hyperlink is not what a doubled scheme
    breaks -- the label reads correctly either way, which is exactly why
    this had to be checked against the link target rather than the
    emitted source or the extracted text.
    """
    found: list[str] = []
    document = PDFDocument(PDFParser(BytesIO(pdf_bytes)))
    for page in PDFPage.create_pages(document):
        for annotation in resolve1(page.annots) or []:
            action = resolve1(resolve1(annotation).get("A")) or {}
            uri = action.get("URI")
            if uri:
                found.append(uri.decode() if isinstance(uri, bytes) else str(uri))
    return found


# A résumé whose contacts and project URL each arrived carrying their own
# scheme -- the IR's "bare domain" convention is a convention, not a
# constraint the schema enforces, so a snapshot written before
# `EXTRACT_PROMPT` asked for it (or a value typed in by hand) looks like
# this. Mixed case on purpose: `_common.bare_url` matches the scheme
# case-insensitively.
_SCHEMED_CONTACTS = ResumeContent(
    name="Scheme Carrying Case",
    contact=Contact(
        email="scheme@example.com",
        github="https://github.com/weiling",
        linkedin="HTTP://linkedin.com/in/weiling",
        website="Https://weiling.dev",
    ),
    projects=[ProjectEntry(name="Doubled", url="https://github.com/weiling/doubled")],
)


@pytest.mark.parametrize("pair", PAIRS)
def test_registered_emitter_never_emits_a_doubled_scheme_in_a_link_target(pair):
    # Every one of these packages (and acorn-resume's own `#link`)
    # prepends `"https://"` to the value it is handed, so passing a value
    # that already carries one produced `https://https://github.com/x` as
    # the link target: the label looked right and the hyperlink was dead,
    # in the PDF a person sends to employers. Read back off the compiled
    # PDF's annotations rather than asserted on the emitted source,
    # because the source is where it looked fine.
    #
    # `clean-print-cv` is the one pair that renders no link annotation at
    # all (it prints every URL as plain text -- confirmed against
    # `cv-template.typ`), so for that pair this is the weaker statement
    # that nothing regressed into emitting a broken one. The companion
    # test below is what keeps the assertion from going vacuous across
    # the whole registry.
    name, version = pair
    source = emitter_for(name, version).emit(_SCHEMED_CONTACTS).source
    uris = _pdf_uris(compile_typst(source, "pdf"))
    for uri in uris:
        assert "https://https://" not in uri, uri
        assert "https://http://" not in uri, uri
        # Belt and braces for any other ordering a future emitter could
        # produce: a scheme may appear only at position 0.
        assert uri.lower().count("http://") + uri.lower().count("https://") <= 1, uri


def test_the_registry_renders_link_targets_at_all():
    """The doubled-scheme gate above is per-pair, and a pair that renders
    no annotations passes it for free. If every emitter stopped emitting
    links -- or `_pdf_uris` stopped finding them -- that gate would go
    green on nothing at all, which is the same vacuity class the
    `assert PAIRS` at the top of this module exists for.
    """
    everything: list[str] = []
    for name, version in PAIRS:
        source = emitter_for(name, version).emit(_SCHEMED_CONTACTS).source
        everything.extend(_pdf_uris(compile_typst(source, "pdf")))
    assert "https://github.com/weiling" in everything
    assert "https://github.com/weiling/doubled" in everything
    assert "mailto:scheme@example.com" in everything


# --- clean-print-cv's unguarded contact separators ------------------------


def _clean_print_cv_contact_row(content: ResumeContent) -> str:
    source = emitter_for("clean-print-cv", "0.1.0").emit(content).source
    text = extract_pdf_text(compile_typst(source, "pdf"))
    # The row directly under the name -- the second non-empty line of the
    # rendered page, by cv-header's own layout.
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[1]


def test_clean_print_cv_renders_no_separator_junk_for_three_contact_values():
    # The fix's real payoff, read off the page rather than the source:
    # `email | phone | location` is printed with *unconditional*
    # separators (cv-template.typ:107-111) and `contact-sep` is a
    # closed-over `#let`, so the only lever is which values occupy those
    # three slots. Packing present values into them in order means
    # anyone with three or more contact values -- email + phone +
    # location, or email + two links -- renders with no stray separator
    # at all. Field-for-field mapping rendered this exact content as
    # `r@example.com  |    |    |  github.com/a  |  a.dev`.
    content = ResumeContent(
        name="Packed",
        contact=Contact(email="r@example.com", github="github.com/a", website="a.dev"),
    )
    assert _clean_print_cv_contact_row(content) == "r@example.com  |  github.com/a  |  a.dev"


def test_clean_print_cv_renders_one_trailing_separator_for_two_contact_values():
    # Two values fill two of the three unguarded slots, so one trailing
    # separator is left. Field-for-field mapping rendered the same
    # content with three separators and two of them dangling.
    content = ResumeContent(
        name="Two", contact=Contact(email="r@example.com", github="github.com/a")
    )
    assert _clean_print_cv_contact_row(content).rstrip() == "r@example.com  |  github.com/a  |"


def test_clean_print_cv_still_renders_two_separators_for_minimal():
    """What `MINIMAL` -- name + email, the shape of an untouched starter --
    actually renders through this emitter, pinned rather than assumed.

    This is the residual the packing cannot remove, recorded here so it
    is a known number rather than a surprise: the unguarded part of
    cv-header's contact row always emits exactly two `contact-sep`
    glyphs, and no arrangement of a single value can hide both. Removing
    them would mean not calling `cv-header` at all (reimplementing the
    name block, rule, subtitle and contact row against this package's
    *internal* style `#let`s) or emitting negative `h()` spacing into a
    slot, which is font- and size-dependent junk in a PDF a person sends
    to employers. If this assertion ever starts failing because the row
    got *cleaner*, that is the fix, not a regression -- update it.
    """
    row = _clean_print_cv_contact_row(MINIMAL)
    assert row.startswith("jordan@example.com")
    assert row.count("|") == 2
    assert row.rstrip() == "jordan@example.com  |    |"
