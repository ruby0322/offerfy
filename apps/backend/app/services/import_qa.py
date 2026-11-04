"""Render verification: did the compiled PDF actually show what the source says?

**The bug this exists to stop.** An import agent wrote a bullet containing
`Coinbase \\@x402/*`. `\\@` correctly escapes the reference, but `/*` opens a
Typst **block comment that runs to end-of-file** -- there is no matching
`*/` anywhere later in the document, so Typst swallows everything after it.
`typst compile` still exits 0: a block comment is syntactically fine, so
nothing fails the compile. The rendered PDF is one truncated bullet and
nothing else -- every later section is gone. The import agent's own
compile-only check (`verify_compile=True` on the turn) cannot see this,
because the thing that is wrong is not a compile error, it is a silent
content loss. `verify_render` is the second check this needs: did the text
that is in the source actually make it onto the page.

**Algorithm.** `verify_render(typst_source)`:

1. Compile `typst_source` to PDF (`app.services.typst_compile.compile_typst`)
   and extract its text (`app.services.ats.extract_pdf_text`). No new PDF
   library -- both already exist and are already relied on elsewhere
   (`app.services.ats`'s own ATS checks run against the same extraction).
2. Collect *candidate* visible strings out of the source, in source order
   (see `_candidates` below for the exact rules and, just as important,
   the exclusions).
3. Normalise both the candidates and the extracted PDF text to one
   lowercase alphanumeric(+CJK) word stream (see `_normalise`).
4. A candidate is "found" if its normalised words appear **contiguously**
   in the PDF's normalised word stream -- Typst's own line wrapping
   collapses to a single space on both sides, so wrapping a candidate
   across two PDF lines does not cost it a match.
5. `coverage = found / checked`. `ok = coverage >= RENDER_OK_THRESHOLD`
   (or `True` if there was nothing to check at all).

**Why a threshold, not "every candidate present."** Formatting noise --
ligatures, hyphenation, a stray font substitution, the odd candidate this
module should have excluded but didn't -- must not fail an honest import.
The real bug scored **0%** (`.superpowers/sdd/2026-11-04-resume-import-progress/`
`t.typ` + `probe.py`: 0/3, every single candidate gone), because `/*` does
not pick a convenient stopping point -- it erases the rest of the document.
`RENDER_OK_THRESHOLD = 0.7` leaves the real failure mode (which lands at or
near 0%) a wide margin below the line while still refusing an import that
is mostly a cliff with a token surviving fragment at the very top.

**False positives are the main risk** -- a false "not ok" costs the person
a repair round for nothing. See `_candidates` for every deliberate
exclusion and why it exists. When in doubt, this module excludes a
candidate rather than risk flagging real content as missing.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

from app.services.ats import extract_pdf_text
from app.services.typst_compile import compile_typst, typst_available

# The real bug scores ~0%; see the module docstring. 0.7 leaves a wide
# margin above plausible formatting noise (wrapped lines, ligatures, a
# candidate this module under-excludes) and a wide margin below the
# cliff the bug actually produces.
RENDER_OK_THRESHOLD = 0.7

# "Skip candidates under 3 normalised words -- too weak a signal to act
# on" (spec). A one- or two-word candidate (a short name, "City, Country",
# a bare year) is cheap to match by coincidence and expensive to act on
# when it's wrong.
MIN_CANDIDATE_WORDS = 3

# A cheap pre-filter on quoted `#let` literals, before normalisation: the
# real gate is MIN_CANDIDATE_WORDS, but there is no point running every
# one-character flag value through normalisation first.
MIN_LET_LITERAL_CHARS = 8

# CJK Unified Ideographs + Extension A. Narrow on purpose: this covers the
# Chinese resumes this product actually renders (zh-TW / zh-CN), not every
# CJK-adjacent script. Hiragana/Katakana/Hangul are not localised here, and
# fullwidth punctuation is correctly treated as punctuation (stripped), not
# content -- widening the range would only risk keeping junk.
_CJK_RANGE = "\u4e00-\u9fff\u3400-\u4dbf"

_BULLET_PREFIX_RE = re.compile(r"^[-=*+]+\s*")
_ESCAPE_RE = re.compile(r"\\(.)")
_CJK_CHAR_RE = re.compile(f"([{_CJK_RANGE}])")
_NON_WORD_RE = re.compile(rf"[^0-9A-Za-z{_CJK_RANGE}\s]+")
# Matches a scheme (`https://`, `mailto:`) at the very start of a literal --
# used to drop URL-shaped strings (see `_candidates`'s "Exclusion 1").
_URL_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*://|mailto:)", re.IGNORECASE)
# A whole source line that is nothing but a self-contained block comment,
# e.g. `/* TODO: tighten the margins */` -- see "Exclusion 4".
_FULL_LINE_BLOCK_COMMENT_RE = re.compile(r"^/\*.*\*/$")
_LET_LITERAL_RE = re.compile(r'"([^"]{%d,})"' % MIN_LET_LITERAL_CHARS)


def _normalise(text: str) -> str:
    """One lowercase alphanumeric(+CJK) word stream, applied identically to
    source candidates and extracted PDF text so the two sides are
    comparable.

    - De-hyphenate across a line break (`-\\n` -> ``): Typst (and PDF text
      extraction) can wrap a long word at a hyphen; without this, a wrapped
      word never matches its unwrapped candidate.
    - Unescape a backslash escape (`\\@` -> `@`, `\\*` -> `*`, ...): the
      *source* candidate carries the escaping backslash the model wrote;
      the *rendered* text never does. Comparing them unescaped is what lets
      the repro bullet's `\\@x402` match the page's literal `@x402`.
    - Split every CJK character into its own token (surround it with
      spaces) before the punctuation pass below. Chinese prose carries no
      inter-word spaces, so without this a whole Chinese sentence collapses
      to a single token and the MIN_CANDIDATE_WORDS gate -- and the
      contiguous-match check -- would never fire correctly for it. Treating
      each Han character as its own word matches how pdfminer already
      extracts CJK text (character by character, no inferred spacing), so
      both sides tokenise the same way.
    - Replace every remaining non-alphanumeric, non-CJK character with a
      space (drops markup glyphs, punctuation, bullet/heading markers,
      full-width punctuation).
    - Lowercase and collapse whitespace.
    """
    text = text.replace("-\n", "")
    text = _ESCAPE_RE.sub(r"\1", text)
    text = _CJK_CHAR_RE.sub(r" \1 ", text)
    text = _NON_WORD_RE.sub(" ", text)
    return " ".join(text.lower().split())


def _is_url(literal: str) -> bool:
    return bool(_URL_RE.match(literal.strip()))


def _candidates(source: str) -> list[str]:
    """Visible-ish strings out of `source`, in source order.

    Rule: markup lines (headings, bullets, prose), plus quoted literals of
    real length out of `#let` lines. Everything else -- a `#`-prefixed
    directive line and everything inside the parentheses it opens, even
    across line breaks -- is skipped, because it is code, not prose, even
    when its keyword arguments hold real values (see Exclusion 2).

    **Deliberate exclusions, and why each one exists** (false positives
    are the expensive mistake here -- see the module docstring):

    1. **URL-shaped literals.** `#link("https://...")[label]`'s href
       string never renders as text -- only `label` does, and `label` is
       not inside the quotes. A `#let`-declared contact literal that
       happens to be a full URL (`#let personal-site = "https://..."`)
       is excluded on the same rule: being conservative here only means
       this module checks less, never that it wrongly flags real content.

    2. **Directive keyword arguments.** `#edu(institution: "...", degree:
       "...", ...)` and `#work(...)` *do* render their string arguments
       (institution, degree, company, title, location, dates) -- but they
       span multiple source lines inside one pair of parentheses, and nothing
       about a continuation line like `  institution: "...",` looks
       different from a `#let` literal or from prose. Treating it as either
       produces guaranteed false positives on `#show: resume.with(...)`'s
       own style parameters (`accent-color`, `font`, `paper`, `lang`), which
       never render as text at all. Tracking paren depth from the opening
       `#`-line and skipping every line until it closes is the one rule that
       correctly excludes the style parameters without needing to tell them
       apart from the renderable ones -- at the cost of not checking
       institution/degree/company/etc. That coverage loss is accepted
       deliberately: those fields are short, structured, and not where the
       bug this module exists for actually occurs (free-form prose bullets
       are). See the module docstring.

    3. **The `#import` line's package specifier.** `#import
       "@preview/basic-resume:0.2.9": *`'s quoted string names a package,
       never visible text. `#let` lines get literal extraction; `#import`
       lines do not.

    4. **Comments.** A `//...` line, and a line that is nothing but a
       self-contained `/* ... */`, are source-only by definition and must
       never be compared against the rendered page. (An *unterminated*
       `/*` -- the actual bug -- is deliberately **not** special-cased:
       the prose on that same line, and every candidate after it, is
       exactly what must come back "missing". Excluding it would blind
       this module to the one failure mode it exists to catch.)
    """
    out: list[str] = []
    paren_depth = 0
    for raw in source.splitlines():
        stripped = raw.strip()
        if paren_depth > 0:
            paren_depth = max(0, paren_depth + stripped.count("(") - stripped.count(")"))
            continue
        if not stripped:
            continue
        if stripped.startswith("//") or _FULL_LINE_BLOCK_COMMENT_RE.match(stripped):
            continue
        if stripped.startswith("#import"):
            paren_depth = max(0, stripped.count("(") - stripped.count(")"))
            continue
        if stripped.startswith("#let"):
            for literal in _LET_LITERAL_RE.findall(stripped):
                if not _is_url(literal):
                    out.append(literal)
            paren_depth = max(0, stripped.count("(") - stripped.count(")"))
            continue
        if stripped.startswith("#"):
            # A directive invocation: #show, #set, #edu, #work, #link,
            # #align, #pagebreak, ... -- see Exclusion 2.
            paren_depth = max(0, stripped.count("(") - stripped.count(")"))
            continue
        candidate = _BULLET_PREFIX_RE.sub("", stripped)
        if candidate and not _is_url(candidate):
            out.append(candidate)
        paren_depth = max(0, stripped.count("(") - stripped.count(")"))
    return [c for c in out if len(_normalise(c).split()) >= MIN_CANDIDATE_WORDS]


def _contains_contiguous(haystack_words: str, needle_words: str) -> bool:
    """Whether `needle_words` appears as a contiguous run of whole words in
    `haystack_words`. Both are already space-joined normalised word
    streams; padding each side with a boundary space turns Python's plain
    substring check into a word-boundary-safe one -- without it, the
    3-letter candidate "cat" would spuriously match inside the unrelated
    word "concatenate".
    """
    return f" {needle_words} " in f" {haystack_words} "


def _cliff_index(found: list[bool]) -> int | None:
    """The index where a trailing run of at least two consecutive misses
    begins, or `None` if there isn't one.

    This is the signature of the bug this module exists for: an unescaped
    `/*` (or `@`, or `[`) does not scatter a few candidates at random, it
    erases everything from that point to the end of the document. A single
    trailing miss is ordinary noise (the last line before EOF is a common
    place for a candidate this module under-excludes); two or more in a
    row with nothing recovering after them is the cliff.
    """
    n = len(found)
    i = n
    while i > 0 and not found[i - 1]:
        i -= 1
    run = n - i
    if run >= 2:
        return i
    return None


def verify_render(typst_source: str) -> dict:
    """Compile `typst_source`, extract its text, and report how much of the
    source's visible content actually made it onto the page.

    Returns a dict with at least:
    - `ok` (bool): `coverage >= RENDER_OK_THRESHOLD`, or `True` if there was
      nothing to check (`checked == 0`).
    - `coverage` (float): `found / checked`, or `1.0` if `checked == 0`.
    - `missing` (list[str]): candidates not found, in source order.
    - `checked` (int): how many candidates were evaluated.

    Plus, for the repair loop and for debugging:
    - `found` (int): `checked - len(missing)`.
    - `cliff_at` (str | None): the first missing candidate in a trailing
      run of two or more (see `_cliff_index`) -- "everything from here on
      is missing" is a far more actionable thing to hand an agent than an
      unordered list.
    - `compile_error` (str | None): set instead of the above if the source
      did not compile at all. This should not normally happen here -- the
      import turn already runs with `verify_compile=True`, which repairs
      compile failures inline -- but `verify_render` stays correct standing
      alone, which is what makes it unit-testable against the real repro
      without a running import.

    If the Typst binary itself is unavailable (`typst_available()` is
    `False` -- some environment running this code has no CLI installed),
    the check is skipped and reports `ok=True`, the same convention
    `app.services.typst_compile.compile_status` already follows: Typst's
    absence is an environment gap this function should not surface as a
    render failure the import then spends three repair rounds fighting.
    """
    if not typst_available():
        return {
            "ok": True,
            "coverage": 1.0,
            "missing": [],
            "checked": 0,
            "found": 0,
            "cliff_at": None,
            "compile_error": None,
        }
    try:
        pdf_bytes = compile_typst(typst_source, "pdf")
    except HTTPException as exc:
        return {
            "ok": False,
            "coverage": 0.0,
            "missing": [],
            "checked": 0,
            "found": 0,
            "cliff_at": None,
            "compile_error": str(exc.detail),
        }

    pdf_text = extract_pdf_text(pdf_bytes)
    pdf_words = _normalise(pdf_text)

    candidates = _candidates(typst_source)
    found_flags = [_contains_contiguous(pdf_words, _normalise(c)) for c in candidates]

    checked = len(candidates)
    found = sum(found_flags)
    missing = [c for c, ok in zip(candidates, found_flags) if not ok]
    coverage = 1.0 if checked == 0 else found / checked
    cliff = _cliff_index(found_flags)

    return {
        "ok": checked == 0 or coverage >= RENDER_OK_THRESHOLD,
        "coverage": coverage,
        "missing": missing,
        "checked": checked,
        "found": found,
        "cliff_at": candidates[cliff] if cliff is not None else None,
        "compile_error": None,
    }
