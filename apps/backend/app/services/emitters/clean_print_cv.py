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
`_common.bullet_lines`, for the one thing this emitter *does* write as
real markup source: the `extras` bullets (see below).

**An embedded line break.** `typst_value` normalises one to a single
space before quoting, the same way `markup_value` does, and for the same
reason (see `typst_escape.typst_value`'s own docstring) -- this emitter
is what surfaced the gap: it is the first one to route free-form prose
(`summary`, bullets folded into `description`/`details`) through
`typst_value` rather than `markup_value`, since this package displays
prose via code-mode interpolation rather than markup. Fixed once, in
`typst_escape.py` itself, rather than patched around here, once review
flagged the first version of this module for duplicating `markup_value`'s
`_LINE_BREAK_RE` locally -- the gap was a property of `typst_value`, not
of this package, and a per-emitter patch is exactly how it would have
reached a fifth emitter untouched.

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
- Every `Contact` field reaches `personal`: `email`, `phone`,
  `location`, `github`, `linkedin`, `website` are all named keys `cv-
  header` already reads. Unlike `acorn-resume`, which has no slot for
  `phone`/`location` at all, this package's header has a dedicated key
  for both -- no loss, no need for the trailing-line fallback acorn
  needed. Which value lands in which key is *not* field-for-field,
  though: the first three keys are printed with unconditional
  separators, so the values are packed into them in order -- see
  `_CONTACT_SLOTS`.
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
a loss here too. But Tasks 9 and 10 both found the same gap closeable,
and this package closes it even more directly than either: `section-
heading(title)` is a top-level `#let` in `cv-template.typ`, with no
underscore prefix, so `#import "@preview/clean-print-cv:0.1.0": *`
already exports it -- the exact function every built-in section calls
for its own accent-bar/upper-case/hairline-rule heading. Calling it
directly for `extras` too (then `_common.bullet_lines` for the body)
costs nothing and matches every other heading on the page exactly,
rather than falling back to a bare `== <title>` markup heading that
would only *approximate* this package's own look. (An earlier version of
this module used the bare-heading fallback, reasoning that it kept
`extras` handling identical across all four emitters -- review correctly
rejected that: this task exists *because* this package is shaped
differently from the other three, so cross-emitter uniformity was the
wrong axis to optimise, and the package's own heading was free for the
taking.) So `extras` is appended after every other section, exactly like
the other three emitters in *position*, but through the package's own
heading function rather than a markup approximation of one, and reports
nothing in `dropped`/`degraded`.
"""

from __future__ import annotations

from app.services.emitters._common import bare_url, bullet_lines, font_literal
from app.services.emitters.base import EmitResult
from app.services.resume_content import ProjectEntry, ResumeContent
from app.services.typst_escape import typst_value

name = "clean-print-cv"
version = "0.1.0"


def _text(value: str | None) -> str:
    """A prose string ready for `typst_value`: `""` for `None`/empty.
    `typst_value` itself now normalises an embedded line break to a
    single space (see that function's own docstring) -- this helper only
    supplies the empty-string default `cv-header`/etc. require in place
    of `none`, since every key `_personal`/`_experience`/etc. build is
    read with no `.at(default:)` guard."""
    return value or ""


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


# `cv-header`'s contact row, in the package's own order. The first three
# are printed with *unconditional* separators -- `#personal.email
# #contact-sep #personal.phone #contact-sep #personal.location`
# (cv-template.typ:107-111) -- and only the last three are guarded by an
# `if != ""`. `contact-sep` is a closed-over `#let` inside
# `cv-page-setup`'s scope, so there is no argument, key or show rule this
# emitter can reach to suppress a separator next to an empty slot: a
# person with no phone number used to get `r@example.com  |    |` under
# their name, and `MINIMAL` (name + email, the shape of an untouched
# starter) rendered the same.
#
# So the slots are *packed* rather than mapped field-for-field: every
# present contact value, in this order, fills the three unguarded slots
# first and spills into the guarded ones. That is safe because
# `cv-header` renders all six identically -- plain text, no `mailto:`,
# no `#link`, no per-field icon or prefix (confirmed against
# cv-template.typ) -- so which slot a value occupies changes nothing
# about how it looks, only whether a separator next to it has something
# to separate. Three or more contact values (email + phone + location,
# or email + two links) now render with no stray separator at all, and
# two leave one trailing separator instead of two interleaved ones.
#
# One contact value still leaves two trailing separators: the unguarded
# part of that row always emits exactly two, and no arrangement of a
# single value can hide both. Removing them would mean not calling
# `cv-header` at all -- reimplementing the name block, rule, subtitle and
# contact row against this package's *internal* style `#let`s -- or
# emitting negative `h()` spacing into a slot, which is font- and
# size-dependent junk in a PDF a person sends to employers. Neither is
# worth it; see `test_clean_print_cv_packs_a_partial_contact_row`.
_CONTACT_SLOTS = ("email", "phone", "location", "linkedin", "github", "website")


def _personal(content: ResumeContent) -> dict:
    contact = content.contact
    # linkedin/github/website go through `bare_url`, not `_text`: this
    # package shows them as plain text rather than a hyperlink, so a
    # value carrying its own `https://` is a label inconsistent with
    # every other emitter's rather than a dead link -- normalised here
    # for the same reason and in the same place. See `_common.bare_url`.
    present = [
        value
        for value in (
            _text(contact.email),
            _text(contact.phone),
            _text(contact.location),
            bare_url(contact.linkedin),
            bare_url(contact.github),
            bare_url(contact.website),
        )
        if value
    ]
    personal = {
        "name": _text(content.name),
        # No ResumeContent field maps to this at all (not even on
        # Contact) -- "" is not a loss, there is nothing to drop. See
        # module docstring.
        "title": "",
        # Every slot is always present, empty or not: `cv-header` reads
        # each one with no `.at(default:)` guard (except `website`), so a
        # missing key is a hard compile error.
        **{slot: "" for slot in _CONTACT_SLOTS},
    }
    personal.update(dict(zip(_CONTACT_SLOTS, present)))
    return personal


def _skills(content: ResumeContent) -> list[dict]:
    # A group with nothing in it is filtered out, not emitted empty:
    # `cv-skills` prints `#group.category:` and `#group.items.join(...)`
    # unconditionally, so an empty group renders a bare `:` under the
    # TECHNICAL SKILLS heading. This is the dict-shaped counterpart of
    # `_common.skill_line` returning `None`, which is what already keeps
    # the other three emitters from rendering the same junk -- and the
    # reason `emit()` guards `#cv-skills` on *this* list rather than on
    # `content.skills`: a skills list of nothing but empty groups must
    # not print the heading either.
    groups = [
        {"category": _text(group.category), "items": [_text(item) for item in group.items if item]}
        for group in content.skills
    ]
    return [group for group in groups if group["category"] or group["items"]]


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
            # Shown as plain text here, not a `#link` -- `bare_url` for
            # label consistency with the other three emitters, which
            # must strip the scheme to keep the hyperlink alive.
            "url": bare_url(entry.url),
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
    # `data["skills"]`, not `content.skills`: `_skills` drops a group
    # with no category and no items, so a skills list made only of empty
    # groups must not print the heading. See `_skills`.
    if data["skills"]:
        lines.append("#cv-skills(data.skills)")
    if content.projects:
        lines.append("#cv-projects(data.projects)")
    if content.education:
        lines.append("#cv-education(data.education)")

    for section in content.extras:
        # section-heading() prints unconditionally -- it has no internal
        # "skip if empty" guard of its own, unlike `_common.heading_
        # block` (which this emitter no longer calls here, see below).
        # A `Section` with no -- or entirely blank -- `entries` must
        # still emit nothing at all, the same "heading only when there
        # is a body" rule every other call in this function already
        # follows (see the "Section calls are omitted" note above) and
        # the one `heading_block` gave this section for free before the
        # switch to `section-heading()`. Computing the body first and
        # skipping the section when it is empty restores that.
        body = bullet_lines(section.entries)
        if not body:
            continue
        lines.append("")
        # section-heading() is the package's own top-level `#let`
        # (confirmed against cv-template.typ: no underscore prefix),
        # already in scope via the `*` import above -- the same
        # accent-bar/upper-case/hairline-rule heading every built-in
        # section uses, not a markup `==` approximation of it. See
        # module docstring's "extras is rendered" note.
        lines.append(f"#section-heading({typst_value(section.title)})")
        lines.extend(body)

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
