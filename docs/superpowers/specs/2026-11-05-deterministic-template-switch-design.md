# Switching templates without asking a model

**Date:** 2026-11-05
**Status:** design approved in conversation; spec awaiting review — not
implemented
**Touches:** backend `app/jobs/survey_templates.py` (new),
`data/template_support.json` (new), `app/services/templates.py`,
`app/services/resume_content.py` (new), `app/services/emitters/` (new, five
files), `app/routers/templates.py`, `app/routers/resumes.py`,
`app/routers/chat.py`, `app/services/role_card.py`,
`app/services/llm/prompts.py`, `app/models.py`, `app/schemas.py`, two alembic
revisions; frontend `components/editor/ChatMessageCard.tsx`,
`components/editor/EditorTemplatePanel.tsx`,
`components/editor/EditorShell.tsx`, `lib/api.ts`,
`messages/{en,zh-TW,zh-CN}.json`

## The finding

Changing templates is a model call that rewrites the whole document. The picker
generates an English sentence (`templates.apply_prompt`), the frontend sends it
as a **user** message (`EditorTemplatePanel.tsx` → `EditorShell.onApplyTemplate`),
and the chat router recognises it again by matching English substrings against
that same sentence:

```python
def is_template_switch_message(message: str) -> bool:
    text = message or ""
    return (
        "rewrite my current Typst resume to use the" in text
        and "apply_typst_edit" in text
        and "@preview/" in text
    )
```

Three things follow from that shape.

**The intent is guessed when it could be a parameter.** A person who types
「幫我換成 acorn-resume」 matches none of those substrings, so
`TEMPLATE_SWITCH_EXTRA` is never added, `prefer_full_source` is never set and
the package's official example is never put in front of the model. The path
degrades silently, and it degrades for exactly the users this product is built
for — it ships zh-TW, zh-CN and en.

**Every switch costs a full rewrite.** The official example alone has a median
of 3,682 characters (measured across the 95 prefetched examples, 2026-11-05),
on top of reading the live source and emitting a complete new one. Browsing
four templates is four rewrites.

**The result is not reproducible.** The same person, the same document and the
same target template produce a different file each time, and nothing records
what the rewrite could not carry across.

### The picker oversells by about 40%

`compile_typst_pages` writes the source to a lone `resume.typ` in an empty temp
directory, so a template whose usage needs sidecar files cannot work here.
Measured 2026-11-05 over all 97 prefetched `cv` packages with the pinned binary
(`apps/backend/.tools/typst`, 0.15.1), compiling each vendor example:

| | |
|---|---|
| compile with their own template directory | 92 / 97 |
| **compile as a single file** | **58 / 97** |
| need a text data file (yaml/toml/json) | 15 |
| need a binary asset (photo, SVG icons, `.bib`) | 14 |
| need sibling `.typ` modules | 4 |
| vendor example fails even with its full directory | 3 |
| ship no example at all | 2 |

The three that fail outright are `chuli-cv`, `fantastic-cv` and `vantage-cv`;
the two with no example are `clean-ats-cv` and `scienceicons` — the latter is an
icon library, not a CV. The `cv` category also contains cover letters
(`exp-cover-letter`, `modernpro-coverletter`) and Japanese rirekisho forms,
which are not résumés and cannot be reached by reformatting one.

Of the 15 data-file templates, **9 compile as a single file once the data file is
mechanically inlined as a Typst value**: `academicv`, `clean-print-cv`,
`curriculo-acad`, `free-cv`, `imprecv`, `jsume`, `kiresume`, `modern-acad-cv`,
`swe-cv`. Three more still need a photo or icon after inlining (`grotesk-cv`,
`nabcv`, `quati-curriculum`); `polycv` and `porygon` take their data path from
`sys.inputs` and are reachable only with per-template handling; `brilliant-cv`
`include`s a directory and is not single-file viable. So the ceiling is **67**,
not 58 — and a probe that only tries the example as shipped wrongly condemns 9
good templates.

