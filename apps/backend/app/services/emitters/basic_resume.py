"""Deterministic emitter for `@preview/basic-resume:0.2.9`.

Pinned to that exact version, never the index's latest (`0.2.10` as of this
writing): `app.services.starter._render` and `app.services.llm.SYSTEM_PROMPT`
both ship `0.2.9`, and the two versions' `src/resume.typ` differ, so an
emitter written against one is not safe to assume valid for the other --
see `app/services/emitters/__init__.py`'s module docstring.

This is also the round-trip identity case: `basic-resume` is the starter's
own template, its `resume.with(...)` and `edu`/`work`/`project` helpers
(`typst/packages/preview/basic-resume/0.2.9/src/resume.typ`) take a named
argument for every field `ResumeContent` has, and a plain markup paragraph
under a `== heading` -- which is all a `#show: resume.with(...)` body ever
needs -- covers `summary`, `skills` and `extras` (none of which the package
gives a dedicated component). So `emit()` never has anything to report in
`dropped` or `degraded`: there is nowhere in this IR that this package
cannot hold.

Every user-supplied string goes through `app.services.typst_escape`:
`typst_value` for anything that becomes a quoted argument (`author:`,
`dates-helper(start-date: ...)`, ...), `escape_typst_markup` for anything
that becomes document prose (bullets, headings, the summary paragraph) --
see that module for why the two need different rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from app.services.resume_content import (
    EducationEntry,
    ProjectEntry,
    ResumeContent,
    SkillGroup,
    WorkEntry,
)
from app.services.typst_escape import escape_typst_markup, typst_value

if TYPE_CHECKING:
    # Type-checking only: `__init__.py` imports this module to build the
    # registry, so a real module-level import the other way would be
    # circular -- see that module's docstring. `emit()` imports the actual
    # class lazily, at call time, long after both modules have finished
    # loading.
    from app.services.emitters import EmitResult

name = "basic-resume"
version = "0.2.9"

# The package's own default, left un-set, is "New Computer Modern" too --
# but relying on an un-set default is exactly what lets a *different*
# package's un-set default end up asking for Calibri (see the __init__
# module docstring), so this emitter names a font explicitly either way.
# "Noto Serif CJK TC" mirrors `app.services.starter.PLACEHOLDERS`' own
# fallback locale (`generate_starter` defaults an unmatched locale to
# zh-TW) for any content this emitter detects as carrying CJK text.
_LATIN_FONT = "New Computer Modern"
_CJK_FONT = "Noto Serif CJK TC"
_CJK_RE = re.compile("[一-鿿㐀-䶿]")


def _walk_strings(value: object) -> Iterator[str]:
    """Every string nested anywhere inside a `model_dump()`-shaped value --
    used only to decide the font (see `_has_cjk`), so walking the whole
    document once is simpler and no less correct than naming every field
    that could carry CJK text one at a time."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _has_cjk(content: ResumeContent) -> bool:
    return any(_CJK_RE.search(text) for text in _walk_strings(content.model_dump()))


def _font_for(content: ResumeContent) -> str:
    return _CJK_FONT if _has_cjk(content) else _LATIN_FONT


def _dates_expr(start: str | None, end: str | None) -> str | None:
    """A `dates-helper(...)` call, or `None` when there is nothing to date
    -- omitting the argument entirely keeps `#edu`/`#work`/`#project` from
    rendering a bare em dash when neither end of the range is known."""
    if not start and not end:
        return None
    return (
        f"dates-helper(start-date: {typst_value(start or '')}, "
        f"end-date: {typst_value(end or '')})"
    )


def _bullet_lines(bullets: list[str]) -> list[str]:
    return [f"- {escape_typst_markup(bullet)}" for bullet in bullets if bullet]


