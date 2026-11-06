"""Make LLM-written Typst more likely to compile, and raw data safe to
place inside it.

Fill/edit turns often paste resume text with raw `"`, `$`, `@`, or `*` into
string fields and list items. Typst then treats them as syntax.
`escape_typst_string`/`escape_typst_markup`/`sanitize_typst_source` handle
that case: already-written Typst *source*, where a `"` really does open a
string literal and a `\\` is already part of an intentional escape.

`typst_value`/`markup_value` are the other case: raw *data* (e.g. an
emitter rendering `ResumeContent` straight into a document -- see
`app.services.emitters`) that was never pre-escaped at all, where a `"` is
just a character and every `\\` must be doubled before anything else runs.
Mixing the two up is exactly how a data-mode caller silently drops a
backslash or breaks on a quote followed by `$`/`@` -- pick the function
for what the text actually is, not for where it ends up.

Comments are left alone: escaping is meaningless inside `//` and `/* ... */`,
and template comments full of `name: ""` placeholders otherwise get rewritten
on every edit.
"""

from __future__ import annotations

import re
from typing import Any

_STRING_FIELD = re.compile(
    r"""(?x)
    ^
    (?P<prefix>
        .*?
        (?:
            \#let \s+ [\w.-]+ \s* = \s*
            | (?<![A-Za-z0-9_-]) [\w.-]+ \s* : \s*
        )
    )
    "
    (?P<inner>.*)
    "
    (?P<suffix> \s* ,? \s* \)? \s* )
    $
    """
)
_BULLET = re.compile(r"^(\s*-\s+)(.*)$")
_IMPORT = re.compile(r"^\s*#import\s")


