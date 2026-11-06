"""Deterministic emitter for `@preview/acorn-resume:0.1.0`.

Pinned to that exact version for the same reason `basic_resume.py` pins
`0.2.9`: an emitter is written against one version's `src/lib.typ`, not
assumed valid for another.

This is the lossy case Task 8's plan singled this package out for: its
components have slots the IR's fields genuinely do not fit. Two losses,
both confirmed against this package's own `src/lib.typ`, not inferred by
analogy with `basic_resume.py`/`ats_friendly_resume.py`:

- `edu(degree:, date:, institution:, gpa:, location:)` has **no**
  `details` parameter at all, unlike `exp()`/`project()` (both of which
  do). Education bullets therefore have nowhere to go *inside* the
  component -- they are emitted as plain markup immediately after the
  `#edu(...)` call closes instead, which is `degraded` (`"education[].
  bullets"`): they still render, just without the indented/paragraph
  treatment a `details:` slot would have given them.
- `project(name:, technologies:, live-url:, repo-url:, details:)` has
  **no** date slot whatsoever -- not even a renamed one. A project's
  `start`/`end` are `dropped` (`"projects[].dates"`): there is nowhere in
  this component, or anywhere else in the document, to put a date range
  without inventing layout the package does not have.

Both are computed from `content` on every call, not returned as a
constant (see Task 9's own fix for why): a resume with no project dates,
or no education bullets, has nothing to lose in either place.

**Everything else renders, including two fields that have no dedicated
slot either but are not declared lost** -- the brief's own note that
avoiding a loss beats declaring one whenever the template can actually
display the content:

- `ProjectEntry.role`: `project()` has no `role` parameter. Folded into
  `name:` as `"<role>, <name>"` instead -- the exact `*role*, name`
  display `basic-resume`'s own `project()` component already uses
  natively for this same situation (see `basic-resume/0.2.9/src/
  resume.typ`'s `project()`), just achieved by string concatenation
  since this package has no second parameter to hold it. This is safe
  specifically because `name:` is a plain string argument the component
  interpolates as text -- no markup needs to be parsed out of it.
- `ProjectEntry.url`: `project()`'s own `live-url`/`repo-url` arguments
  exist, but its body never displays the literal url string for either
  one -- only fixed `"Live"`/`"GitHub"` labels (`link(live-url)[*Live*]`,
  `link(repo-url)[*GitHub*]`). Passing `url` into either slot would not
  satisfy this feature's own content-survival checks, which look for the
  literal url text on the rendered page. Rendered instead as a real
  `#link(...)` line inside `details:`, the identical idiom
  `ats_friendly_resume.py` uses for its own dateless-url fallback (see
  that module's docstring) -- content the package can genuinely display,
  just not through either of its two named slots for it.

`Contact.location`/`Contact.phone` are a smaller version of the same
question: `header(name:, contacts: ((url, label), ...), position:)` can
only render a contact as a hyperlink pair, and neither location nor a
phone number is a URL worth wrapping in a fabricated `link()` target.
Both are rendered instead as a plain centered line under `#header(...)`
-- a trailing line the template can already display, same as the
`url`/`role` cases above, just outside any component slot at all rather
than inside one.

`summary`, `skills`, and `extras` have no dedicated component in this
package either, but neither did they in `basic-resume`/`ats-friendly-
resume` -- the same bare `== heading` + markup paragraph/bullet
technique (`_common.heading_block`) applies unchanged.

Escaping follows `basic_resume.py`'s rule exactly: `typst_value` for a
quoted argument (`name:`, `role:`, `date:`, ...), `markup_value` for
document prose (bullets, the summary paragraph, the phone/location
line). `date_range_expr` (not `dates_expr`) builds the `date:` argument:
`acorn-resume` has no `dates-helper`-shaped function anywhere in
`src/lib.typ` at all -- its own vendor template builds `date:` by hand
as a bare string (`date: "Jun 2025 - Present"`) -- so calling
`dates_expr` here would emit a call to a function that does not exist in
this package's namespace. See `_common.date_range_expr`'s own docstring.

**The font matters more here than in either other emitter.** `resume()`
defaults to `font: "Calibri"`, which this host does not have -- its own
vendor example fails the support survey's font gate for exactly that
reason (`data/template_support.json` currently has `acorn-resume:0.1.0`
marked `ok: false`). `_common.font_literal()` is passed explicitly for
every document this emitter writes, same as the other two, which is what
lets a later survey run offer this pair once this emitter exists.
"""

from __future__ import annotations

from app.services.emitters._common import (
    bare_url,
    bullet_lines,
    date_range_expr,
    font_literal,
    heading_block,
    skill_line,
    url_value,
)
from app.services.emitters.base import EmitResult
from app.services.resume_content import EducationEntry, ProjectEntry, ResumeContent, WorkEntry
from app.services.typst_escape import markup_value, typst_value

name = "acorn-resume"
version = "0.1.0"


def _show_lines(content: ResumeContent) -> list[str]:
    return [
        "#show: resume.with(",
        f"  author: {typst_value(content.name or '')},",
        f"  font: {font_literal()},",
        ")",
    ]


