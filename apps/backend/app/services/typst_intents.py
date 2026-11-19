"""Turn an edit intent into new Typst source.

Pure: source in, source out. No database, no compile; the caller owns the hash check, the
compile gate and the revision. All Typst writing goes through `serialize_unit`, which
validates marks and applies this unit's own escape policy.
"""

import hashlib
import re
from dataclasses import replace

from app.schemas_editing import IntentIn
from app.services.emitters import emitter_for
from app.services.resume_content import (
    Contact, EducationEntry, ProjectEntry, ResumeContent, Section as ExtraSection,
    WorkEntry)
from app.services.typst_projection import (
    POSITIONAL, Block, Mark, Projection, Section, Unit, _Arg, _args, _close_of,
    _HASH_CALL, line_role, line_unit, parse_projection, serialize_unit)

# The words a new entry is written with, when the request does not bring its own. Both
# are *content*, not Typst, so the locale holding the person's own words owns them and an
# intent may carry them (`IntentIn.placeholder` / `.title`, through `_word`): the defaults
# below are CJK, and an English-locale résumé was getting 「（待填）」 in its `url:` and
# date arguments, where it is easy to miss before export. The design only ever showed the
# CJK form, so this is a gap in it rather than a deviation from it.
PLACEHOLDER = "（待填）"
NEW_SECTION_TITLE = "新段落"

# What precedes a unit's text on its line, by role. The marker is part of the line the
# parser reads, so a candidate is judged with it, as it will be written.
_PREFIX = {"heading": "== ", "bullet": "- ", "para": ""}


