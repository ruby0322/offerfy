"""Deterministic emitter for `@preview/clean-print-cv:0.1.0`.

Pinned to that exact version for the same reason every other emitter in
this package is: written against this version's own
`template/main.typ` and `cv-template.typ`, not assumed valid for another.

**The dict-shaped case.** `basic-resume`, `ats-friendly-resume`, and
`acorn-resume` are all "call-shaped": the emitter writes
`#work(title: "...", ...)` and the package's own component unpacks the
keyword arguments. `clean-print-cv` is different -- its entrypoint
(`template/main.typ`) builds one `data` dict from `yaml("cv-data.yaml")`
and hands whole sub-dicts to its section functions
(`cv-header(data.personal)`, `cv-experience(data.experience)`, ...), so
this emitter does the same: build a plain Python dict shaped exactly
like `cv-data.yaml`'s own schema, serialize the whole thing in one shot
with `typst_value`, and call only the section functions that have data.
Confirmed against `cv-template.typ`'s own section functions, not
inferred by analogy with the call-shaped emitters:

- `cv-header(personal)` reads `personal.name/title/email/phone/location/
  linkedin/github` with **no** `.at(default:)` guard on any of those --
  a missing key is a hard "dictionary does not contain key" error, so
  `personal` always carries all seven, even when empty. `website` is the
  one exception (`personal.at("website", default: "")`), included anyway
  for symmetry.
- `cv-skills(skills)` reads `group.category` and `group.items.join(...)`.
- `cv-experience(experience)` reads `exp.role/company/location/period`
  and iterates `exp.highlights` as a **list** (`#for item in
  exp.highlights`), each item interpolated directly -- a real list
  argument, not a joined string.
- `cv-projects(projects)` reads `proj.name/url/description` -- `url` is
  always displayed (`#proj.url`), never gated behind whether a date is
  present (unlike `ats-friendly-resume`'s `project()`), and `description`
  is a **single string**, not a list.
- `cv-education(education)` reads `edu.degree/period/institution/
  location` and `edu.at("details", default: "")` -- also a single
  string, not a list.
- `cv-certifications(certs)` and `cv-languages(languages)` read
  `cert.name/issuer/year` and `lang.language/level/dots` respectively.
  `ResumeContent` has no field for either -- see "Losses" below.

**Section calls are omitted, not just emptied, when there is no data.**
Every section function unconditionally renders its own
`section-heading(...)` (confirmed against `cv-template.typ`) with no
internal "skip if empty" guard of its own -- unlike `_common.
heading_block`'s markup idiom, calling `cv-experience(())` would still
print an empty "PROFESSIONAL EXPERIENCE" heading over nothing. So this
emitter checks `content.work`/`content.skills`/`content.projects`/
`content.education`/`content.summary` before emitting each call, the
same "heading only when there is a body" rule `heading_block` already
enforces for the call-shaped emitters, just applied at the call site
instead of inside a shared helper (there is no shared dict-shaped
section helper to reuse -- each one hangs its own `section-heading` call
differently, e.g. certifications' two-column layout vs. summary's bare
paragraph).

**Font.** `cv-page-setup(body)` is the one component in this whole
registry that does *not* take a `font:` argument -- it hardcodes
`set text(font: "New Computer Modern", ...)` internally (confirmed
against `cv-template.typ`), so `_common.font_literal()` cannot be passed
as a keyword the way every other emitter passes it. Instead this emitter
writes `#set text(font: font_literal())` as its own line immediately
after `#show: cv-page-setup`. This works because of how `#show: fn`
desugars in Typst: everything after that line becomes the *content*
handed to `cv-page-setup` as its `body` parameter, so the `set text(font:
...)` line is itself part of that content -- it runs *after*
`cv-page-setup`'s own internal `set text(font: "New Computer Modern")`
has already applied (since that happens before `body` is shown), and a
later `set` rule for the same property wins for everything that follows
it in the same scope. Verified against the real compiler: the CJK
fallback face renders with zero font warnings this way, exactly as the
other three emitters achieve by passing `font:` as an argument.

**Why `typst_value`, not `markup_value`, for every string in `data`.**
Every field above is displayed by the package's own functions via plain
code-mode interpolation (`#proj.description`, `#item`, `#personal.
title`) -- Typst shows a `str` value handed to it this way as literal
text content, with **no markup re-parsing at all**. That is a real
behavioral difference from the call-shaped emitters' bullets, which are
written as actual markup source (`- <text>`) and *do* get re-parsed (a
literal `*` there really can toggle bold, a `"` really does become a
smart quote) -- which is exactly why those use `markup_value`. Every
value that goes into `data` here only ever needs `typst_value`'s
string-literal escaping (backslash-doubling, quote-escaping); running it
through `markup_value` as well would double-escape markup characters
(`#`, `$`, `@`, ...) that this package's functions never interpret as
markup in the first place. `markup_value` is still used, via
`_common.heading_block`/`bullet_lines`, for the one thing this emitter
*does* write as real markup source: the `extras` section (see below).

**One hazard `typst_value` does not cover, that `markup_value` does.**
`typst_value` only doubles backslashes and escapes quotes -- unlike
`markup_value`, it never touches an embedded line-break character.
Confirmed against the real compiler: a raw `\\n` inside a quoted Typst
string literal is legal syntax (the string itself spans multiple source
lines), but it reaches the rendered PDF as a literal newline between the
two halves, not the single space `markup_value`'s own `_LINE_BREAK_RE`
normalization already guarantees for every call-shaped emitter's
bullets. Every other emitter in this registry never hits this because
every string it hands to `typst_value` is a short, structured argument
(a date, a name) rather than free-form prose -- this is the first
emitter to route prose (`summary`, bullets folded into `description`/
`details`) through `typst_value` instead of `markup_value`, because this
package displays prose via code-mode interpolation rather than markup.
`_text()` below closes that gap locally, by the same normalization
`markup_value` applies, duplicated rather than imported: `typst_escape.
_LINE_BREAK_RE` is a private module symbol, and the two functions
disagree on purpose (one must also escape markup characters, the other
must not), so sharing the pattern as a public export would be a bigger
change than this one emitter needs.

**Losses, derived field by field over every `ResumeContent` field** (not
copied from the brief, which had the right two losses but the wrong
*form* for one of them -- see the test's own comment):

- `projects[].dates` (`start`/`end`) -- **dropped**. `cv-projects`'
  `proj` schema has no date field anywhere (`name`, `url`, `description`
  only, confirmed against `cv-template.typ`); there is nowhere in this
  component to put a date range at all. Computed from `content` on every
  call, not a constant: a resume with no project dates has nothing to
  lose here.
- `projects[].bullets` -- **degraded**, into `description`: that slot is
  a single string, not a list, so a project's bullets are joined into
  one string rather than rendered as discrete items the way `exp.
  highlights` (a real list) preserves `WorkEntry.bullets`.
- `education[].bullets` -- **degraded**, into `details`, for the
  identical reason: `edu.details` is a single string too.
- Both degradations are also computed from `content`, not constants --
  a resume with no project bullets or no education bullets has nothing
  to lose in either place.
- `ProjectEntry.role` -- **not** dropped. `cv-projects`' `proj` schema
  has no `role` key either, but `name` is a plain data-dict string the
  template interpolates as text, not markup, so folding `role` into it
  (`"<role>, <name>"`, the identical idiom `acorn_resume.py` uses for
  this exact situation) avoids the loss rather than declaring it.
- Every `WorkEntry` field maps directly: `title` -> `role`, `company`,
  `location`, `start`/`end` -> `period` (a plain `"<start> – <end>"`
  string, per `cv-data.yaml`'s own example data), and `bullets` -> the
  real `highlights` list, with no join needed at all.
- Every `EducationEntry` field maps directly except `bullets` (above):
  `degree`, `institution`, `location`, `start`/`end` -> `period`.
- Every `Contact` field maps directly onto `personal`: `email`, `phone`,
  `location`, `github`, `linkedin`, `website` are all named keys `cv-
  header` already reads. Unlike `acorn-resume`, which has no slot for
  `phone`/`location` at all, this package's header has a dedicated key
  for both -- no loss, no need for the trailing-line fallback acorn
  needed.
- `personal.title` -- this package's own schema has a professional
  headline/subtitle slot (`cv-data.yaml`'s `"Ogre | Swamp Owner | ..."`)
  that `ResumeContent` simply has no field for *at all*, not even on
  `Contact` -- the mirror image of `certifications`/`languages` below.
  Emitted as `""` (required: `cv-header` reads `personal.title` with no
  default guard, so the key cannot be omitted). Not a loss: there is no
  `ResumeContent` field to drop, the same reasoning that keeps
  `certifications`/`languages` out of `dropped` too.
- `certifications`/`languages` -- no `ResumeContent` field for either,
  same as `personal.title`. Unlike `title`, nothing in `cv-header`/any
  other *called* function requires these keys to exist at all (they are
  each their own top-level component, called independently in the
  vendor example), so this emitter simply never calls `cv-certifications`
  / `cv-languages` and never puts either key in `data` -- not a
  placeholder empty list, just absent, since nothing reads it.
- `summary`, `skills`, `extras` -- see the per-field notes above
  (`summary`/`skills` have dedicated slots in this package, `extras`
  below does not).

**`extras` is rendered, not declared dropped.** This package has no
generic user-named-section function (no `cv-section(title, body)`
analogue), the same gap the original plan assumed would make `extras`
a loss here too. But Tasks 9 and 10 both found the same gap closeable:
a bare `== <title>` heading plus markup bullets -- `_common.
heading_block`/`bullet_lines`, unchanged -- renders as an ordinary Typst
heading and bullet list, with no dependency on `cv-page-setup`'s own
styling at all (`cv-page-setup` only sets `text`/`par`/`page`, no `show
heading` rule that would make a bare heading look out of place against
this package's own `section-heading()` accent-bar treatment). Verified
against the real compiler: it compiles cleanly and the heading/bullets
render legibly, just without the accent bar and hairline rule
`section-heading()` gives every *built-in* section -- a visual
inconsistency, not a broken or hidden one, and avoiding a loss beats
declaring one wherever the template can actually display the content
(this module's own design brief, Tasks 9-10's precedent). So `extras` is
appended after every other section, exactly like the other three
emitters, and reports nothing in `dropped`/`degraded`.
"""

