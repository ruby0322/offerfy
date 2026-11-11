"""Pieces every per-package emitter in this directory is expected to reuse.

Each emitter still owns its own package's component calls (`#edu(...)`,
`#work(...)`, whatever `ats-friendly-resume` renames them to) -- that part
is genuinely package-specific and belongs in each emitter's own module.
What is not package-specific is the CJK fallback, the dates-helper idiom
`basic-resume` and its forks share (even where a fork renames the helper
function itself -- see `dates_expr`'s `helper` argument), the rendering of
a `SkillGroup` as a markup bullet, the bullet-list rendering of a list of
strings, and the "heading, then body, or nothing at all when the body is
empty" shape every section in every emitter so far has used. A second
emitter re-deciding any of these independently is exactly how the
fallback would end up with four different CJK regexes instead of one
answer -- see `CJK_FALLBACK` below.

The Latin face is the opposite: it belongs to the emitter, because it is
the one thing a reader notices and the picker's thumbnail already
promises. See `font_literal`.
"""

from __future__ import annotations

import re

from app.services.resume_content import SkillGroup
from app.services.typst_escape import markup_value, typst_value

# Per-glyph fallback, not an all-or-nothing switch: Typst's `font` accepts
# an array, and every target package here passes it straight to `set
# text(font: font)` (e.g. basic-resume's own `resume.typ`), so naming a
# Latin face and this one together covers a résumé that mixes Latin prose
# with a single CJK name without retyping the whole document in a
# different face for it -- the bug a content-sniffing branch (one CJK
# character anywhere -> switch everything) would otherwise have. "Noto
# Serif CJK TC" mirrors `app.services.starter.PLACEHOLDERS`' own fallback
# locale (`generate_starter` defaults an unmatched locale to zh-TW).
CJK_FALLBACK: str = "Noto Serif CJK TC"


def font_literal(*latin: str) -> str:
    """The Typst array literal for an emitter's `font:` argument: the
    Latin faces this template wants, then `CJK_FALLBACK`.

    **Every emitter names its own Latin face, and they are deliberately
    different.** This function used to take no arguments and hand all
    four emitters `("New Computer Modern", CJK_FALLBACK)`, which made
    the instant half of the picker typographically uniform: switching
    between any two of its templates changed the layout and never the
    typeface, while each thumbnail -- rendered by Typst Universe in that
    package's own face -- promised otherwise. Treating the Latin face as
    shared policy was the mistake; the CJK fallback genuinely is shared,
    and stays here.

    A face named here must be one the host actually has, or Typst warns
    and renders tofu rather than failing, and the support survey's font
    gate then drops the template from the picker entirely. Check against
    `typst fonts --font-path apps/backend/fonts` before naming a new one,
    and add it to the Dockerfile's font block and the host install in
    docs/deploy.md.
    """
    return typst_value([*latin, CJK_FALLBACK])


def dates_expr(
    start: str | None, end: str | None, *, helper: str = "dates-helper"
) -> str | None:
    """A `<helper>(start-date: ..., end-date: ...)` call, or `None` when
    there is nothing to date -- omitting the argument entirely keeps a
    dates-consuming component from rendering a bare em dash when neither
    end of the range is known.

    `helper` defaults to `"dates-helper"`, basic-resume's own name for
    this idiom. Shared because the *shape* of the call (two named
    `start-date`/`end-date` keywords, each a plain string) is what every
    fork so far keeps -- but the name is not: `ats-friendly-resume`
    renamed the function itself to `dates-util` (confirmed against its
    own `src/resume.typ`) while keeping this exact shape. Hard-coding
    `"dates-helper"` here would silently call a function that does not
    exist in that package's namespace, so the name is a parameter, not
    an assumption.
    """
    if not start and not end:
        return None
    return (
        f"{helper}(start-date: {typst_value(start or '')}, "
        f"end-date: {typst_value(end or '')})"
    )