## Why this cannot simply be made deterministic

Two independent problems, and only one of them is about templates.

**Reading the document.** `Resume.typst_source` is the only state, and chat
edits it as free text. After one switch it can be in any of 97 package APIs,
plus whatever the model wrote. A deterministic reader would have to be a Typst
semantic parser for all of them. The import agent's own tool description
("one resume might have publications, military service, or certifications,
another might have no education section at all",
`app/services/import_tools.py`) states that sections are deliberately not a
closed schema, so there is nothing fixed to parse toward.

**Writing the document.** There is no common API to target. Across the 95
examples the `#show:` entry point is `resume.with` 27 times and `cv.with` 18,
with the remaining 20-odd each appearing once. The most common content function
is `project` at 15 — **no function name appears in even half the templates**.

Name similarity actively misleads. A proof of concept (throwaway, not kept)
wrote one IR and four emitters by hand; **two of the four failed on the first
attempt**, and each fix required reading the package's own source:

- `ats-friendly-resume` is a fork of `basic-resume` with the same `resume.with`,
  `#work` and `#edu`, but `dates-helper` is renamed `dates-util` and
  `work(title:)` is renamed `work(role:)`.
- `acorn-resume`'s `#edu` has no `details` parameter, so education bullets have
  nowhere to go; its `#project` has no `date` slot at all; and `technologies` is
  nominally optional but the body calls `.map` on it, so omitting it raises
  `type none has no method map`.

All four compiled after those fixes. The conclusion is not that emitters are
hard — each is 30–60 lines — but that each must be written against the
package's source and held in place by a compile test, and that some mappings
lose content no matter who writes them.

## Two words this spec uses

The **curated set** is the templates with a hand-written emitter: switching to
one is code, so it is instant, costs nothing and gives the same file every time.
The **long tail** is every other listed template: switching to one still rewrites
the document with a model, so it takes seconds, spends tokens and is not
reproducible. They look identical in the picker; the difference begins after the
click. In the interface they are 內建模板 and AI 改寫模板 — the i18n keys should
use that pair rather than translating "curated".

## Decisions taken

- **The IR is derived state; `typst_source` stays the source of truth.** Chat
  keeps editing Typst as free text and gains no new tools. The IR carries the
  hash of the source it came from; when they disagree it is re-extracted once.
  So "deterministic" means *one bounded extraction per drift, then free* — not
  "no model ever".
- **Emitters pin an exact version.** `(name, version)`, never "latest".
- **What a template cannot hold is reported, not swallowed.** Each emitter
  returns `dropped` and `degraded` alongside the source; the endpoint returns
  them and the editor says so.
- **The support list is strict, and failures are not listed at all.** No badge,
  no disabled card, no "advanced" drawer.
- **A switch is recorded in the transcript as a system row**, not as a sentence
  we wrote pretending to be the user.
- **Installing fonts is out of scope**, and templates that need a font this host
  does not have are excluded for now rather than shown broken.

## The support list

A CLI job, `app/jobs/survey_templates.py`, following `ingest_catalog.py` and
`refresh_profiles.py`. It writes `data/template_support.json`, committed beside
`ats_boards.json`, and `list_templates()` filters on it. Pre-computed and
reviewable in a diff rather than probed at runtime: the list changes when
someone changes it.

Six gates, all of which must pass:

| # | Gate | What it costs today |
|---|---|---|
| 1 | compiles as a single file, inlining `yaml()`/`toml()`/`json()` data when the as-shipped attempt fails on a missing data file | 97 → 67 |
| 2 | no missing-font warning at all — on the emitter's output where there is one, on the vendor example otherwise | 67 → 29 on vendor examples |
| 3 | vendor example renders in ≤ 2 pages | −7, overlapping gate 2 |
| 4 | thumbnail actually fetches | unmeasured |
| 5 | not on the denylist | −3 of the survivors |
| 6 | for templates with an emitter: the golden IR passes `import_qa.verify_render` | curated set only |

