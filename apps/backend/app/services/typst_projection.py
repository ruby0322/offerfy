"""Project a Typst résumé onto the parts a form may edit, without ever writing Typst.

The form editor's contract is that the backend owns the grammar: the frontend sees
sections, component blocks and *values*, and every byte of Typst that changes is
produced here. That makes the parser's worst failure a quiet one -- reading Typst
syntax as content and offering it to a person as a text field. So the rules below
all lean the same way: anything this module cannot classify is counted in the
report and left alone, never promoted to a unit.

What counts as structure is a *shape*, not a name. `#ident(` with balanced
parentheses is a component block whether it is `#work`, `#edu` or a helper nobody
has an emitter for; a template's function names are not a list this module carries.
A block exists only if at least one of its arguments is editable; `#pagebreak()` and
`#grid(...)` are recorded in `report.structures` instead.

Continuation lines are the other trap. A construct that opens a bracket on one line
and closes it on a later one -- a multi-line `#let data = (...)` -- owns every line
in between. The line index therefore advances construct by construct, tracking
bracket depth and the code/markup distinction (a `(` inside `[...]` is prose, not
syntax; `#stars (4` with a space is prose too), so a continuation line can never be
read as a paragraph. A bracket that never closes swallows the rest of the file; that
is counted as `structures["unterminated"]`, never silent.

Line units are the content half. A `line` unit is one text range -- a bullet's text
after `- `, a bare paragraph line, a heading's title -- tokenised into text, `*strong*`,
`_emph_`, `#link("url")[body]` and `[group]` marks, and opaque runs (math, raw text,
`#call[...]`, labels, comments...) and flattened to plain `value` plus `marks`.
`serialize_unit` is the inverse and the only place Typst is written. Like `editable` for
an argument, a line unit's `editable` is *derived*: it is True only when
`serialize_unit(unit)` reproduces the source slice byte for byte. Anything this module
reads differently from how it would write it -- an opaque run, a stray `*`, an
intra-word `_` -- therefore falls out as read-only (and the document as Tier B) with no
list of special cases. Spans are offsets into the `str` that was passed in, so `\\r\\n`
sources and a missing final newline keep them exact.

`parse_projection` is a pure function of the source: no database, no models, no
caching (the parse is microseconds; see the design's decision 3).
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field, replace

# `skipped_args` key for content inside a recognised call that has no key to project it
# by: a positional string literal or content block (not a positional identifier).
POSITIONAL = "(positional)"
STYLE_KEYS = frozenset({"font", "paper", "lang", "margin", "size", "leading"})
_STYLE_SUFFIXES = ("-color", "-position", "-font")

_IDENT = r"[A-Za-z_][A-Za-z0-9_.-]*"
_LET = re.compile(rf"#let\s+({_IDENT})\s*=\s*")
_SHOW_CALL = re.compile(rf"#show:\s*({_IDENT})\s*\(")
_HASH_CALL = re.compile(rf"#({_IDENT})\s*\(")
_MARKUP_CALL = re.compile(rf"#{_IDENT}\(")  # in markup, no space before `(`
_HASH_NAME = re.compile(r"#[A-Za-z_][A-Za-z0-9_.-]*")
_KEYED = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*")
_CALL_VALUE = re.compile(rf"{_IDENT}\s*\(")
_BARE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# A list marker: `-`, `+` or `1.`, then whitespace or the end of the (stripped) line.
_BULLET = re.compile(r"[ \t]*(?:[-+]|\d+\.)(?:[ \t]+|$)")
_TERM = re.compile(r"[ \t]*/[ \t]")  # `/ Term: description`
# Indentation is part of the shape: a heading is a heading wherever the markup puts
# it, and inside an `#align(...)[...]` or `#block[...]` body it is normally indented.
# Anchoring this at column 0 (as it was) let `  == Nested` fall through to a `para`
# unit -- Typst syntax in a form field, and a value edit then wrote the heading away.
_EQ_HEADING = re.compile(r"[ \t]*=+[ \t]")
_LINK = re.compile(r'#link\("((?:[^"\\]|\\.)*)"\)\[')
_HASH_ID = re.compile(r"#[^\W\d]\w*(?:[.-][^\W\d]\w*)*")
_LABEL = re.compile(r"<[\w.:-]+>")
_REF = re.compile(r"@[\w.:-]+")

# Characters a line unit always writes escaped. `&` is deliberately absent: a bare `&`
# is legitimate markup, so a range that already spells `\&` records it in `escapes`.
_CANON = frozenset("\\#$*_[]<>@~`")


@dataclass(frozen=True)
class Mark:
    kind: str
    start: int
    end: int
    url: str | None = None


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str
    span: tuple[int, int]
    value: str
    editable: bool
    key: str | None = None
    style: bool = False
    role: str | None = None
    marks: tuple[Mark, ...] = ()
    opaque: tuple[str, ...] = ()
    escapes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Block:
    id: str
    kind: str
    name: str | None
    outer: tuple[int, int]
    content_end: int
    args: tuple[str, ...] = ()
    unit: str | None = None
    bullets: tuple["Block", ...] = ()
    notes: tuple[tuple[int, int], ...] = ()
    body: tuple[int, int] | None = None  # a `#ident(...)[...]` content block
    body_units: tuple[str, ...] = ()  # the line units inside that body


@dataclass(frozen=True)
class Section:
    id: str
    title_unit: str | None
    outer: tuple[int, int]
    body_start: int
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class Report:
    structures: dict[str, int]
    skipped_args: dict[str, int]
    lints: tuple[str, ...]


@dataclass(frozen=True)
class Projection:
    hash: str
    tier: str
    sections: tuple[Section, ...]
    units: dict[str, Unit]
    report: Report


def is_style_key(key: str) -> bool:
    """Layout settings (font, colour...) are editable but grouped apart in the form."""
    return key in STYLE_KEYS or key.endswith(_STYLE_SUFFIXES)


# --- bracket-aware scanning -------------------------------------------------------------


def _string_end(src: str, i: int) -> int:
    """Index just past the closing quote of the string literal opening at `i`."""
    n = len(src)
    i += 1
    while i < n:
        if src[i] == "\\":
            i += 2
        elif src[i] == '"':
            return i + 1
        else:
            i += 1
    return n


def _run(src: str, i: int, stack: list[str], until_close: bool) -> int:
    """Walk Typst from `i`, keeping a stack of open brackets and the mode each opened.

    Code mode (`(`, `{`, a construct's own text) has string literals and comments.
    Markup mode (`[`) does not: an apostrophe or a lone `(` there is just prose, and
    only `[`/`]`, escapes and an embedded `#call(` change the depth.

    With `until_close`, returns the index just past the bracket that empties the
    stack. Otherwise returns the index of the newline that ends the construct at
    depth zero (or `len(src)`). An unterminated construct swallows the rest of the
    source -- nothing after it can be read as prose -- but that is under-recognition,
    so the caller sees it as a non-empty `stack` on return and must report it.
    """
    n = len(src)
    while i < n:
        c = src[i]
        if not stack and c == "\n":
            return i
        if stack and stack[-1] == "markup":
            if c == "\\":
                i += 2
                continue
            if c == "[":
                stack.append("markup")
            elif c == "]":
                stack.pop()
            elif c == "#":
                m = _MARKUP_CALL.match(src, i)
                if m:
                    stack.append("code")
                    i = m.end()
                    continue
        else:
            if c == '"':
                i = _string_end(src, i)
                continue
            if src.startswith("//", i):
                j = src.find("\n", i)
                i = n if j == -1 else j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                i = n if j == -1 else j + 2
                continue
            if c in "({":
                stack.append("code")
            elif c == "[":
                stack.append("markup")
            elif c in ")}]" and stack:
                stack.pop()
        i += 1
        if until_close and not stack:
            return i
    return n


def _close_of(src: str, open_idx: int) -> int:
    """Index just past the bracket matching the opener at `open_idx`."""
    mode = "markup" if src[open_idx] == "[" else "code"
    return _run(src, open_idx + 1, [mode], True)


# --- line index -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    """One top-level construct: a source line, or the several a bracket spans."""

    kind: str  # blank | comment | head | code | text
    start: int
    end: int  # index of the newline that ends it, or len(src)
    unterminated: bool = False  # a bracket never closed: this construct ate the rest


def _index(src: str) -> list[_Line]:
    n = len(src)
    lines: list[_Line] = []
    i = 0
    while i < n:
        le = src.find("\n", i)
        le = n if le == -1 else le
        line = src[i:le]
        s = line.strip()
        unterminated = False
        if not s:
            kind, end = "blank", le
        elif s.startswith("//"):
            kind, end = "comment", le
        elif s.startswith("/*"):
            j = src.find("*/", i)
            if j == -1:
                end = n
            else:
                end = src.find("\n", j)
                end = n if end == -1 else end
            kind = "comment"
        elif line.startswith("== ") and line[3:].strip():
            kind, end = "head", le
        elif line.startswith("#"):
            kind = "code"
            stack: list[str] = []
            end = _run(src, i, stack, False)
            unterminated = bool(stack)
        else:
            kind, end = "text", le
        lines.append(_Line(kind, i, end, unterminated))
        i = end + 1
    return lines


# --- string arguments -------------------------------------------------------------------


@dataclass
class _Arg:
    key: str
    kind: str  # literal | ident | content | other
    start: int = 0  # literal: inner span
    end: int = 0
    ident: str = ""


def _unescape(slice_: str) -> str:
    return re.sub(r'\\(["\\])', r"\1", slice_)


def _escape(value: str) -> str:
    return re.sub(r'(["\\])', r"\\\1", value)


def _args(
    src: str, open_idx: int, close: int, out: list[_Arg], nested: bool = False
) -> None:
    """Collect `key: value` entries of the call whose `(` is at `open_idx`.

    A value that is itself a call (`dates: dates-helper(start-date: "...")`) is
    flattened one level, so its keyed arguments are collected as if they were the
    block's own. Positional arguments are ignored: they have no key to label a field.
    """
    end = close - 1
    i = open_idx + 1
    while i < end:
        if src[i] in " \t\r\n,":
            i += 1
            continue
        m = _KEYED.match(src, i)
        key = m.group(1) if m else None
        v = m.end() if m else i
        if v >= end:
            break
        if src[v] == '"':
            j = _string_end(src, v)
            if key:
                out.append(_Arg(key, "literal", v + 1, j - 1))
            else:  # `#cvitem("Role", "Company")`: content with no key to label it
                out.append(_Arg(POSITIONAL, "other"))
            i = j
            continue
        call = None if nested else _CALL_VALUE.match(src, v)
        if call:
            o = call.end() - 1
            cl = _close_of(src, o)
            _args(src, o, cl, out, nested=True)
            i = max(cl, v + 1)
            continue
        j = v
        while j < end and src[j] != ",":
            if src[j] == '"':
                j = _string_end(src, j)
            elif src[j] in "([{":
                j = _close_of(src, j)
            else:
                j += 1
        j = min(j, end)
        raw = src[v:j].strip()
        if not key:
            if src[v] == "[":  # a positional content block, e.g. a grid cell
                out.append(_Arg(POSITIONAL, "content"))
        else:
            if _BARE_IDENT.fullmatch(raw):
                out.append(_Arg(key, "ident", ident=raw))
            elif raw.startswith("["):
                # A KEYED content block (`details: [- a\n- b]`, how several templates
                # carry an entry's bullets). It is content the projection makes no unit
                # of, exactly like a positional one -- but it has a key, so the key
                # alone cannot tell it from a number or a style value. `kind` can.
                out.append(_Arg(key, "content"))
            else:
                out.append(_Arg(key, "other"))
        i = max(j, v + 1)


# --- line units: inline tokens and the serialiser ----------------------------------------


class _Out:
    """What `_inline` accumulates: plain text, mark ranges in pre-order, opaque runs."""

    def __init__(self) -> None:
        self.chars: list[str] = []
        self.marks: list[list] = []  # [kind, start, end, url], outermost first
        self.opaque: list[str] = []
        self.escapes: set[str] = set()


def _balanced(src: str, open_idx: int) -> int | None:
    """Index just past the bracket matching `open_idx`, or None if it never closes."""
    stack = ["markup" if src[open_idx] == "[" else "code"]
    j = _run(src, open_idx + 1, stack, True)
    return None if stack else j


def _hash_end(src: str, i: int, end: int) -> int | None:
    """End of the `#...` construct at `i`: `#name`, `#name(..)[..]`, `#(..)`, `#{..}`."""
    m = _HASH_ID.match(src, i)
    if m:
        j = m.end()
        while j < end and src[j] in "([":
            k = _balanced(src, j)
            if k is None or k > end:
                break
            j = k
        return j
    if i + 1 < end and src[i + 1] in "({":
        k = _balanced(src, i + 1)
        return k if k is not None and k <= end else None
    if i + 1 < end and src[i + 1] == '"':  # `#"text"`: a string embedded as code
        k = _string_end(src, i + 1)
        return k if k <= end and src[k - 1] == '"' and k - 1 > i + 1 else None
    return None


def _delimited_end(src: str, i: int, end: int) -> int | None:
    """End of the `$math$` or `` `raw` `` run opening at `i`, or None if it never closes."""
    if src[i] == "$":
        j = i + 1
        while j < end:
            if src[j] == "\\":
                j += 2
            elif src[j] == "$":
                return j + 1
            else:
                j += 1
        return None
    fence = re.compile(r"`+").match(src, i).group(0)
    j = src.find(fence, i + len(fence), end)
    return None if j == -1 else j + len(fence)


def _atom_end(src: str, j: int, end: int) -> int:
    """Skip one construct that may contain the character a mark is looking for."""
    c = src[j]
    if c == "\\":
        return min(j + 2, end)
    if c == "[":
        k = _balanced(src, j)
        return k if k is not None and k <= end else j + 1
    if c == "#":
        return _hash_end(src, j, end) or j + 1
    if c in "$`":
        return _delimited_end(src, j, end) or j + 1
    return j + 1


def _find_close(src: str, i: int, end: int, c: str) -> int | None:
    j = i + 1
    while j < end:
        if src[j] == c:
            # an `_` touching a letter on its right is inside a word, not a closer
            if c == "*" or not (j + 1 < end and src[j + 1].isalnum()):
                return j
            j += 1
        else:
            j = _atom_end(src, j, end)
    return None


def _inline(src: str, i: int, end: int, lo: int, out: _Out) -> None:
    """Tokenise `src[i:end]` into `out`. Anything unrecognised stays a text character,
    which `serialize_unit` will escape -- so the unit comes out read-only, never wrong."""

    def opaque(j: int) -> None:
        raw = src[i:j]
        out.opaque.append(raw)
        out.chars.extend(raw)

    def mark(kind: str, body: int, close: int, url: str | None = None) -> None:
        out.marks.append([kind, len(out.chars), None, url])
        slot = out.marks[-1]
        _inline(src, body, close, lo, out)
        slot[2] = len(out.chars)

    while i < end:
        c = src[i]
        nxt = i + 1
        if c == "\\":
            d = src[i + 1] if i + 1 < end else ""
            k = src.find("}", i, end) if d == "u" and src.startswith("u{", i + 1) else -1
            if k != -1:  # `\u{..}`
                opaque(k + 1)
                nxt = k + 1
            elif d == "" or d.isalnum() or d.isspace():  # a hard line break, or `\n`
                nxt = i + 2 if d else i + 1
                opaque(nxt)
            else:
                out.chars.append(d)
                # Every escape the range spells is recorded, so it is written back as
                # it was. A `/` is the exception only where the serializer itself writes
                # `\/`: the second of `//`, and one touching a `*`.
                # (the `/` before is bare only if an even run of backslashes precedes it:
                # `\\` is an escaped backslash, not an escape of the `/` after it)
                j = i - 2
                while j >= lo and src[j] == "\\":
                    j -= 1
                bare_slash = i > lo and src[i - 1] == "/" and (i - 2 - j) % 2 == 0
                canonical = d == "/" and (bare_slash or src.startswith("*", i + 2))
                if d not in _CANON and not canonical:
                    out.escapes.add(d)
                nxt = i + 2
        elif c in "*_":
            ok = c == "*" or not (i > lo and src[i - 1].isalnum())
            j = _find_close(src, i, end, c) if ok else None
            if j is not None and j > i + 1:
                mark("strong" if c == "*" else "emph", i + 1, j)
                nxt = j + 1
            else:
                out.chars.append(c)
        elif c == "[":
            k = _balanced(src, i)
            if k is not None and k <= end and k - 1 > i + 1:
                mark("group", i + 1, k - 1)
                nxt = k
            else:
                out.chars.append(c)
        elif c == "#":
            m = _LINK.match(src, i)
            k = _balanced(src, m.end() - 1) if m else None
            if m and k is not None and k <= end and k - 1 > m.end():
                mark("link", m.end(), k - 1, re.sub(r'\\(["\\])', r"\1", m.group(1)))
                nxt = k
            else:
                k = _hash_end(src, i, end)
                if k:
                    opaque(k)
                    nxt = k
                else:
                    out.chars.append(c)
        elif c in "$`":
            k = _delimited_end(src, i, end)
            if k:
                opaque(k)
                nxt = k
            else:
                out.chars.append(c)
        elif src.startswith("//", i):
            opaque(end)
            nxt = end
        elif src.startswith("/*", i):
            k = src.find("*/", i + 2, end)
            k = end if k == -1 else k + 2
            opaque(k)
            nxt = k
        elif c in "<@":
            m = (_LABEL if c == "<" else _REF).match(src, i)
            if m and m.end() <= end:
                opaque(m.end())
                nxt = m.end()
            else:
                out.chars.append(c)
        elif c == "~":
            opaque(nxt)
        else:
            out.chars.append(c)
        i = nxt


def _line_unit(src: str, uid: str, start: int, end: int, role: str) -> Unit:
    out = _Out()
    _inline(src, start, end, start, out)
    unit = Unit(
        uid, "line", (start, end), "".join(out.chars), False, role=role,
        marks=tuple(Mark(k, a, b, u) for k, a, b, u in out.marks),
        opaque=tuple(out.opaque), escapes=tuple(sorted(out.escapes)))
    # A document may already hold something `serialize_unit` refuses to write, such as
    # `#link("")[..]`: that unit cannot be rebuilt, so it is read-only, not an error.
    try:
        editable = serialize_unit(unit) == src[start:end]
    except ValueError:
        editable = False
    return replace(unit, editable=editable)


def _check_marks(unit: Unit) -> None:
    """Refuse marks that would emit unbalanced markup. The parser never produces them, but
    `serialize_unit` is the one write path and a client may supply marks."""
    n = len(unit.value)
    for m in unit.marks:
        if m.kind not in _MARK_KINDS:
            raise ValueError(f"unknown mark kind {m.kind!r}")
        if not 0 <= m.start < m.end <= n:
            raise ValueError(f"mark {m.kind} {m.start}..{m.end} is outside 0..{n}")
        if m.kind == "link":
            if m.url is None or not m.url.strip():
                raise ValueError("a link mark's url is missing")
            if "\n" in m.url or "\r" in m.url:
                raise ValueError("a link mark needs a one-line url")
    open_: list[Mark] = []
    for m in sorted(unit.marks, key=lambda m: (m.start, -m.end)):
        while open_ and open_[-1].end <= m.start:
            open_.pop()
        if open_ and m.end > open_[-1].end:
            raise ValueError("marks overlap without nesting")
        open_.append(m)


_MARK_KINDS = frozenset({"strong", "emph", "group", "link"})
_OPENERS = {"strong": "*", "emph": "_", "group": "["}


def serialize_unit(unit: Unit) -> str:
    """The unit's source text, rebuilt from `value`, `marks` and `escapes`.

    This is the only function that writes Typst, so it is deliberately blunt: the
    canonical set plus whatever this range already had escaped gets a backslash, `//`
    is written `/\\/`, and a `/` is never left next to a `*` (that would open a block
    comment). Opaque runs sit in `value` as their raw text and are escaped like any
    other text -- they cannot be rebuilt, which is exactly why a unit that has one
    does not round-trip and is read-only. Callers must check `unit.editable` first.
    """
    if unit.kind == "arg":
        return _escape(unit.value)
    value = unit.value
    if "\n" in value or "\r" in value:
        raise ValueError("a line unit is one line")
    _check_marks(unit)
    escaped = _CANON | frozenset(unit.escapes)
    order = sorted(range(len(unit.marks)),
                   key=lambda i: (unit.marks[i].start, -unit.marks[i].end, i))
    out: list[str] = []
    stack: list[Mark] = []
    slash = False  # the last piece written is a bare `/`

    def star() -> None:
        nonlocal slash
        if slash:
            out[-1] = "\\/"
            slash = False

    nxt = 0
    for p in range(len(value) + 1):
        while stack and stack[-1].end == p:
            m = stack.pop()
            if m.kind == "strong":
                star()
            out.append("]" if m.kind in ("link", "group") else _OPENERS[m.kind])
            slash = False
        while nxt < len(order) and unit.marks[order[nxt]].start == p:
            m = unit.marks[order[nxt]]
            nxt += 1
            if m.kind == "strong":
                star()
            if m.kind == "link":
                out.append(f'#link("{_escape(m.url or "")}")[')
            else:
                out.append(_OPENERS[m.kind])
            slash = False
            stack.append(m)
        if p == len(value):
            break
        ch = value[p]
        if ch == "/" and "/" in unit.escapes:  # this range escapes every `/` it holds
            out.append("\\/")
            slash = False
        elif ch == "/":
            if slash:
                out.append("\\/")
                slash = False
            else:
                out.append("/")
                slash = True
        else:
            out.append("\\" + ch if ch in escaped else ch)
            slash = False
    return "".join(out)


# --- projection -------------------------------------------------------------------------


@dataclass
class _RawBlock:
    kind: str
    name: str
    start: int
    content_end: int
    outer_end: int
    notes: list[tuple[int, int]]
    args: list[_Arg]
    unit: tuple[int, int] | None = None  # a loose bullet/paragraph: its text range
    body: tuple[int, int] | None = None
    bullets: list[tuple[int, int, int, int]] = field(default_factory=list)
    # each bullet: (line start, end of its line incl. newline, text start, text end)


@dataclass
class _RawSection:
    id: str
    start: int
    body_start: int
    blocks: list[_RawBlock]
    title: tuple[int, int] | None = None


def _let_literal(src: str, line: _Line) -> tuple[str, int, int] | None:
    """`#let name = "text"` and nothing else on the construct -> (name, inner span)."""
    m = _LET.match(src, line.start)
    if not m or m.end() >= line.end or src[m.end()] != '"':
        return None
    j = _string_end(src, m.end())
    if src[j - 1] != '"' or src[j : line.end].strip():
        return None
    return m.group(1), m.end() + 1, j - 1


def _text_range(src: str, line: _Line) -> tuple[int, int, int | None]:
    """(text start, text end, list-marker end) of a source line, indent and trailing
    whitespace (including a `\\r`) excluded. The text starts after the marker, so a
    marker-only line (`-   `) has an empty range and never a unit that covers the `-`."""
    text = src[line.start:line.end].rstrip()
    end = line.start + len(text)
    m = _BULLET.match(text)
    if m:
        return line.start + m.end(), end, line.start + m.end()
    return line.start + (len(text) - len(text.lstrip())), end, None


def _body_lines(
    src: str, body: tuple[int, int] | None, structures: Counter[str]
) -> list[tuple[int, int, str]]:
    """(text start, text end, role) of each line inside a `[...]` content body.

    List syntax is handled exactly as it is at the top level (`_text_range`): a list
    marker stays outside the unit, a marker-only line, a term-list item and a deeper
    heading are counted in `structures` and not projected.
    """
    if body is None:
        return []
    out = []
    pos = body[0]
    for text in src[body[0]:body[1]].split("\n"):
        line = _Line("text", pos, pos + len(text))
        pos += len(text) + 1
        if not text.strip():
            continue
        start, end, marker = _text_range(src, line)
        if marker is None and _EQ_HEADING.match(src, line.start):
            structures["= heading"] += 1
        elif marker is None and _TERM.match(src, line.start):
            structures["term list item"] += 1
        elif start >= end:
            structures["empty list item"] += 1
        else:
            out.append((start, end, "para" if marker is None else "bullet"))
    return out


def parse_projection(source: str) -> Projection:
    src = source
    n = len(src)
    lines = _index(src)
    structures: Counter[str] = Counter()
    skipped: Counter[str] = Counter()

    lets: dict[str, tuple[int, int]] = {}
    for line in lines:
        if line.kind == "code":
            found = _let_literal(src, line)
            if found:
                lets[found[0]] = (found[1], found[2])

    sections = [_RawSection("s0", 0, 0, [])]
    heads = 0
    k = 0
    while k < len(lines):
        line = lines[k]
        k += 1
        if line.kind == "head":
            heads += 1
            body = min(line.end + 1, n)
            t0 = line.start + 3
            t0 += len(src[t0:line.end]) - len(src[t0:line.end].lstrip())
            t1 = line.start + len(src[line.start:line.end].rstrip())
            sections.append(_RawSection(f"s{heads}", line.start, body, [], (t0, t1)))
            continue
        if line.kind == "comment":
            structures["comment"] += 1
            continue
        if line.kind == "text":
            start, end, marker = _text_range(src, line)
            if marker is None and _EQ_HEADING.match(src, line.start):
                structures["= heading"] += 1  # a heading level this form does not edit
                continue
            if marker is None and _TERM.match(src, line.start):
                structures["term list item"] += 1  # `Term: text` has syntax inside
                continue
            if start >= end:
                structures["empty list item"] += 1
                continue
            content_end = min(line.end + 1, n)
            outer_end = content_end
            j = k
            while j < len(lines) and lines[j].kind == "blank":
                outer_end = min(lines[j].end + 1, n)
                j += 1
            sections[-1].blocks.append(_RawBlock(
                "para" if marker is None else "bullet", "", line.start, content_end,
                outer_end, [], [], (start, end)))
            continue
        if line.kind != "code":
            continue
        if line.unterminated:
            # Everything from here to EOF belongs to this construct, so nothing in it
            # was classified. Say so rather than let the coverage report look whole.
            structures["unterminated"] += 1
            continue

        head = _SHOW_CALL.match(src, line.start)
        call = head or _HASH_CALL.match(src, line.start)
        if not call:
            if _LET.match(src, line.start) and not _let_literal(src, line):
                structures["#let"] += 1
            elif not _LET.match(src, line.start):
                token = _HASH_NAME.match(src, line.start)
                structures[token.group(0) if token else "#"] += 1
            continue

        open_idx = call.end() - 1
        args: list[_Arg] = []
        call_end = _close_of(src, open_idx)
        _args(src, open_idx, call_end, args)
        body = None
        if call_end < line.end and src[call_end] == "[":
            body_end = _balanced(src, call_end)
            if (body_end is not None and body_end <= line.end
                    and body_end - 1 > call_end + 1):
                body = (call_end + 1, body_end - 1)
        # A leaf is a string literal (even "") or a name bound to a literal #let.
        leaves = [a for a in args if _resolves(src, a, lets)]
        for a in args:
            if a not in leaves:
                skipped[a.key] += 1
        if not leaves and body is None:
            structures["#show" if head else f"#{call.group(1)}"] += 1
            continue

        content_end = min(line.end + 1, n)
        notes: list[tuple[int, int]] = []
        bullets: list[tuple[int, int, int, int]] = []
        while k < len(lines):
            nxt = lines[k]
            if nxt.kind == "comment":
                notes.append((nxt.start, min(nxt.end + 1, n)))
                structures["comment"] += 1
            elif nxt.kind == "text" and _text_range(src, nxt)[2] is not None:
                start, end, _ = _text_range(src, nxt)
                if start < end:
                    bullets.append((nxt.start, min(nxt.end + 1, n), start, end))
                else:
                    structures["empty list item"] += 1
            else:
                break
            content_end = min(nxt.end + 1, n)
            k += 1
        outer_end = content_end
        j = k
        while j < len(lines) and lines[j].kind == "blank":
            outer_end = min(lines[j].end + 1, n)
            j += 1
        sections[-1].blocks.append(
            _RawBlock("show" if head else "comp", call.group(1), line.start,
                      content_end, outer_end, notes, leaves, bullets=bullets,
                      body=body)
        )

    return _build(src, sections, lets, structures, skipped)


def _resolves(src: str, arg: _Arg, lets: dict[str, tuple[int, int]]) -> bool:
    if arg.kind == "literal":
        return True
    if arg.kind == "ident":
        span = lets.get(arg.ident)
        return span is not None
    return False


def _build(src, raw_sections, lets, structures, skipped) -> Projection:
    n = len(src)
    units: dict[str, Unit] = {}
    let_units: dict[str, str] = {}

    def make_unit(key: str, start: int, end: int) -> str:
        uid = f"u{len(units) + 1}"
        slice_ = src[start:end]
        value = _unescape(slice_).replace("\r", "")
        units[uid] = Unit(uid, "arg", (start, end), value, _escape(value) == slice_,
                          key=key, style=is_style_key(key))
        return uid

    def make_line(start: int, end: int, role: str) -> str:
        uid = f"u{len(units) + 1}"
        units[uid] = _line_unit(src, uid, start, end, role)
        return uid

    sections: list[Section] = []
    used: set[str] = set()
    for si, rs in enumerate(raw_sections):
        if si == 0 and not rs.blocks:
            continue  # nothing before the first heading worth showing
        end = raw_sections[si + 1].start if si + 1 < len(raw_sections) else n
        title = make_line(*rs.title, "heading") if rs.title else None
        blocks = []
        for bi, rb in enumerate(rs.blocks):
            ids: list[str] = []
            for a in rb.args:
                if a.kind == "literal":
                    ids.append(make_unit(a.key, a.start, a.end))
                else:
                    used.add(a.ident)
                    if a.ident not in let_units:
                        let_units[a.ident] = make_unit(a.key, *lets[a.ident])
                    ids.append(let_units[a.ident])
            body_units = tuple(
                make_line(a, b, role)
                for a, b, role in _body_lines(src, rb.body, structures))
            bullets = tuple(
                Block(f"{rs.id}i{bi}b{bj}", "bullet", None, (ls, le), le,
                      unit=make_line(ts, te, "bullet"))
                for bj, (ls, le, ts, te) in enumerate(rb.bullets))
            blocks.append(Block(
                f"{rs.id}i{bi}", rb.kind, rb.name, (rb.start, rb.outer_end),
                rb.content_end, args=tuple(ids), bullets=bullets, notes=tuple(rb.notes),
                body=rb.body, body_units=body_units,
                unit=make_line(*rb.unit, "bullet" if rb.kind == "bullet" else "para")
                if rb.unit else None))
        outer_start = rs.start if si else 0
        sections.append(
            Section(rs.id, title, (outer_start, end), rs.body_start, tuple(blocks)))

    lints = []
    for name in lets:
        if name not in let_units:
            structures["#let"] += 1  # a literal nothing edits: still not silent
        if name not in used and not re.search(rf"#{re.escape(name)}(?![\w-])", src):
            lints.append(f"`#let {name}` is never used by any argument")

    editable = [u for u in units.values() if u.editable]
    if not editable:
        tier = "C"
    elif len(editable) < len(units) or any(
            n and not is_style_key(key) for key, n in skipped.items()):
        # Content the form cannot show is as much a Tier B reason as a unit it cannot
        # write: "A" must mean every content unit is editable.
        #
        # EVERY non-style skipped argument, not just `(positional)`. A skipped `details:`
        # was recorded in the report and then ignored here, so a document hiding a whole
        # entry's bullets still graded "A" -- the grade claimed full coverage while the
        # form showed half a résumé, and nothing failed. A style key (`font`, `margin`,
        # `accent-color`) is not content and never forced B; that part is unchanged.
        tier = "B"
    else:
        tier = "A"

    return Projection(
        hash=hashlib.sha256(src.encode("utf-8")).hexdigest(),  # == revisions.source_hash
        tier=tier,
        sections=tuple(sections),
        units=units,
        report=Report(dict(structures), dict(skipped), tuple(lints)),
    )


def line_unit(line: str) -> Unit | None:
    """The one line unit the parser projects from this single source line, or None.

    None means the parser would not project it as a line: a `=` heading, a term-list item,
    an empty list item, a comment, a `#` call. This asks the parser itself rather than
    keeping a second list of what counts as structure, so a guard built on it cannot
    drift from it.
    """
    if "\n" in line or "\r" in line:
        return None
    found = [u for u in parse_projection(line).units.values() if u.kind == "line"]
    return found[0] if len(found) == 1 else None


def line_role(line: str) -> str | None:
    """What the parser classifies this source line as: `heading`, `bullet`, `para`, or
    None when it is not projected as a line."""
    unit = line_unit(line)
    return unit.role if unit else None
