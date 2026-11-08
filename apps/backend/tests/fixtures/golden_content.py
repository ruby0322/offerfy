"""Two shared `ResumeContent` fixtures every emitter task (8-11) tests against.

`GOLDEN` is deliberately hostile: CJK text, a `"`, a `\\`, a bare `$`, an
`@`, a `#` (an ordinary skill like "C#", not an adversarial string -- see
the `skills` list below), an unpaired `*`, and one over-long bullet, spread
across two jobs with bullets, one degree with bullets, two projects (one
dated, one with a `url` and no `start`/`end` -- see below), two skill
groups, and one `extras` section -- everything an emitter in this package
is asked to hold (see `app/services/emitters/__init__.py`'s module
docstring). An emitter that mishandles any one of these is exactly what
`test_emitter_escapes_content_that_would_break_typst` and the compile-gated
tests in `tests/test_emitters_compile.py` exist to catch.

`projects[1]` carries a `url` but no `start`/`end`: a dated project alone
cannot prove a template renders a project's URL independently of its
dates, because a package's own component can (and `ats-friendly-resume`'s
does -- Task 9's review caught this) conditionally hide the `url` *only*
when there are no dates, which this fixture's first project never
exercises. Both `start` and `end` must be absent, not just one of them --
`_common.dates_expr` treats either end alone as "there are dates" -- so
`dropped`/`degraded` claims about a project's URL can only be trusted
against a fixture where the dates are genuinely, fully absent.

The hostile characters are not only scattered across separate bullets --
one work bullet puts a `@` and a `$` *inside* a quoted phrase
(`"LegacyPay @ $12M/yr"`), which is the specific adversarial shape a
source-oriented escaper gets wrong (it copies a quoted span verbatim,
assuming it is a real Typst string literal, so anything special inside it
reaches the document raw) but a data-mode escaper must not. They also
reach a `SkillGroup` item, a `Section` title, a company name, a project
name, and a `Contact` field (`location`) -- not just bullets -- because
those are exactly the places a dict-shaped emitter (a later task) routes
through a different code path than a markup bullet does, and this fixture
is what decides whether that path gets proven too.

Every field `ResumeContent` has is set on `GOLDEN`, including `summary` and
every `Contact` sub-field (`website` included) -- not just the sections a
given package happens to have a dedicated component for. A package with
nowhere to put one of these has to report it in `dropped` or `degraded`,
and that claim can only be checked against a fixture that actually carries
the field in question; a `GOLDEN` missing `summary` would let every future
emitter's loss-reporting go unchecked for exactly that field.

`MINIMAL` is the opposite case on purpose: a name and an email and nothing
else, the shape of an untouched starter (see `app/services/starter.py`).
It exists to prove an emitter never renders a heading or a component call
for a section that has no entries -- `test_emitter_omits_a_section_with_no_
entries` is the reason this matters: `basic-resume`'s own `0.2.9/template/
main.typ` shows a heading is just markup text, so nothing stops an emitter
from writing `== Projects` for an empty list if it does not check first.
"""

from __future__ import annotations

from app.services.resume_content import (
    Contact,
    EducationEntry,
    ProjectEntry,
    ResumeContent,
    Section,
    SkillGroup,
    WorkEntry,
)

GOLDEN = ResumeContent(
    name="Chen Wei-Ling 陳薇玲",
    contact=Contact(
        email="wei.ling@example.com",
        phone="+1 (555) 010-0100",
        location='Taipei, Taiwan ("near NTU" @ $0 commute)',
        github="github.com/weiling",
        linkedin="linkedin.com/in/weiling",
        website="weiling.dev",
    ),
    summary="Backend engineer who has spent six years hardening payment "
    "infrastructure, most recently leading a ledger migration at Acme Pay.",
    education=[
        EducationEntry(
            institution="National Taiwan University",
            degree='B.S. Computer Science, advisor: Prof. "Lin"',
            location="Taipei, Taiwan",
            start="2016-09",
            end="2020-06",
            bullets=[
                "Published a paper on $O(n log n)$ sorting under adversarial input",
                "Teaching assistant for Data Structures @ NTU CSIE for three "
                "semesters running, covering lecture prep, grading for over two "
                "hundred students each term, and holding weekly office hours "
                "that regularly ran long because nobody wanted to leave without "
                "actually understanding the material first",
            ],
        )
    ],
    work=[
        WorkEntry(
            title="Senior Backend Engineer",
            company="Acme Pay",
            location="Taipei, Taiwan",
            start="2022-01",
            end="Present",
            bullets=[
                'Led the migration off a vendor named "LegacyPay @ $12M/yr" '
                "to an in-house ledger",
                "Cut p99 latency 40% with a * flagged cache layer @edge, "
                "config path C:\\configs\\prod",
            ],
        ),
        WorkEntry(
            title="Software Engineer",
            company='Beta Systems ("BS" @ Hsinchu)',
            location="Hsinchu, Taiwan",
            start="2020-07",
            end="2021-12",
            bullets=[
                "Built the first version of the internal billing @gateway service",
                "Mentored two interns on distributed systems fundamentals",
            ],
        ),
    ],
    projects=[
        ProjectEntry(
            name='OpenLedger ("v2" @ $0 cost)',
            role="Maintainer",
            url="github.com/weiling/openledger",
            start="2021-03",
            end="Present",
            bullets=["Double-entry ledger library used by 3 startups"],
        ),
        # No start/end at all -- see the module docstring on why this is
        # the one shape the first project can never exercise.
        ProjectEntry(
            name="QuietTools",
            url="github.com/weiling/quiettools",
            bullets=["A small CLI released without ever recording a date"],
        ),
    ],
    skills=[
        # "C#" is not adversarial input -- it is an ordinary résumé skill,
        # and `#` is the character the registry-parametrized compile
        # tests in tests/test_emitters_compile.py exist to catch (see
        # typst_escape.markup_value's docstring): unescaped, it either
        # fails to compile outright or silently vanishes from the
        # rendered page while `dropped` still claims nothing was lost.
        SkillGroup(category="Languages", items=["Python", "Go", "Rust", "C#"]),
        SkillGroup(category="Infra", items=["Kubernetes", "Postgres", 'Kafka ($ real-time)']),
    ],
    extras=[
        Section(
            title='Certifications @ "Audited" Co.',
            entries=["AWS Certified Solutions Architect -- Professional (2023)"],
        )
    ],
)

MINIMAL = ResumeContent(name="Jordan Lee", contact=Contact(email="jordan@example.com"))
