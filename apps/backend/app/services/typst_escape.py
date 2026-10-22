"""Make LLM-written Typst more likely to compile.

Fill/edit turns often paste resume text with raw `"`, `$`, `@`, or `*` into
string fields and list items. Typst then treats them as syntax.

Comments are left alone: escaping is meaningless inside `//` and `/* ... */`,
and template comments full of `name: ""` placeholders otherwise get rewritten
on every edit.
"""

from __future__ import annotations

import re

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