Gates 1–3 and 5 leave **26** of the long tail. Gate 4 is unmeasured and can only
shrink that. The curated set is judged on its own output instead, which is why
`acorn-resume` is in it although its vendor example fails gate 2 — see
*Emitters*.

Gate 2 is the largest filter by a wide margin, and most of what it catches is
our own doing. Of the 67, only 29 compile with no missing-font warning —
measured against this host's fonts as the service sees them, which is 450
families from the system plus `--font-path apps/backend/fonts` (that directory
holds only a README; the fonts named in it are placed there at image build
time). 47 distinct families are missing across the pool. The widest single
cause is Font Awesome 6/7 brands/free/solid, which 10 templates want, followed
by Source Sans 3, Inria Sans, PT Serif, Fira Sans and EB Garamond — all free
fonts we could ship. Only two templates are blocked *solely* by a font we
cannot install (Calibri, Courier New and similar).

A missing font in Typst is a warning, not an error, so these templates compile
and silently render icons as tofu. The first wave therefore excludes **any**
template with a missing-font warning, rather than only the unavailable ones:
excluding just the unavailable ones would drop two templates and admit 36 that
render visibly broken, which is the opposite of the intent. The job still
records each warning as `installable` or `unavailable`, so the to-install set is
on file; installing those fonts and re-running the job admits the affected
templates with no code change. That installation is separate work.

Gate 5 cannot be derived. That `scienceicons` sits in the `cv` category is a
registry fact, so the denylist is hand-maintained and each entry carries its
reason. Five non-résumés reach the 67: `exp-cover-letter`,
`modernpro-coverletter`, `hadronic-rirekisho`, `rirekisho-ofa` and
`shokumu-keirekisho-ofa`. Three of those also clear gates 2 and 3, so without
this gate they would ship.

Gate 6 is deliberately asymmetric, and the spec says so because the list would
otherwise read as a promise it does not make: for a template with an emitter we
can push a fixed test résumé through and check the content survives rendering,
which tests *whether a person's content will look right*. For the rest we can
only compile the vendor's own example, which shows the template is not broken
and nothing more.

Each row records `{name, version, ok, needs_inline_data, reason}`. `reason` is
not served to the frontend; it exists so that "why is `modern-cv` missing?" has
an answer on file (it needs a portrait).

## The content record

A table of its own rather than columns on `Resume`, following
`talent_profiles`: the separate table is what makes "derived, disposable,
rebuildable" legible, and `Resume` is already wide.

```
resume_contents
  resume_id          String(36) PK, FK -> resumes.id, ON DELETE CASCADE
  content_json       Text
  derived_from_hash  String(64)
  built_at           DateTime(timezone=True)
  model              String(64)
```

`Text`, not `JSONB`, for the reason already written at `models.py`'s
`profile_json`: nothing queries inside the blob, and JSONB would normalise key
order.

`derived_from_hash` is `revisions.source_hash(typst_source)` captured at
extraction. The record is fresh exactly when
`source_hash(resume.typst_source) == derived_from_hash`. It reuses that function
rather than introducing a second hash — two hashes with drifting meanings would
be a hard bug to find later.

The schema (pydantic, in `app/services/resume_content.py` beside the
extraction) holds name, contacts, education, work, projects, skills **and
`extras: list[Section]`** — a titled list of entries for publications, awards,
military service and anything else. `extras` is not completeness for its own
sake: a closed schema would discard content at the extraction step, which is the
least observable place it could possibly go wrong. Emitters render `extras`
through a generic section where the template has one, and report it as dropped
where it does not.

Extraction runs only inside the switch endpoint, and only when the target has an
emitter and the record is missing or stale. Not after import, not on publish,
not on a timer. It bills under a new `extract` purpose on `LlmCall`; the
migration copies `025_llm_call_import_purpose.py`, whose docstring already
explains why a model call must not hide inside `chat` (`usage.reconcile`
reconciles `usage_counters.uusd` against the `llm_calls` total, and an
unrecorded call leaves a permanent false gap on the admin overview). Its prompt
goes in `app/services/llm/prompts.py`, which claims to hold every word this
product says to a model.