def date_range_expr(start: str | None, end: str | None, *, sep: str = " - ") -> str | None:
    """A plain `"<start><sep><end>"` Typst string literal, or `None` when
    there is nothing to date -- the plain-string counterpart to
    `dates_expr`, for a package with no dates-helper function in its
    namespace at all.

    `dates_expr` assumes the target package exposes a two-keyword helper
    call (`dates-helper(start-date:, end-date:)`, or a fork's rename of
    it) -- true of every emitter before this one. `acorn-resume` has no
    such function anywhere in `src/lib.typ`: its vendor template builds
    the `date:` argument on `exp`/`edu` by hand, as a bare string
    (`date: "Jun 2025 - Present"`). Calling `dates_expr` against this
    package would silently emit a call to a function that does not
    exist. This is the same shape question `dates_expr`'s own `helper`
    keyword exists to answer, just one level further: not *which*
    function to call, but *whether there is a function to call at all* --
    so this is a sibling, not a variant of, `dates_expr`, kept here
    rather than duplicated in `acorn_resume.py` on the chance a later
    package needs the same plain-string shape.
    """
    if not start and not end:
        return None
    if start and end:
        return typst_value(f"{start}{sep}{end}")
    return typst_value(start or end or "")


# A scheme at the front of a URL-ish value, matched case-insensitively --
# `HTTPS://` and `Https://` are as valid in a résumé as `https://`.
_SCHEME_RE = re.compile(r"^\s*https?://", re.IGNORECASE)


def bare_url(value: str | None) -> str:
    """`value` with a leading `http://`/`https://` removed -- the raw
    string behind `url_value`, for a call site that needs the text rather
    than a quoted literal (acorn-resume's `(url, label)` pairs, which
    build their own `https://` target; clean-print-cv's `data` dict, which
    `typst_value` quotes once for the whole dict).

    The IR carries an implicit "bare domain, no scheme" convention that
    nothing used to enforce -- not the schema, not `prompts.EXTRACT_PROMPT`,
    not the emitters -- while every target package (and acorn's own
    `#link`) prepends `"https://"` itself. A value that arrived carrying
    its own scheme therefore produced `https://https://github.com/x` as
    the PDF's `/URI`: a label that looks right above a hyperlink that is
    dead, in the file a person sends to employers. Normalising here rather
    than only at extraction is what makes that true for a snapshot written
    before the prompt said so, or a value typed in by hand.
    """
    return _SCHEME_RE.sub("", value or "").strip()


def url_value(value: str | None) -> str:
    """`bare_url(value)` as a quoted Typst string literal -- what every
    URL-ish argument slot (`url:`, `github:`, `linkedin:`,
    `personal-site:`, `portfolio:`) in every emitter here passes."""
    return typst_value(bare_url(value))


def skill_line(group: SkillGroup) -> str | None:
    """One `- *category*: item, item` (or just one half, whichever is
    present) markup bullet for a `SkillGroup`, or `None` for an empty one.

    Shared because no package in this directory has a dedicated skills
    component -- `basic-resume` and `ats-friendly-resume` alike render
    skills as plain markup bullets under a `== Skills` heading (see
    `ats-friendly-resume`'s own `template/main.typ` example), so the
    rendering is a property of `ResumeContent.skills`, not of either
    package.
    """
    category = markup_value(group.category) if group.category else ""
    items = ", ".join(markup_value(item) for item in group.items if item)
    if not category and not items:
        return None
    if category and items:
        return f"- *{category}*: {items}"
    return f"- {category or items}"


def bullet_lines(items: list[str]) -> list[str]:
    """One `- <item>` line per non-empty item, each run through
    `markup_value` -- the bullet-list idiom every emitter here renders
    free-form prose with."""
    return [f"- {markup_value(item)}" for item in items if item]


def heading_block(title: str, lines: list[str]) -> list[str]:
    """`== <title>`, a blank line, the body, and a trailing blank line --
    or nothing at all when `lines` is empty.

    Every section in every emitter fits this one shape: a fixed heading
    guarded by whether its entries exist (Education, Skills, ...) and a
    user-named `extras` section the same way. Building each section by
    calling this once is what keeps "never emit a heading for a section
    with nothing in it" true everywhere at once, rather than a guard an
    emitter has to remember to repeat at every call site (see
    `test_emitter_omits_a_section_with_no_entries`).
    """
    if not lines:
        return []
    return [f"== {markup_value(title)}", "", *lines, ""]