from __future__ import annotations

import re

from app.services.emitters._common import bullet_lines, font_literal, heading_block
from app.services.emitters.base import EmitResult
from app.services.resume_content import ProjectEntry, ResumeContent
from app.services.typst_escape import typst_value

name = "clean-print-cv"
version = "0.1.0"

# Mirrors `typst_escape._LINE_BREAK_RE` (private to that module -- a
# small, deliberate duplication rather than an import across the
# boundary; see the module docstring's "one hazard `typst_value` does
# not cover" section for why `typst_value` itself cannot absorb this).
_LINE_BREAK_RE = re.compile("[\r\n\x0b\x0c\x85  ]+")


def _text(value: str | None) -> str:
    """A prose string ready for `typst_value`: `""` for `None`/empty,
    any embedded line break collapsed to one space exactly like
    `markup_value` already does for every call-shaped emitter's
    bullets."""
    if not value:
        return ""
    return _LINE_BREAK_RE.sub(" ", value)


def _period(start: str | None, end: str | None) -> str:
    """The plain `"<start> – <end>"` string this package's own `period`
    field expects (`cv-data.yaml`'s own example data uses the same en
    dash). Deliberately not `_common.date_range_expr`: that helper
    returns an already-*quoted* Typst literal for a call-shaped
    argument, but this is a raw Python string headed into the `data`
    dict, which `typst_value` quotes once, itself, when the whole dict
    is serialized -- feeding it an already-quoted string would quote it
    twice instead of escaping it once.
    """
    if not start and not end:
        return ""
    if start and end:
        return f"{start} – {end}"
    return start or end or ""


