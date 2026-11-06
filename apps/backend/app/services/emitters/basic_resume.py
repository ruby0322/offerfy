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
`dates-helper(start-date: ...)`, ...), `markup_value` for anything that
becomes document prose (bullets, headings, the summary paragraph) -- see
that module for why data (never pre-escaped, no string literal to
protect) needs a different rule than already-written Typst source does.
"""

from __future__ import annotations

from app.services.emitters._common import bullet_lines, dates_expr, font_literal, heading_block
from app.services.emitters.base import EmitResult
from app.services.resume_content import (
    EducationEntry,
    ProjectEntry,
    ResumeContent,
    SkillGroup,
    WorkEntry,
)
from app.services.typst_escape import markup_value, typst_value

name = "basic-resume"
version = "0.2.9"


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
    lines.append(f"  font: {font_literal()},")
    lines.append('  paper: "a4",')
    lines.append(")")
    return lines


def _edu_lines(entry: EducationEntry) -> list[str]:
    lines = ["#edu("]
    if entry.institution:
        lines.append(f"  institution: {typst_value(entry.institution)},")
    if entry.location:
        lines.append(f"  location: {typst_value(entry.location)},")
    dates = dates_expr(entry.start, entry.end)
    if dates:
        lines.append(f"  dates: {dates},")
    if entry.degree:
        lines.append(f"  degree: {typst_value(entry.degree)},")
    lines.append(")")
    lines.extend(bullet_lines(entry.bullets))
    return lines


def _work_lines(entry: WorkEntry) -> list[str]:
    lines = ["#work("]
    if entry.title:
        lines.append(f"  title: {typst_value(entry.title)},")
    if entry.company:
        lines.append(f"  company: {typst_value(entry.company)},")
    if entry.location:
        lines.append(f"  location: {typst_value(entry.location)},")
    dates = dates_expr(entry.start, entry.end)
    if dates:
        lines.append(f"  dates: {dates},")
    lines.append(")")
    lines.extend(bullet_lines(entry.bullets))
    return lines


def _project_lines(entry: ProjectEntry) -> list[str]:
    lines = ["#project("]
    if entry.name:
        lines.append(f"  name: {typst_value(entry.name)},")
    if entry.role:
        lines.append(f"  role: {typst_value(entry.role)},")
    if entry.url:
        lines.append(f"  url: {typst_value(entry.url)},")
    dates = dates_expr(entry.start, entry.end)
    if dates:
        lines.append(f"  dates: {dates},")
    lines.append(")")
    lines.extend(bullet_lines(entry.bullets))
    return lines


def _skill_line(group: SkillGroup) -> str | None:
    category = markup_value(group.category) if group.category else ""
    items = ", ".join(markup_value(item) for item in group.items if item)
    if not category and not items:
        return None
    if category and items:
        return f"- *{category}*: {items}"
    return f"- {category or items}"


def emit(content: ResumeContent) -> EmitResult:
    lines: list[str] = [f'#import "@preview/{name}:{version}": *', ""]
    lines.extend(_header_lines(content))
    lines.append("")

    lines.extend(
        heading_block("Summary", [markup_value(content.summary)] if content.summary else [])
    )

    edu_body: list[str] = []
    for entry in content.education:
        edu_body.extend(_edu_lines(entry))
        edu_body.append("")
    lines.extend(heading_block("Education", edu_body))

    work_body: list[str] = []
    for entry in content.work:
        work_body.extend(_work_lines(entry))
        work_body.append("")
    lines.extend(heading_block("Experience", work_body))

    project_body: list[str] = []
    for entry in content.projects:
        project_body.extend(_project_lines(entry))
        project_body.append("")
    lines.extend(heading_block("Projects", project_body))

    skill_body = [line for line in (_skill_line(group) for group in content.skills) if line]
    lines.extend(heading_block("Skills", skill_body))

    for section in content.extras:
        lines.extend(heading_block(section.title, bullet_lines(section.entries)))

    source = "\n".join(lines).rstrip() + "\n"
    return EmitResult(source=source, dropped=(), degraded=())
