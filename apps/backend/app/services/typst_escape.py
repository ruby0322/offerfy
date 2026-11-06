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


# Every character Typst's markup grammar gives special meaning to
# *anywhere* in a line, independent of position or pairing: `#` opens
# code mode; `$` opens math; `@` opens a reference; `*`/`_` toggle
# strong/emphasis; `` ` `` opens raw/code; `<`/`>` can open a label;
# `[`/`]` can open a content block; `~` is the non-breaking-space
# shorthand. Verified against the real binary, character by character
# and in combination (see tests/test_typst_escape.py and
# tests/test_emitters_compile.py's round-trip test): every one of these,
# escaped with a leading `\`, renders back as the literal character with
# no other effect. `"` is deliberately absent -- it is just a character
# in markup prose, not a string literal to protect (Typst renders it as
# a smart quote, which is correct), unlike in `escape_typst_markup`'s
# source-sanitizing job.
_MARKUP_ALWAYS_ESCAPE = frozenset("#$@*_`<>[]~")

# These four only matter as the very *first* character of a line: `-`,
# `+`, and `/` open a bullet/numbered/term list item, and `=` opens a
# heading -- but only when they begin the line. A bullet or heading built
# by this package's emitters is always written with its own fixed prefix
# (`"- "`, `"== "`), which already occupies that position, so the data
# itself is never actually line-initial there. A bare paragraph (the
# `summary` field, rendered with no prefix at all) is the one place data
# can land at true line start, so escaping the data's own leading
# character unconditionally -- harmless when a prefix already protects
# it, since `\-` renders identically to an unescaped `-` mid-line -- is
# what keeps every caller safe without each one having to know whether it
# is "the prefixed kind" or "the bare kind". Verified against the real
# binary: unescaped, `/` as the very first character of a line is a hard
# compile error (`expected colon`, term-list syntax), and `-`/`+`/`=`
# silently change what renders (a bullet marker, a numbered marker, a
# heading) instead of the literal character.
_LINE_INITIAL_HAZARD = frozenset("-+/=")


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
    other. So this function does not try to guess intent at all: every
    `\\` is doubled first (exactly like `typst_value`, for the same
    reason -- an un-doubled backslash hands Typst an escape sequence that
    silently consumes the next character), then every markup-significant
    character is escaped *unconditionally*, regardless of pairing or
    position, plus a line-initial list/heading marker (see
    `_LINE_INITIAL_HAZARD`). Escaping unconditionally rather than only
    "when it would otherwise break" is deliberate: a parity or position
    heuristic is exactly the kind of reasoning that missed `#` the first
    time around, and every character this function escapes round-trips
    to the identical literal character either way (see
    `tests/test_typst_escape.py`), so there is no correctness reason to
    make the narrower call.
    """
    text = text.replace("\\", "\\\\")
    out = [f"\\{ch}" if ch in _MARKUP_ALWAYS_ESCAPE else ch for ch in text]
    if out and out[0] in _LINE_INITIAL_HAZARD:
        out[0] = f"\\{out[0]}"
    return "".join(out)


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