def escape_typst_string(value: str) -> str:
    """Escape text for a Typst `"..."` literal without double-escaping."""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(value[i : i + 2])
            i += 2
            continue
        if ch == '"':
            out.append(r"\"")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def typst_value(value: Any) -> str:
    """Render a Python value as a Typst literal: dict/list data inlined straight
    into source, rather than loaded from a sidecar file at compile time.

    Unlike `escape_typst_string`, which assumes a backslash in LLM-written Typst
    source is already part of an intentional escape sequence, a backslash here
    comes from raw data (YAML/TOML/JSON) and is never pre-escaped -- so every
    backslash is doubled before the quote escaping is applied.
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f'"{escape_typst_string(value.replace("\\", "\\\\"))}"'
    if isinstance(value, dict):
        if not value:
            return "(:)"
        pairs = "".join(
            f"{typst_value(str(key))}: {typst_value(item)}, "
            for key, item in value.items()
        )
        return f"({pairs})"
    if isinstance(value, (list, tuple)):
        if not value:
            return "()"
        items = "".join(f"{typst_value(item)}, " for item in value)
        return f"({items})"
    return f'"{escape_typst_string(str(value))}"'


def escape_typst_markup(text: str) -> str:
    """Escape `$`, `@`, and unmatched `*` in Typst markup (list items)."""
    out: list[str] = []
    i = 0
    star_at: list[int] = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
            continue
        if ch == '"':
            out.append(ch)
            i += 1
            while i < len(text):
                if text[i] == "\\" and i + 1 < len(text):
                    out.append(text[i : i + 2])
                    i += 2
                    continue
                out.append(text[i])
                i += 1
                if out[-1] == '"':
                    break
            continue
        if ch == "$":
            out.append(r"\$")
            i += 1
            continue
        if ch == "@":
            out.append(r"\@")
            i += 1
            continue
        if ch == "*":
            star_at.append(len(out))
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    if len(star_at) % 2 == 1:
        for idx in reversed(star_at):
            out[idx] = r"\*"
    return "".join(out)


# Every character Typst's markup grammar gives special meaning to,
# escaped unconditionally regardless of position or pairing: `#` opens
# code mode; `$` opens math; `@` opens a reference; `*`/`_` toggle
# strong/emphasis; `` ` `` opens raw/code; `<`/`>` can open a label;
# `[`/`]` can open a content block; `~` is the non-breaking-space
# shorthand; `-`/`+`/`/` open a bullet/numbered/term list item and `=`
# opens a heading when they are the first character *of a line*; `/`
# followed by another `/` is also a line comment *anywhere* on the line
# (not just at the start), which escaping every `/` on its own,
# unconditionally, prevents from ever forming in the first place.
#
# "Unconditionally" is the point, not a simplification of something more
# careful: an earlier version of this function escaped `-`/`+`/`/`/`=`
# only as the first character of the whole *string*, reasoning that a
# bullet/heading's own fixed prefix ("- ", "== ") already occupies that
# position for every other caller. That reasoning had two holes a
# position check can never close: leading whitespace in the data
# (`"  - indented"` is still line-initial once rendered, `out[0]` is a
# space) and an embedded newline (`"line one\n- line two"` makes the
# text after the `\n` line-initial too, which no single `out[0]` check
# can see) -- both verified against the real binary to fabricate
# structure or hard-fail the compile. A position check is exactly the
# kind of reasoning that also missed `#` entirely in an earlier round:
# checking *where* a character is, instead of just escaping every
# instance of it, leaves a gap for every position nobody enumerated.
# Escaping every occurrence, full stop, has no such gap: there is no
# position left to get wrong.
#
# All fifteen verified against the real binary to render back as the
# literal character with no other effect when escaped with a leading
# `\`, individually and in combination (see tests/test_typst_escape.py
# and tests/test_emitters_compile.py's round-trip tests). `"` is
# deliberately absent -- it is just a character in markup prose, not a
# string literal to protect (Typst renders it as a smart quote, which is
# correct), unlike in `escape_typst_markup`'s source-sanitizing job.
#
# Not escaped, deliberately: a *run* of two or more `-` (Typst's en/em
# dash shorthand) and three `.` (ellipsis) are intact typography this
# feature must not suppress -- see the module docstring's "Settled"
# note. `.` was never in this set; a lone `-` still is, and always will
# be escaped on its own (an isolated `-` has no shorthand to preserve),
# but this module does not scan for a `--`/`---` run to carve out an
# exception for it, so a résumé bullet that happens to contain one will,
# after this function, render that run as literal hyphens rather than a
# dash. That is a known, disclosed trade-off of escaping unconditionally
# rather than conditionally (see this module's own docstring and the
# task report for the reasoning), not an oversight.
_MARKUP_ALWAYS_ESCAPE = frozenset("#$@*_`<>[]~-+/=")


def markup_value(text: str) -> str:
    """Escape raw, never-pre-escaped data for direct use as Typst markup
    (a bullet, a heading, a paragraph) -- the markup-mode counterpart to
    `typst_value`'s string-literal escaping, for an emitter rendering
    `ResumeContent` text straight into a document body rather than into a
    quoted argument.

    `escape_typst_markup` above is not safe for this: it was written for
    *already-written Typst source* (an LLM's own bullet, edited in place),
    where a `"` really does open a string literal worth copying verbatim,
    a `\\` is already part of an intentional escape sequence, and a
    *paired* `*`/`_`/`` ` `` is assumed to be formatting the editor
    actually intended. None of those assumptions hold for data straight
    out of a pydantic model: a `"` is just a character, a `\\` is never
    pre-escaped, and a matched pair of `*`s in someone's raw résumé text
    (`C# and F#`, `Rated *5/5*`) is almost certainly not a request for
    bold -- it is two characters that happen to appear next to each
    other. So this function does not try to guess intent, or position,
    at all: every `\\` is doubled first (exactly like `typst_value`, for
    the same reason -- an un-doubled backslash hands Typst an escape
    sequence that silently consumes the next character), an embedded
    newline is normalised to a single space (see the paragraph below),
    and then every markup-significant character in `_MARKUP_ALWAYS_
    ESCAPE` is escaped -- every occurrence, with no pairing rule and no
    position check. See that set's own comment for why unconditional
    beats "only when it would otherwise break" as a method, and for the
    one disclosed trade-off this makes (a `--`/`---` run that would
    otherwise become Typst's own en/em dash renders as literal hyphens
    instead, since there is no run-length exception carved out for it).

    An embedded `\\n`/`\\r` is normalised to a single space rather than
    passed through as a literal line break. `ResumeContent`'s fields
    (`summary`, every `bullets`/`items`/`title`/entry) are plain `str`
    with nothing upstream stripping a newline out of an LLM extraction,
    so a multi-line value is representable even though every emitter
    treats each one as a single logical line of prose. Passing a raw
    newline through would split that line in the *emitted Typst source*
    itself -- harmless for a bullet or heading built with this package's
    own fixed prefix (the continuation merely loses its bullet styling,
    a formatting defect, not a correctness one, and already how
    `escape_typst_markup` treats a newline in LLM-written source), but
    `_skill_line` builds one whole skill group as a single source line
    (`- *Category*: item, item, item`); a newline inside one item would
    cut that line off mid-list, losing every item after it from that
    group -- exactly the silent-loss class this module exists to close,
    just caused by whitespace instead of a special character. Normalising
    first means no caller has to know which shape it is building.
    """
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = text.replace("\\", "\\\\")
    return "".join(f"\\{ch}" if ch in _MARKUP_ALWAYS_ESCAPE else ch for ch in text)


def sanitize_typst_source(source: str) -> str:
    out: list[str] = []
    depth = 0
    for line in source.split("\n"):
        code, comment, depth = _split_comment(line, depth)
        out.append(_sanitize_line(code) + comment)
    return "\n".join(out)


def _split_comment(line: str, depth: int) -> tuple[str, str, int]:
    """Split a line into its leading code and the comment that follows it.

    `depth` is the `/* ... */` nesting carried in from earlier lines, and is
    returned updated for the next one. A line that opens inside a block comment
    has no leading code, so it is handed back untouched. Anything after the
    first comment start is left verbatim too, including code that follows an
    inline `/* ... */` on the same line -- rare enough not to be worth the
    bookkeeping, and leaving it unescaped is no worse than not matching.

    String state does not carry across lines: a stray `"` in markup is far more
    common here than a Typst string literal spanning lines, and treating the
    rest of the file as one string would hide every later comment.
    """
    start: int | None = 0 if depth else None
    i = 0
    in_string = False
    while i < len(line):
        if depth:
            if line.startswith("*/", i):
                depth -= 1
                i += 2
                continue
            if line.startswith("/*", i):
                depth += 1
                i += 2
                continue
            i += 1
            continue
        if start is not None:
            # A block comment opened and closed earlier on this line.
            break
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if in_string:
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if line.startswith("//", i):
            return line[:i], line[i:], 0
        if line.startswith("/*", i):
            depth = 1
            start = i
            i += 2
            continue
        i += 1
    if start is None:
        return line, "", 0
    return line[:start], line[start:], depth


def _sanitize_line(line: str) -> str:
    if _IMPORT.match(line):
        return line
    match = _STRING_FIELD.match(line)
    if match:
        inner = escape_typst_string(match.group("inner"))
        return f'{match.group("prefix")}"{inner}"{match.group("suffix")}'
    bullet = _BULLET.match(line)
    if bullet:
        return bullet.group(1) + escape_typst_markup(bullet.group(2))
    return line