If extraction fails — provider down, quota exhausted — the endpoint falls back
to the model path rather than failing the switch.

## Emitters

`app/services/emitters/`, one file per package plus `__init__.py` as the
registry.

```python
@dataclass(frozen=True)
class EmitResult:
    source: str
    dropped: tuple[str, ...]    # "projects[].dates"
    degraded: tuple[str, ...]   # "education[].bullets -> plain markup after #edu"

class Emitter(Protocol):
    name: str      # "acorn-resume"
    version: str   # "0.1.0"
    def emit(self, content: ResumeContent) -> EmitResult: ...
```

The registry matches `(name, version)` exactly. `latest_cv_packages()` always
takes the newest version and `list_templates()` follows it, so versions float
underneath us; when the index offers a version no emitter claims, that template
falls back to the model path instead of being emitted by an adapter written for
an older API. `ats-friendly-resume` renaming two of `basic-resume`'s parameters
is the evidence that a version bump can do exactly that.

Escaping goes through `escape_typst_string` and `escape_typst_markup`
(`app/services/typst_escape.py`). Four emitters hand-rolling their own escaping
would be four escapings that each break separately.

**An emitter sets the font explicitly rather than inheriting the package
default**, and gate 2 is measured on the emitter's output rather than on the
vendor example. The vendor's font choice is not binding on us: `acorn-resume`
defaults to Calibri, which this host does not have, so the example warns and the
emitted document does not once the emitter passes an available family. Measured
2026-11-05 on the four emitters' own output — `basic-resume` and
`ats-friendly-resume` clean as emitted, `acorn-resume` clean once it passes
`font: "New Computer Modern"`.

First four, all confirmed to compile 2026-11-05:

| Package | Why it is in the set |
|---|---|
| `basic-resume:0.2.9` | the default starter, and the identity case for a round-trip test |
| `ats-friendly-resume:0.1.1` | the renamed-parameter fork — the false-friend trap |
| `acorn-resume:0.1.0` | missing slots, a required "optional" argument, and the font override |
| `clean-print-cv:0.1.0` | dict-shaped: the emitter builds a data value and calls section functions, which is the strongest check that the interface is not over-fitted to `basic-resume` |

`chicv-ripoff:1.1.4` was the fourth candidate and was dropped. Its Font Awesome
dependency comes from `#personal-info`, the contact block a résumé cannot do
without, and its own default family is Montserrat; neither is installed, so it
cannot be emitted cleanly before the font work. It is a good candidate for a
later wave.

Among data-driven templates, only those where **the package exposes the section
functions** are emitter candidates. `clean-print-cv` is one: its entrypoint just
wires `cv-header(data.personal)`, `cv-experience(data.experience)` and six more.
`swe-cv` is the counter-example — its entrypoint *is* the layout, with the
package supplying only helpers, so an emitter would have to carry a copy of the
vendor's layout and would silently stop matching upstream. Do not write emitters
for that shape.

Every emitter carries two tests:

1. A shared golden IR — CJK, quotation marks, `$`/`@`/`*`, an over-long bullet,
   an empty section, missing contact fields — that every emitter must render and
   actually compile. `pytestmark = pytest.mark.skipif(not typst_available(), …)`,
   following `tests/test_import_qa.py`, so a checkout without the binary skips
   rather than fails.
2. `dropped` and `degraded` asserted as exact sets. This is the only thing that
   keeps a newly lost field from appearing silently in a refactor. What the
   first four report today, measured rather than guessed: `acorn-resume` drops
   `projects[].dates` and degrades `education[].bullets` to plain markup after
   `#edu`; `clean-print-cv` drops `projects[].dates` and degrades both
   `projects[].bullets` and `education[].bullets` into the single-string
   `description` and `details` fields its schema provides.

## The endpoint

