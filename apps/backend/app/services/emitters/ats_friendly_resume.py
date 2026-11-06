"""Deterministic emitter for `@preview/ats-friendly-resume:0.1.1`.

Pinned to that exact version for the same reason `basic_resume.py` pins
`0.2.9`: an emitter is written against one version's `src/resume.typ`, not
assumed valid for another.

This is the false-friend case the whole feature exists to guard against:
`ats-friendly-resume` is a fork of `basic-resume` that kept almost every
component's shape but quietly renamed two things an emitter written "by
analogy" to `basic_resume.py` would get wrong silently rather than loudly
(both confirmed against this package's own
`typst/packages/preview/ats-friendly-resume/0.1.1/src/resume.typ`, not
inferred from basic-resume's):

- `work(company:, role:, dates:, tech-used:, location:)` -- basic-resume's
  `work()` takes `title:`; this one takes `role:` for the same idea.
- The date helper is `dates-util(start-date:, end-date:)`, not
  `dates-helper(...)`. Same two-keyword shape, different function name --
  `_common.dates_expr`'s `helper` argument exists for exactly this.

`edu(institution:, location:, degree:, dates:)` and the overall "component
call, then bullets as plain markup" shape are unchanged from basic-resume.

**Loss, derived field by field over every `ResumeContent` field (not
copied from the plan that named this package):** every field maps
somewhere *except* `ProjectEntry.role` -- `project(name:, dates:,
tech-used:, url:)` has no `role` parameter at all in this package (unlike
basic-resume's own `project()`, which does), so there is nowhere for it to
go. `dropped` reports `"projects[].role"` only when some project in
`content` actually has one set (`emit`'s own final `any(...)` check) --
not unconditionally, since a resume with no project `role` has nothing to
lose there. `degraded` is empty -- there is no case here where content
survives in a lesser form rather than not at all.

`ProjectEntry.url` is **not** dropped, but getting there took a second
look (see the Task 9 fix report): `project()`'s own one-by-one-layout
only displays `url:` inside `if url != "" and dates != "" [...]` --
passing `url:` as an argument is not sufficient on its own when a project
has no dates at all (`GOLDEN.projects[1]` is exactly this case), so the
package's own rendering silently drops it even though the emitted source
looks correct. `_project_lines` below renders that case itself, as a
trailing link line, rather than declaring the loss -- the package *can*
display the URL, it is only this one vendor macro's conditional that
hides it, so avoiding the loss is the right answer per this module's own
design brief, not declaring it.

`Contact.website` is not dropped either: `resume()` has no `website:`
argument, but it does have `portfolio:`, which the same contact line
renders a link from. `summary`, `skills`, and `extras` are not dropped or
degraded: this package has no dedicated component for any of them, but
neither does basic-resume, and the same bare `== heading` +
markup-paragraph/bullet technique that lets `basic_resume.emit` report
zero losses for those three fields applies here unchanged -- `resume()`'s
`body` is plain Typst content, so an arbitrary heading works the same way
in both packages.

**Section order:** the plan's own note says "skills, experience,
education, projects", but this package's own vendor example
(`template/main.typ`) orders its sections skills, experience, *projects*,
then education -- this emitter follows the file, not the note, per this
task's own instruction to verify against source rather than trust a
written assertion. See the Task 9 report for this discrepancy.

Escaping follows `basic_resume.py`'s rule exactly: `typst_value` for a
quoted argument, `markup_value` for document prose.
"""

from __future__ import annotations

from app.services.emitters._common import (
    bullet_lines,
    dates_expr,
    font_literal,
    heading_block,
    skill_line,
    url_value,
)
from app.services.emitters.base import EmitResult
from app.services.resume_content import EducationEntry, ProjectEntry, ResumeContent, WorkEntry
from app.services.typst_escape import markup_value, typst_value

name = "ats-friendly-resume"
version = "0.1.1"

# This package's own fork of basic-resume's `contact-item(..., link-type:
# "https://")` slots (`src/resume.typ`) -- see basic_resume._URL_ARGS for
# why these three take `url_value` rather than `typst_value`.
_URL_ARGS = frozenset({"linkedin", "github", "portfolio"})