def _project_name(entry: ProjectEntry) -> str:
    # cv-projects' `proj` schema has no `role` key (confirmed against
    # cv-template.typ: name/url/description only) -- folded into `name`
    # instead of declared lost, the same combination acorn_resume.py's
    # own `_project_name` uses for the identical gap. See module
    # docstring.
    role, name = entry.role or "", entry.name or ""
    if role and name:
        return f"{role}, {name}"
    return role or name


def _personal(content: ResumeContent) -> dict:
    contact = content.contact
    return {
        "name": _text(content.name),
        # No ResumeContent field maps to this at all (not even on
        # Contact) -- "" is not a loss, there is nothing to drop. See
        # module docstring.
        "title": "",
        "email": _text(contact.email),
        "phone": _text(contact.phone),
        "location": _text(contact.location),
        "linkedin": _text(contact.linkedin),
        "github": _text(contact.github),
        "website": _text(contact.website),
    }


def _skills(content: ResumeContent) -> list[dict]:
    return [
        {"category": _text(group.category), "items": [_text(item) for item in group.items]}
        for group in content.skills
    ]


def _experience(content: ResumeContent) -> list[dict]:
    return [
        {
            "role": _text(entry.title),
            "company": _text(entry.company),
            "location": _text(entry.location),
            "period": _period(entry.start, entry.end),
            # A real list -- `exp.highlights` is iterated by
            # cv-experience, not joined into a string. No loss here.
            "highlights": [_text(bullet) for bullet in entry.bullets],
        }
        for entry in content.work
    ]


