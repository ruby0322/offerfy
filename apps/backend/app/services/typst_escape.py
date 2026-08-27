"""Make LLM-written Typst more likely to compile.

Fill/edit turns often paste resume text with raw `"`, `$`, `@`, or `*` into
string fields and list items. Typst then treats them as syntax.
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
    lines = source.split("\n")
    return "\n".join(_sanitize_line(line) for line in lines)


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