`POST /v1/resumes/{resume_id}/template`, body `{name, version}`, shaped after
`resumes.restore_revision` — already a POST that replaces the draft source.

`(name, version)` must appear in `data/template_support.json` or the request is
rejected with 400. Enforcing it here and not only in `list_templates()` is the
point: a list that merely hides bad options still lets anything through to the
document.

1. An emitter claims `(name, version)` → ensure the content record is fresh,
   extracting once if not → `emit()` → **compile the result** → on success
   `set_draft_source()`, write the system row, return
   `{mode: "applied", dropped, degraded, resume}`.
2. The compile fails → **write nothing**, log it, return `{mode: "llm"}`. A
   deterministic path that can commit a broken document is worse than the model
   path it replaces, and one compile costs about 0.12s on this host (measured
   for the import-progress spec, 2026-11-04). `compile_typst` raises
   `HTTPException(400)`, so this branch has to catch it rather than let it
   escape.
3. No emitter → `{mode: "llm", prompt}`, and the frontend sends it through chat
   as it does today. `apply_prompt()` survives, but is produced per request
   rather than baked into every row of `list_templates()`.

`is_template_switch_message()` is deleted rather than repaired. Two things
replace it: the chat request gains an optional `template: {name, version}`
field, which the picker's model path sets explicitly; and natural-language
detection matches **package names from the support list, and `@preview/x:y`,
in any language**. 「幫我換成 acorn-resume」 then works as well as the English
sentence does. A person who writes only "give me something prettier" is still
not detected, and that is a different problem.

## What the person sees

The system row stores structured JSON in `content` —
`{"type": "template_switch", "name": …, "version": …, "dropped": […],
"degraded": […]}` — rendered by the frontend in the reader's language. Tool rows
already store JSON in `content`, and this product ships three languages, so an
English sentence in the database would be wrong in two of them.

`_history` selects `role.in_(("user", "assistant"))` (`app/routers/chat.py`), so
a `system` row never reaches a provider. `list_messages` does not filter by role
and `ChatMessageOut.role` is a plain `str`, so no schema changes. But
`ChatMessageCard` dispatches on `role === "user"` and `role === "tool"` and
treats everything else as an assistant bubble, so **it must gain a `system`
branch** or it will print the JSON verbatim.

`build_role_card` gains a line naming the template the document currently uses,
read from the live source with the existing `parse_preview_spec()`. Without it
`SYSTEM_PROMPT`'s standing instruction — "The default starter uses
`@preview/basic-resume:0.2.9` … Keep those unless the user asks to switch
templates" — will pull the document back toward the starter on the next ordinary
chat turn, a bug that only surfaces one conversation after the switch and is
very hard to attribute.

## Order of work

Each step ships on its own and none blocks another.

1. Survey job, `data/template_support.json`, `list_templates()` filtering,
   denylist. Visible the same day: bad options disappear.
2. The endpoint, deleting the English match, the system row, the
   `ChatMessageCard` branch, the role-card line. Behaviour unchanged; intent
   becomes a parameter.
3. `resume_contents`, the IR schema, extraction, the `extract` purpose
   migration.
4. The four emitters, the golden IR tests, the registry. Switching becomes
   instant here and not before.

Two alembic revisions, `026_resume_contents` and
`027_llm_call_extract_purpose`. The single head is `025` as of 2026-11-05, and
other sessions merge to `main` concurrently, so confirm `alembic heads` before
writing them.

## Not in scope

- Installing fonts, and the re-run of the survey that would reconsider up to 36
  more templates — the 38 held back by gate 2, less the two blocked by a font
  we cannot install, and still subject to the remaining gates.
- Emitters beyond the first four. The registry makes each additional one an
  independent piece of work.
- Fitting a résumé onto one page, deciding where content goes when the target
  template has no slot for it, templates that need a portrait, and converting
  between document kinds (a résumé into a rirekisho). None of these are
  deterministic, and none of them work correctly today either — they just fail
  more quietly.