class IntentError(Exception):
    """A refusal, with a machine-readable reason where one sentence will not do.

    `code` is the three the design names (`stale_hash` is the router's). `detail` is for
    a person reading a log; `reason` is for the client, which has to word the refusal in
    the person's language and cannot parse an English sentence to do it. `unsupported`
    covers four quite different things for `insert_item` alone -- an entry holding
    positional text that cannot be blanked, a template with no emitter, a template whose
    entries put fields where the form cannot reach them, and a section with nothing to
    copy -- and the panel was showing the first one's sentence for all four.
    """

    def __init__(self, code: str, detail: str = "", reason: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code  # "expect_mismatch" | "unsupported"
        self.detail = detail
        self.reason = reason


_BLANKS = re.compile(r"\n{3,}")


def normalize_blanks(source: str, at: int | None = None) -> str:
    """Collapse runs of three or more newlines to two, and end with a newline.

    With `at`, only the run touching that offset is collapsed: a structural edit may only
    tidy the seam it made, never rewrite blank lines elsewhere in the person's file (a
    multi-line string literal can hold them). Without it, the whole document is tidied.
    """
    if at is None:
        out = _BLANKS.sub("\n\n", source)
    else:
        at = min(at, len(source))
        lo = at
        while lo > 0 and source[lo - 1] == "\n":
            lo -= 1
        hi = at
        while hi < len(source) and source[hi] == "\n":
            hi += 1
        out = source[:lo] + ("\n\n" if hi - lo >= 3 else source[lo:hi]) + source[hi:]
    return out if out.endswith("\n") or not out else out + "\n"


def _word(given: str | None, what: str, default: str) -> str:
    """One word this request wants written into the document, or the server's default.

    Validated like any other value, because it ends up in the person's source: one line,
    and something left after a strip. Nothing else is checked -- it is content, and
    `serialize_unit` owns the escaping of whatever it says.
    """
    if given is None:
        return default
    if "\n" in given or "\r" in given:
        raise IntentError("unsupported", f"a {what} is one line")
    if not given.strip():
        raise IntentError("unsupported", f"a {what} cannot be empty")
    return given


def _ph(intent: IntentIn) -> str:
    return _word(intent.placeholder, "placeholder", PLACEHOLDER)


def _title(intent: IntentIn) -> str:
    return _word(intent.title, "section title", NEW_SECTION_TITLE)


def _unit(projection: Projection, intent: IntentIn, kind: str) -> Unit:
    unit = projection.units.get(intent.unit) if intent.unit is not None else None
    if unit is None:
        raise IntentError("unsupported", f"no such unit {intent.unit!r}")
    if unit.kind != kind:
        raise IntentError("unsupported", f"{intent.kind} needs a {kind} unit")
    if not unit.editable:
        raise IntentError("unsupported", f"unit {unit.id} is read-only")
    if intent.expect is None:
        raise IntentError("unsupported", f"{intent.kind} needs expect")
    if intent.expect != unit.value:
        raise IntentError("expect_mismatch", f"unit {unit.id} has changed")
    return unit


def _splice(source: str, unit: Unit, new: str) -> str:
    start, end = unit.span
    # The projection must describe this very source; a stale one would write elsewhere.
    if not 0 <= start <= end <= len(source) or source[start:end] != serialize_unit(unit):
        raise IntentError("unsupported", "the projection does not match the source")
    return source[:start] + new + source[end:]


def _check_reparse(unit: Unit, text: str, new: str) -> None:
    """Refuse text that, written, would not come back as this same unit. The parser is
    asked; there is no list of openers here to drift from it."""
    prefix = _PREFIX.get(unit.role or "")
    if prefix is None:
        raise IntentError("unsupported", f"a {unit.role} line cannot be set")
    back = line_unit(prefix + new)
    if (back is None or back.role != unit.role or back.value != text.strip()
            or not back.editable):
        raise IntentError("unsupported", "the text would re-parse as structure")
    # After `- ` the parser never classifies a marker (it only looks for `/ ` and `=` at
    # the start of a line), yet Typst nests a list, term list or heading there. So a
    # bullet's text must, standing alone, be a plain paragraph. A heading body is
    # inline-only, so this does not apply to it.
    if unit.role == "bullet" and line_role(new) != "para":
        raise IntentError("unsupported", "the text would re-parse as structure")


def _find_section(projection: Projection, intent: IntentIn) -> Section:
    sec = next((x for x in projection.sections if x.id == intent.target), None)
    if sec is None:
        raise IntentError("unsupported", f"no such section {intent.target!r}")
    return sec


def _find_block(
    projection: Projection, intent: IntentIn
) -> tuple[Section, Block, Block | None]:
    """The target block, its section, and its parent block when it is a bullet of one."""
    for sec in projection.sections:
        for block in sec.blocks:
            if block.id == intent.target:
                return sec, block, None
            for bullet in block.bullets:
                if bullet.id == intent.target:
                    return sec, bullet, block
    raise IntentError("unsupported", f"no such block {intent.target!r}")


def _contentful(sec: Section) -> list[Block]:
    return [b for b in sec.blocks if b.kind != "show"]


_EXPLICIT_NUMBER = re.compile(r"(\s*)\d+\.(\s*)")


def _fresh_marker(marker: str) -> str:
    """The marker for a new sibling of a line that starts with `marker`. An explicit
    number is not copied: `1.` twice renders "1." and "1.", while `+` after `1.` renders
    "1." and "2."."""
    m = _EXPLICIT_NUMBER.fullmatch(marker)
    return f"{m.group(1)}+{m.group(2) or ' '}" if m else marker


def _marker(source: str, projection: Projection, bullets: tuple[Block, ...]) -> str:
    """What precedes a bullet's text, copied from the sibling it joins: `+ ` is an
    enumeration, not a bullet list, and indentation is part of the shape."""
    if not bullets:
        return "- "
    last = bullets[-1]
    return _fresh_marker(source[last.outer[0]:projection.units[last.unit].span[0]])


def _insert_bullet(source: str, projection: Projection, intent: IntentIn) -> str:
    _, block, parent = _find_block(projection, intent)
    if parent is not None or block.kind != "comp":
        raise IntentError("unsupported", "a bullet is added to an item, not to a bullet")
    pos = block.content_end
    lead = "\n" if pos and source[pos - 1] != "\n" else ""
    new = f"{_marker(source, projection, block.bullets)}{_ph(intent)}\n"
    out = source[:pos] + lead + new + source[pos:]
    return normalize_blanks(out, at=pos + len(lead) + len(new))


def _has_positional(source: str, block: Block) -> bool:
    """Whether the call holds a positional string or content block. The projection cannot
    show it, so a copy would carry the previous entry's text into the new one."""
    call = _HASH_CALL.match(source, block.outer[0])
    if call is None:
        return False
    open_idx = call.end() - 1
    found: list[_Arg] = []
    _args(source, open_idx, _close_of(source, open_idx), found)
    return any(a.key == POSITIONAL for a in found)


def _blank_item(source: str, projection: Projection, last: Block, ph: str) -> str:
    """A copy of `last` with its text replaced, without the blank lines that follow it."""
    if last.kind == "comp":
        if _has_positional(source, last):
            raise IntentError(
                "unsupported",
                "this template's entries carry unnamed values that cannot be blanked",
                "positional")
        # the call itself: up to the first bullet or comment that follows it
        end = min([b.outer[0] for b in last.bullets] + [n[0] for n in last.notes]
                  + [last.content_end])
        start = last.outer[0]
        cuts = []
        for uid in last.args:
            unit = projection.units[uid]
            # a name bound by a `#let` outside the call is shared: never rewritten here
            if start <= unit.span[0] and unit.span[1] <= end and not unit.style:
                cuts.append(unit.span)
        if last.body is not None and last.body[1] <= end:
            cuts.append(last.body)
        text = source[start:end]
        for a, b in sorted(set(cuts), reverse=True):
            text = text[:a - start] + ph + text[b - start:]
        if not text.endswith("\n"):
            text += "\n"
        if last.bullets:
            text += f"{_marker(source, projection, last.bullets)}{ph}\n"
    else:
        unit = projection.units[last.unit]
        marker = _fresh_marker(source[last.outer[0]:unit.span[0]])
        text = marker + ph + "\n"
    return text


_IMPORT = re.compile(r'#import\s+"@preview/([^:"/]+):([^"]+)"')

# What an empty section's heading says it holds, in the three languages the product
# ships. Checked in this order: "Project Experience" is projects, not work. A heading
# that matches none of them gets a bullet, which every emitter writes for a free-form
# section (skills, extras).
_KINDS = (
    ("project", ("project", "專案", "项目", "專題", "作品")),
    ("education", ("education", "academic", "學歷", "学历", "教育")),
    ("work", ("experience", "employment", "work", "career", "工作", "經歷", "经历", "實習")),
)


def _section_kind(title: str) -> str:
    low = title.lower()

    def has(word: str) -> bool:
        # an ASCII keyword is a whole word ("Coursework" is not work); CJK has no spaces
        return bool(re.search(rf"\b{word}", low)) if word.isascii() else word in low

    return next((k for k, words in _KINDS if any(has(w) for w in words)), "bullet")


_BREAK = re.compile(r"#(?:pagebreak|colbreak)\([^()]*\)")


def _tail(source: str, sec: Section) -> tuple[bool, int]:
    """Whether the section body holds nothing but whitespace, the blocks the projection
    shows (a `#show` rule) and page breaks, and where the first of those begins. A page
    break ends a section, it does not start one; a `#grid` the projection cannot see into
    is content."""
    pos, rest, starts = sec.body_start, [], [b.outer[0] for b in sec.blocks]
    for b in sorted(sec.blocks, key=lambda b: b.outer):
        rest.append(source[pos:b.outer[0]])
        pos = max(pos, b.outer[1])
    rest.append(source[pos:sec.outer[1]])
    text = "".join(rest)
    body = source[sec.body_start:sec.outer[1]]
    starts += [sec.body_start + m.start() for m in _BREAK.finditer(body)]
    return not _BREAK.sub("", text).strip(), min(starts, default=sec.body_start)


def _section_body(emitter, probe: ResumeContent) -> str:
    out = emitter.emit(probe).source
    head = re.search(r"^== .*\n+", out, re.M)
    return out[head.end():].rstrip("\n") + "\n" if head else ""


def _unreachable(body: str, ph: str) -> int:
    """How many placeholders in `body` sit where the projection offers no field: a
    positional argument, an embedded `#"..."` label, a bullet inside `details: [..]`."""
    doc = parse_projection(f'#import "@preview/x:1"\n\n== T\n\n{body}')
    reachable = sum(u.value.count(ph) for u in doc.units.values() if u.editable)
    return body.count(ph) - reachable


# What an entry may leave out, in the order they are given up.
_OPTIONAL = {
    "project": ("role", "url", "bullets", "location"),
    "work": ("bullets", "location"),
    "education": ("bullets", "location"),
}


def _emitter_snippet(source: str, kind: str, ph: str) -> str:
    """One entry of `kind` in the grammar of the template the document imports: the
    template's own emitter writes a one-entry document and its section body is taken.
    A field is kept only if its placeholder lands where the form can reach it; one the
    template writes into a blind spot is left out, and a snippet with nothing reachable
    is refused rather than written."""
    m = _IMPORT.search(source)
    emitter = emitter_for(m.group(1), m.group(2)) if m else None
    if emitter is None:
        raise IntentError(
            "unsupported", "there is no emitter for this template", "no_emitter")
    if kind == "bullet":
        probe = ResumeContent(name=ph, contact=Contact(),
                              extras=[ExtraSection(title=ph, entries=[ph])])
        return _checked(_section_body(emitter, probe), ph)
    entry = {
        "work": WorkEntry(title=ph, company=ph, location=ph, start=ph, end=ph,
                          bullets=[ph]),
        "education": EducationEntry(institution=ph, degree=ph, location=ph, start=ph,
                                    end=ph, bullets=[ph]),
        "project": ProjectEntry(name=ph, role=ph, url=ph, start=ph, end=ph, bullets=[ph]),
    }[kind]

    def build(e) -> str:
        field = {"work": "work", "education": "education", "project": "projects"}[kind]
        return _section_body(emitter, ResumeContent(
            name=ph, contact=Contact(), **{field: [e]}))

    def without(e, name: str):
        return e.model_copy(update={name: [] if name == "bullets" else None})

    body = build(entry)
    if kind == "project":
        # `role` only where the template gives it an argument of its own: acorn-resume
        # folds it into `name:`, which would read "（待填）, （待填）"
        bare = build(without(entry, "role"))
        if body.count("\n") <= bare.count("\n"):
            entry, body = without(entry, "role"), bare
    for name in _OPTIONAL[kind]:
        bad = _unreachable(body, ph)
        if not bad:
            break
        if getattr(entry, name, None):
            trial = without(entry, name)
            trial_body = build(trial)
            if _unreachable(trial_body, ph) < bad:
                entry, body = trial, trial_body
    if _unreachable(body, ph):
        raise IntentError(
            "unsupported", "this template's entries have fields the form cannot reach",
            "template_shape")
    return _checked(body, ph)


def _checked(body: str, ph: str) -> str:
    if ph not in body:
        raise IntentError("unsupported",
                          "this template's entries are not written as markup",
                          "template_shape")
    return body


def _insert_first_item(source: str, projection: Projection, sec: Section, ph: str) -> str:
    empty, pos = _tail(source, sec)
    if sec.title_unit is None or not empty:
        raise IntentError("unsupported", "the section has no item to copy the shape of",
                          "nothing_to_copy")
    snippet = _emitter_snippet(
        source, _section_kind(projection.units[sec.title_unit].value), ph)
    after = source[pos:].lstrip("\n")
    return (source[:pos].rstrip("\n") + "\n\n" + snippet
            + ("\n" if after else "") + after)


def _insert_item(source: str, projection: Projection, intent: IntentIn) -> str:
    sec = _find_section(projection, intent)
    blocks = _contentful(sec)
    if not blocks:
        return _insert_first_item(source, projection, sec, _ph(intent))
    last = blocks[-1]
    # The new item goes right after the last one's content, with the blank lines that
    # already separate its siblings between them; the last one's own trailing blanks stay
    # after the new item. So no existing item ends up spaced differently (a blank line
    # inside a tight list makes the whole list wide).
    pos = last.content_end
    prev = blocks[-2] if len(blocks) > 1 else None
    if prev and prev.outer[1] == last.outer[0]:
        gap = source[prev.content_end:last.outer[0]]
    else:
        # a lone item, or one set apart from its sibling by content the projection does
        # not show, has no adjacent pair to ask
        gap = "" if last.kind == "bullet" else "\n"
    lead = "\n" if pos and source[pos - 1] != "\n" else ""
    new = lead + gap + _blank_item(source, projection, last, _ph(intent))
    return normalize_blanks(source[:pos] + new + source[pos:], at=pos + len(new))


def _insert_section(source: str, projection: Projection, intent: IntentIn) -> str:
    # No `== ` section means append, whatever else is there. Guessing a heading convention
    # from `= ` / `=== ` lines cannot work: a document that never had `== ` sections and
    # one that had them until a delete look identical here, and refusing strands the second
    # in a dead end. A stray `== 新段落` is visible in the preview and can be deleted.
    lead = "" if not source or source.endswith("\n\n") else (
        "\n" if source.endswith("\n") else "\n\n")
    out = source + lead + f"== {_title(intent)}\n\n{_ph(intent)}\n\n"
    return normalize_blanks(out, at=len(source))


def _delete_section(source: str, projection: Projection, sec: Section) -> str:
    """Cut a whole section: the same `outer` cut the last-item delete makes, reached
    directly and in one intent. The preamble has no heading and is not a section the person
    sees. Everything in `outer` goes -- comments, `#let`s, `#pagebreak()`, a table -- not just
    what the form shows; the caller says so before it asks. The only headed section may go
    too, as the last-item delete already allows: `insert_section` needs no heading."""
    if not sec.title_unit:
        raise IntentError("unsupported", "the preamble before the first heading is fixed")
    start, end = sec.outer
    out = source[:start] + source[end:]
    if not out.strip():
        raise IntentError("unsupported", "that would leave an empty document")
    if end >= len(source):
        out = out.rstrip("\n") + "\n"
    return normalize_blanks(out, at=start)


def _delete(source: str, projection: Projection, intent: IntentIn) -> str:
    section = next((x for x in projection.sections if x.id == intent.target), None)
    if section is not None:
        return _delete_section(source, projection, section)
    sec, block, parent = _find_block(projection, intent)
    if block.kind == "show":
        raise IntentError("unsupported", "the document's show rule cannot be deleted")
    start, end = block.outer
    # The last item takes its heading with it, but only when nothing else lives in the
    # section: a table or `#pagebreak()` the projection does not show is still content.
    if (parent is None and sec.title_unit and _contentful(sec) == [block]
            and not source[sec.body_start:start].strip()
            and not source[end:sec.outer[1]].strip()):
        start, end = sec.outer
    elif parent is None:
        # Taking a tight sibling's trailing blank lines would close up the gap that
        # follows the list; they belong to the sibling now.
        i = sec.blocks.index(block)
        prev = sec.blocks[i - 1] if i else None
        if prev and prev.outer[1] == start == prev.content_end:
            end = block.content_end
    out = source[:start] + source[end:]
    if not out.strip():
        raise IntentError("unsupported", "that would leave an empty document")
    if end >= len(source):
        out = out.rstrip("\n") + "\n"
    return normalize_blanks(out, at=start)


def _move_section(source: str, projection: Projection, intent: IntentIn) -> str:
    if intent.dir not in ("up", "down"):
        raise IntentError("unsupported", "move_section needs dir up or down")
    sec = _find_section(projection, intent)
    if not sec.title_unit:
        raise IntentError("unsupported", "the preamble before the first heading is fixed")
    i = projection.sections.index(sec) + (-1 if intent.dir == "up" else 1)
    if not 0 <= i < len(projection.sections) or not projection.sections[i].title_unit:
        raise IntentError("unsupported", f"nothing to move {intent.dir} past")
    first, second = sorted((sec, projection.sections[i]), key=lambda x: x.outer[0])
    # Belt and braces: consecutive sections are contiguous by construction, so this is
    # not a case to build on.
    if first.outer[1] != second.outer[0]:
        raise IntentError("unsupported", "the sections are not adjacent in the source")
    a, b = source[slice(*first.outer)], source[slice(*second.outer)]
    a_body, b_body = a.rstrip("\n"), b.rstrip("\n")
    # Bodies trade places and the blank lines stay where they were, so moving a section
    # one way and back is the identity and the file's ending is never disturbed.
    swapped = b_body + a[len(a_body):] + a_body + b[len(b_body):]
    return source[:first.outer[0]] + swapped + source[second.outer[1]:]


_STRUCTURAL = {
    "insert_bullet": _insert_bullet,
    "insert_item": _insert_item,
    "insert_section": _insert_section,
    "delete": _delete,
    "move_section": _move_section,
}


def apply_intent(source: str, projection: Projection, intent: IntentIn) -> str:
    if intent.kind == "set_arg":
        unit = _unit(projection, intent, "arg")
        if intent.value is None:
            raise IntentError("unsupported", "set_arg needs a value")
        if "\n" in intent.value or "\r" in intent.value:
            raise IntentError("unsupported", "a value is one line")
        return _splice(source, unit, serialize_unit(replace(unit, value=intent.value)))
    if intent.kind == "set_line":
        unit = _unit(projection, intent, "line")
        text = intent.text
        if text is None:
            raise IntentError("unsupported", "set_line needs text")
        if text.strip() == "":
            # An emptied line is not projected, so every later unit id would shift.
            raise IntentError("unsupported", "set_line cannot empty a line; use delete")
        marks = tuple(Mark(m.kind, m.start, m.end, m.url) for m in intent.marks)
        # `escapes` is carried over from the unit being replaced
        candidate = replace(unit, value=text, marks=marks)
        try:
            new = serialize_unit(candidate)
        except ValueError as exc:
            raise IntentError("unsupported", str(exc)) from exc
        _check_reparse(unit, text, new)
        return _splice(source, unit, new)
    if intent.kind in _STRUCTURAL:
        if projection.hash != hashlib.sha256(source.encode("utf-8")).hexdigest():
            raise IntentError("unsupported", "the projection does not match the source")
        # Checked here rather than where they are used, so a malformed word is refused
        # before anything is written whichever structural intent carried it.
        _ph(intent)
        _title(intent)
        return _STRUCTURAL[intent.kind](source, projection, intent)
    raise IntentError("unsupported", f"{intent.kind} is not an intent")