def _projects(content: ResumeContent) -> list[dict]:
    return [
        {
            "name": _text(_project_name(entry)),
            "url": _text(entry.url),
            # project() has only a single `description` string, not a
            # list -- this is the one place bullets are joined rather
            # than preserved as discrete items. degraded (see emit()).
            "description": " ".join(_text(bullet) for bullet in entry.bullets if bullet),
        }
        for entry in content.projects
    ]


def _education(content: ResumeContent) -> list[dict]:
    return [
        {
            "degree": _text(entry.degree),
            "institution": _text(entry.institution),
            "location": _text(entry.location),
            "period": _period(entry.start, entry.end),
            # Same join as projects' description, same reason -- edu's
            # `details` is a single string, not a list. degraded.
            "details": " ".join(_text(bullet) for bullet in entry.bullets if bullet),
        }
        for entry in content.education
    ]


def _build_data(content: ResumeContent) -> dict:
    # certifications/languages are deliberately absent -- see module
    # docstring. Nothing in this package's *called* functions requires
    # either key to exist (each is its own independently-called
    # component in the vendor example), so omitting the key entirely,
    # rather than an empty placeholder list, is enough.
    return {
        "personal": _personal(content),
        "summary": _text(content.summary),
        "skills": _skills(content),
        "experience": _experience(content),
        "projects": _projects(content),
        "education": _education(content),
    }


def emit(content: ResumeContent) -> EmitResult:
    data = _build_data(content)
    lines: list[str] = [
        f'#import "@preview/{name}:{version}": *',
        "",
        f"#let data = {typst_value(data)}",
        "",
        "#show: cv-page-setup",
        # cv-page-setup has no font: argument at all (see module
        # docstring) -- overriding it this way, immediately after the
        # show rule, is what makes the CJK fallback face take effect
        # instead of the package's own hardcoded "New Computer Modern".
        f"#set text(font: {font_literal()})",
        "",
        "#cv-header(data.personal)",
    ]
    # Every section function below prints its own heading
    # unconditionally -- see module docstring's "Section calls are
    # omitted, not just emptied" note -- so each call is itself guarded
    # by whether there is anything to show.
    if content.summary:
        lines.append("#cv-summary(data.summary)")
    if content.work:
        lines.append("#cv-experience(data.experience)")
    if content.skills:
        lines.append("#cv-skills(data.skills)")
    if content.projects:
        lines.append("#cv-projects(data.projects)")
    if content.education:
        lines.append("#cv-education(data.education)")

    for section in content.extras:
        lines.append("")
        lines.extend(heading_block(section.title, bullet_lines(section.entries)))

    source = "\n".join(lines).rstrip() + "\n"

    # Computed from `content` on every call, not a constant -- see
    # module docstring and acorn_resume.emit's own precedent.
    dropped = (
        ("projects[].dates",) if any(p.start or p.end for p in content.projects) else ()
    )
    degraded: list[str] = []
    if any(p.bullets for p in content.projects):
        degraded.append("projects[].bullets")
    if any(e.bullets for e in content.education):
        degraded.append("education[].bullets")

    return EmitResult(source=source, dropped=dropped, degraded=tuple(degraded))
