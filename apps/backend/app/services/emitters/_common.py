"""Pieces every per-package emitter in this directory is expected to reuse.

Each emitter still owns its own package's component calls (`#edu(...)`,
`#work(...)`, whatever `ats-friendly-resume` renames them to) -- that part
is genuinely package-specific and belongs in each emitter's own module.
What is not package-specific is the font policy, the `dates-helper(...)`
idiom `basic-resume` and its forks share, the bullet-list rendering of a
list of strings, and the "heading, then body, or nothing at all when the
body is empty" shape every section in every emitter so far has used. A
second emitter re-deciding any of these independently is exactly how the
font policy would end up with four different CJK regexes instead of one
answer -- see `FALLBACK_FONTS` below.
"""

from __future__ import annotations

from app.services.typst_escape import markup_value, typst_value

# Per-glyph fallback, not an all-or-nothing switch: Typst's `font` accepts
# an array, and every target package here passes it straight to `set
# text(font: font)` (e.g. basic-resume's own `resume.typ`), so naming both
# faces once covers a résumé that mixes Latin prose with a single CJK name
# without retyping the whole document in a different face for it -- the
# bug a content-sniffing branch (one CJK character anywhere -> switch
# everything) would otherwise have. "Noto Serif CJK TC" mirrors
# `app.services.starter.PLACEHOLDERS`' own fallback locale
# (`generate_starter` defaults an unmatched locale to zh-TW).
FALLBACK_FONTS: tuple[str, str] = ("New Computer Modern", "Noto Serif CJK TC")


def font_literal() -> str:
    """`FALLBACK_FONTS` rendered as the Typst array literal every
    emitter's `font:` argument should use verbatim."""
    return typst_value(list(FALLBACK_FONTS))


def dates_expr(start: str | None, end: str | None) -> str | None:
    """A `dates-helper(...)` call, or `None` when there is nothing to date
    -- omitting the argument entirely keeps a dates-consuming component
    from rendering a bare em dash when neither end of the range is known.
    Shared because `dates-helper` is the basic-resume idiom its forks
    (`ats-friendly-resume` among them) keep even where they rename other
    parameters.
    """
    if not start and not end:
        return None
    return (
        f"dates-helper(start-date: {typst_value(start or '')}, "
        f"end-date: {typst_value(end or '')})"
    )


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