def _header_lines(content: ResumeContent) -> list[str]:
    contact = content.contact
    # header(name:, contacts: ((url, label), ...)) -- contacts has no
    # none-guard before `.map` is called on it (same hazard class as
    # technologies below), so it is always a list, empty or not. Each
    # scheme (mailto:/https://) matches basic-resume's own `contact-item`
    # convention (resume.typ) for the same fields, not an invented one.
    pairs: list[tuple[str, str]] = []
    if contact.email:
        pairs.append((f"mailto:{contact.email}", contact.email))
    # `bare_url` on both halves: this emitter builds the `https://` target
    # itself, so a value that arrived carrying its own scheme would make
    # the pair `("https://https://github.com/x", "https://github.com/x")`
    # -- a dead hyperlink under a doubled label. See `_common.bare_url`.
    for value in (contact.github, contact.linkedin, contact.website):
        bare = bare_url(value)
        if bare:
            pairs.append((f"https://{bare}", bare))
    lines = [
        "#header(",
        f"  name: {typst_value(content.name or '')},",
        f"  contacts: {typst_value(pairs)},",
        ")",
    ]
    # phone/location are not URLs -- header()'s contacts can only render
    # a (url, label) hyperlink pair, and wrapping either in a fabricated
    # link target would be inventing structure the package does not
    # have. Rendered instead as a plain centered line, the same " | "
    # join style header()'s own contacts row uses.
    extra = [value for value in (contact.phone, contact.location) if value]
    if extra:
        joined = " | ".join(markup_value(value) for value in extra)
        lines.append(f"#align(center)[{joined}]")
    return lines


def _work_lines(entry: WorkEntry) -> list[str]:
    # exp(role:, date:, organization:, location:, details:) -- bullets go
    # through `details`, confirmed against lib.typ.
    lines = ["#exp("]
    if entry.title:
        lines.append(f"  role: {typst_value(entry.title)},")
    if entry.company:
        lines.append(f"  organization: {typst_value(entry.company)},")
    date = date_range_expr(entry.start, entry.end)
    if date:
        lines.append(f"  date: {date},")
    if entry.location:
        lines.append(f"  location: {typst_value(entry.location)},")
    bullets = bullet_lines(entry.bullets)
    if bullets:
        lines.append("  details: [")
        lines.extend(f"    {bullet}" for bullet in bullets)
        lines.append("  ],")
    lines.append(")")
    return lines


def _edu_lines(entry: EducationEntry) -> list[str]:
    # edu(degree:, date:, institution:, gpa:, location:) -- no `details`
    # parameter at all (confirmed against lib.typ), unlike exp()/
    # project(). Bullets are therefore plain markup after the call, the
    # degraded form -- see module docstring. `gpa:` has no ResumeContent
    # field to map from at all, so it is simply never passed.
    lines = ["#edu("]
    if entry.degree:
        lines.append(f"  degree: {typst_value(entry.degree)},")
    date = date_range_expr(entry.start, entry.end)
    if date:
        lines.append(f"  date: {date},")
    if entry.institution:
        lines.append(f"  institution: {typst_value(entry.institution)},")
    if entry.location:
        lines.append(f"  location: {typst_value(entry.location)},")
    lines.append(")")
    lines.extend(bullet_lines(entry.bullets))
    return lines


def _project_name(entry: ProjectEntry) -> str:
    # project() has no `role` parameter -- folded into `name:` instead of
    # declared lost, the same "role, name" combination basic-resume's own
    # project() already uses natively for this exact case. Safe because
    # `name:` is a plain string argument the component interpolates as
    # text, not markup -- no link or emphasis needs to survive inside it.
    if entry.role and entry.name:
        return f"{entry.role}, {entry.name}"
    return entry.role or entry.name or ""


def _project_lines(entry: ProjectEntry) -> list[str]:
    lines = ["#project("]
    project_name = _project_name(entry)
    if project_name:
        lines.append(f"  name: {typst_value(project_name)},")
    # technologies has no ResumeContent equivalent, but the body calls
    # `.map` on it unconditionally with no none-guard (confirmed against
    # lib.typ) -- omitting it raises "type none has no method map", so it
    # is always a list, empty here since the IR carries no tech stack.
    lines.append(f"  technologies: {typst_value([])},")
    details: list[str] = []
    if entry.url:
        # live-url/repo-url exist, but project()'s own body never shows
        # the literal url for either -- only fixed "Live"/"GitHub" labels
        # (see lib.typ). Rendered here instead as a real link, the same
        # idiom ats_friendly_resume.py's own dateless-url fallback uses.
        url_literal = url_value(entry.url)
        details.append(f'    #link("https://" + {url_literal})[#{url_literal}]')
    details.extend(f"    {bullet}" for bullet in bullet_lines(entry.bullets))
    if details:
        lines.append("  details: [")
        lines.extend(details)
        lines.append("  ],")
    lines.append(")")
    return lines


def emit(content: ResumeContent) -> EmitResult:
    lines: list[str] = [f'#import "@preview/{name}:{version}": *', ""]
    lines.extend(_show_lines(content))
    lines.append("")
    lines.extend(_header_lines(content))
    lines.append("")

    lines.extend(
        heading_block("Summary", [markup_value(content.summary)] if content.summary else [])
    )

    # Section order follows this package's own vendor example
    # (template/main.typ): Experience, Skills, Projects, Education --
    # not the plan's note, per this task's own instruction to verify
    # against the file rather than trust a written assertion (same call
    # Task 9 made for ats-friendly-resume).
    work_body: list[str] = []
    for entry in content.work:
        work_body.extend(_work_lines(entry))
        work_body.append("")
    lines.extend(heading_block("Experience", work_body))

    skill_body = [line for line in (skill_line(group) for group in content.skills) if line]
    lines.extend(heading_block("Skills", skill_body))

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

    # Computed from `content`, not a constant -- see module docstring and
    # Task 9's own precedent (ats_friendly_resume.emit).
    dropped = (
        ("projects[].dates",) if any(p.start or p.end for p in content.projects) else ()
    )
    degraded = (
        ("education[].bullets",) if any(e.bullets for e in content.education) else ()
    )
    return EmitResult(source=source, dropped=dropped, degraded=degraded)