def _header_lines(content: ResumeContent) -> list[str]:
    contact = content.contact
    args: list[tuple[str, str]] = [("author", content.name or "")]
    for key, value in (
        ("location", contact.location),
        ("email", contact.email),
        ("github", contact.github),
        ("linkedin", contact.linkedin),
        ("phone", contact.phone),
        ("personal-site", contact.website),
    ):
        if value:
            args.append((key, value))
    lines = ["#show: resume.with("]
    lines.extend(f"  {key}: {typst_value(value)}," for key, value in args)
    # accent-color and paper are the starter's own pins (services.starter
    # ._render), not content -- never escaped, always this literal.
    lines.append('  accent-color: "#26428b",')
    lines.append(f"  font: {typst_value(_font_for(content))},")
    lines.append('  paper: "a4",')
    lines.append(")")
    return lines


def _edu_lines(entry: EducationEntry) -> list[str]:
    lines = ["#edu("]
    if entry.institution:
        lines.append(f"  institution: {typst_value(entry.institution)},")
    if entry.location:
        lines.append(f"  location: {typst_value(entry.location)},")
    dates = _dates_expr(entry.start, entry.end)
    if dates:
        lines.append(f"  dates: {dates},")
    if entry.degree:
        lines.append(f"  degree: {typst_value(entry.degree)},")
    lines.append(")")
    lines.extend(_bullet_lines(entry.bullets))
    return lines


def _work_lines(entry: WorkEntry) -> list[str]:
    lines = ["#work("]
    if entry.title:
        lines.append(f"  title: {typst_value(entry.title)},")
    if entry.company:
        lines.append(f"  company: {typst_value(entry.company)},")
    if entry.location:
        lines.append(f"  location: {typst_value(entry.location)},")
    dates = _dates_expr(entry.start, entry.end)
    if dates:
        lines.append(f"  dates: {dates},")
    lines.append(")")
    lines.extend(_bullet_lines(entry.bullets))
    return lines


def _project_lines(entry: ProjectEntry) -> list[str]:
    lines = ["#project("]
    if entry.name:
        lines.append(f"  name: {typst_value(entry.name)},")
    if entry.role:
        lines.append(f"  role: {typst_value(entry.role)},")
    if entry.url:
        lines.append(f"  url: {typst_value(entry.url)},")
    dates = _dates_expr(entry.start, entry.end)
    if dates:
        lines.append(f"  dates: {dates},")
    lines.append(")")
    lines.extend(_bullet_lines(entry.bullets))
    return lines


def _skill_line(group: SkillGroup) -> str | None:
    category = escape_typst_markup(group.category) if group.category else ""
    items = ", ".join(escape_typst_markup(item) for item in group.items if item)
    if not category and not items:
        return None
    if category and items:
        return f"- *{category}*: {items}"
    return f"- {category or items}"


def emit(content: ResumeContent) -> EmitResult:
    # Deferred: see app/services/emitters/__init__.py's module docstring on
    # why this import cannot sit at module level.
    from app.services.emitters import EmitResult

    lines: list[str] = [f'#import "@preview/{name}:{version}": *', ""]
    lines.extend(_header_lines(content))
    lines.append("")

    if content.summary:
        lines.append("== Summary")
        lines.append("")
        lines.append(escape_typst_markup(content.summary))
        lines.append("")

    if content.education:
        lines.append("== Education")
        lines.append("")
        for entry in content.education:
            lines.extend(_edu_lines(entry))
            lines.append("")

    if content.work:
        lines.append("== Experience")
        lines.append("")
        for entry in content.work:
            lines.extend(_work_lines(entry))
            lines.append("")

    if content.projects:
        lines.append("== Projects")
        lines.append("")
        for entry in content.projects:
            lines.extend(_project_lines(entry))
            lines.append("")

    if content.skills:
        skill_lines = [line for line in (_skill_line(group) for group in content.skills) if line]
        if skill_lines:
            lines.append("== Skills")
            lines.append("")
            lines.extend(skill_lines)
            lines.append("")

    for section in content.extras:
        entry_lines = [f"- {escape_typst_markup(e)}" for e in section.entries if e]
        if not entry_lines:
            continue
        lines.append(f"== {escape_typst_markup(section.title)}")
        lines.append("")
        lines.extend(entry_lines)
        lines.append("")

    source = "\n".join(lines).rstrip() + "\n"
    return EmitResult(source=source, dropped=(), degraded=())