def _header_lines(content: ResumeContent) -> list[str]:
    contact = content.contact
    args: list[tuple[str, str]] = [("author", content.name or "")]
    for key, value in (
        ("location", contact.location),
        ("email", contact.email),
        ("phone", contact.phone),
        ("linkedin", contact.linkedin),
        ("github", contact.github),
        # resume() has no `website:` argument -- `portfolio:` is this
        # package's own name for the same idea (see module docstring).
        ("portfolio", contact.website),
    ):
        if value:
            args.append((key, value))
    lines = ["#show: resume.with("]
    lines.extend(
        f"  {key}: {url_value(value) if key in _URL_ARGS else typst_value(value)},"
        for key, value in args
    )
    # color-enabled/text-color and paper are this emitter's own fixed
    # pins, not content -- never escaped, always this literal. Mirrors
    # basic_resume.py's accent-color/paper pins: same blue, same page
    # size, so a switch between these two templates does not also
    # silently change the resume's look.
    lines.append("  color-enabled: true,")
    lines.append('  text-color: "#26428b",')
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
    dates = dates_expr(entry.start, entry.end, helper="dates-util")
    if dates:
        lines.append(f"  dates: {dates},")
    if entry.degree:
        lines.append(f"  degree: {typst_value(entry.degree)},")
    lines.append(")")
    lines.extend(bullet_lines(entry.bullets))
    return lines


def _work_lines(entry: WorkEntry) -> list[str]:
    lines = ["#work("]
    if entry.company:
        lines.append(f"  company: {typst_value(entry.company)},")
    # work()'s own name for this is `role:`, not `title:` -- the other
    # half of the trap this emitter exists to prove (see module
    # docstring). `WorkEntry.title` has nowhere else to go.
    if entry.title:
        lines.append(f"  role: {typst_value(entry.title)},")
    dates = dates_expr(entry.start, entry.end, helper="dates-util")
    if dates:
        lines.append(f"  dates: {dates},")
    if entry.location:
        lines.append(f"  location: {typst_value(entry.location)},")
    lines.append(")")
    lines.extend(bullet_lines(entry.bullets))
    return lines


def _project_lines(entry: ProjectEntry) -> list[str]:
    # No `role:` here -- project() has no such parameter in this package
    # (see module docstring); ProjectEntry.role is this emitter's one
    # reported loss and is deliberately never referenced below.
    lines = ["#project("]
    if entry.name:
        lines.append(f"  name: {typst_value(entry.name)},")
    dates = dates_expr(entry.start, entry.end, helper="dates-util")
    if dates:
        lines.append(f"  dates: {dates},")
    if entry.url:
        lines.append(f"  url: {url_value(entry.url)},")
    lines.append(")")
    if entry.url and not dates:
        # project()'s own one-by-one-layout (resume.typ) only shows the
        # URL inside `if url != "" and dates != "" [...]` -- passing
        # `url:` above is not enough on its own when there are no dates
        # at all, the package's own layout simply never reaches that
        # branch. Rendered here instead, as a trailing line under the
        # project, using the same "insert the already-quoted string as
        # content via `#expr`" idiom the package's own `contact-item`/
        # `link(...)[#url]` calls use -- not `markup_value`, since this
        # is not markup prose, it is the identical raw-string display
        # the package's own `url` argument already is.
        url_literal = url_value(entry.url)
        lines.append(f'#link("https://" + {url_literal})[#{url_literal}]')
    lines.extend(bullet_lines(entry.bullets))
    return lines


def emit(content: ResumeContent) -> EmitResult:
    lines: list[str] = [f'#import "@preview/{name}:{version}": *', ""]
    lines.extend(_header_lines(content))
    lines.append("")

    lines.extend(
        heading_block("Summary", [markup_value(content.summary)] if content.summary else [])
    )

    skill_body = [line for line in (skill_line(group) for group in content.skills) if line]
    lines.extend(heading_block("Skills", skill_body))

    work_body: list[str] = []
    for entry in content.work:
        work_body.extend(_work_lines(entry))
        work_body.append("")
    lines.extend(heading_block("Experience", work_body))

    # Projects before education: the vendor example's own order, not the
    # plan's note -- see module docstring.
    project_body: list[str] = []
    for entry in content.projects:
        project_body.extend(_project_lines(entry))
        project_body.append("")
    lines.extend(heading_block("Projects", project_body))

    edu_body: list[str] = []
    for entry in content.education:
        edu_body.extend(_edu_lines(entry))
        edu_body.append("")
    lines.extend(heading_block("Education", edu_body))

    for section in content.extras:
        lines.extend(heading_block(section.title, bullet_lines(section.entries)))

    source = "\n".join(lines).rstrip() + "\n"
    # Computed from `content`, not a constant: a loss is a property of
    # what was actually handed in, not of the package in the abstract --
    # a resume with no project `role` set has nothing to lose there, and
    # a hardcoded tuple would go on claiming the loss anyway (see
    # test_ats_friendly_declares_no_project_role_loss_when_none_is_set).
    dropped = ("projects[].role",) if any(entry.role for entry in content.projects) else ()
    return EmitResult(source=source, dropped=dropped, degraded=())
