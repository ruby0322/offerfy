# Structured Typst Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third editor tab where a non-technical person edits their résumé as
fields — values, bullets, blocks, bold and links — while `Resume.typst_source` stays the
only source of truth.

**Architecture:** The backend owns the Typst grammar. `GET /v1/resumes/{id}/projection`
parses the source into units (each with its byte span) and returns them as plain text plus
marks; `POST /v1/resumes/{id}/edit` takes an intent in those same terms, rewrites the
source, compiles it as a gate, and returns the new source, projection and preview. The
frontend never writes Typst and never escapes anything. Preview line highlighting comes
from a labelled shadow copy queried with `typst eval`, crossed with the existing per-line
text layer.

**Tech Stack:** FastAPI + SQLAlchemy + pydantic (backend), pytest; Next.js + React +
TypeScript (frontend), `node:test` via `tsx`; typst 0.15.1 CLI; pdfminer for the text
layer.

**Spec:** `docs/superpowers/specs/2026-11-19-structured-typst-editing-design.md`

## Global Constraints

- `Resume.typst_source` is the only source of truth. **This plan adds no alembic
  revision** and does not touch `resume_contents` or `app/services/resume_content.py`.
- Zero model calls in every path this plan adds.
- Every write compiles before it commits. A failed compile writes nothing and returns
  `compile_failed` with typst's first stderr line.
- Structural intents call `set_draft_source(..., coalesce=False)`. Value intents coalesce
  only when the request's `hash` equals the latest revision's `content_hash`.
- `/edit` is `async def`; every typst subprocess runs through `asyncio.to_thread`; no DB
  session is held across a compile.
- Backend routes are bare `/v1/...` (the `/api` prefix is public-only).
- `messages/{en,zh-TW,zh-CN}.json` gain every key together — a missing key throws at
  render time, it does not fall back. Design and verify in zh-TW.
- Backend tests: `cd apps/backend && .venv/bin/pytest <path> -v`. Frontend pure modules:
  `cd apps/frontend && ./node_modules/.bin/tsx --test <path>`. The frontend gate is
  `npm run build:public` (it typechecks); `npm run lint` is unusable, lint touched files
  with `./node_modules/.bin/eslint <path>`.
- Tests that compile get `pytestmark = pytest.mark.skipif(not typst_available(),
  reason="typst binary not available")`, matching `tests/test_emitters_compile.py`.
- `typst query` is deprecated in 0.15.1 — use `typst eval`.
- No prettier on this repo; hand-wrap at ~90 columns.
- Phone: focusable fields need `text-base md:text-sm` (≥16px) or iOS zooms and never
  returns; `NARROW_SCREEN` in `lib/use-media-query.ts` is the only phone breakpoint.

## Review Focus

1. **A section heading that contains a link or bold** — the title is a `line` unit like any
   other and must round-trip; if it is treated as a plain string the title silently loses
   its markup. Test in Task 3.
2. **Two writers racing** — a chat turn or import lands between the client's projection and
   its next intent; the intent must be refused with `stale_hash`, never merged. Test in
   Task 6.
3. **A line emptied to nothing** — the person selects a whole bullet and deletes it; the
   result must stay a valid empty bullet, never `- **` or a stray `*`. Test in Task 5.
4. **CRLF and a missing trailing newline** — uploaded résumés arrive both ways; every span
   offset and the whitespace normaliser must survive them. Test in Task 2.
5. **One very long line** — a 2000-character bullet wraps across many rendered lines and
   must yield one band per visual line, not one band for the block. Test in Task 8.

---

### Task 1: Synthetic fixtures and the prototype reference

**Files:**
- Create: `apps/backend/tests/fixtures/typst/synthetic-en.typ`,
  `synthetic-zh.typ`, `synthetic-marks.typ`, `synthetic-tier-b.typ`,
  `synthetic-tier-c.typ`
- Create: `docs/superpowers/prototypes/2026-11-19-structured-typst-editing/parser.js`,
  `prototype.html` (copied from the published POC; reference only, nothing imports them)
- Test: `apps/backend/tests/test_typst_fixtures.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FIXTURES = Path(__file__).parent / "fixtures" / "typst"` — every later backend
  task reads its inputs from there.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_typst_fixtures.py
pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

FIXTURES = Path(__file__).parent / "fixtures" / "typst"
NAMES = ["synthetic-en", "synthetic-zh", "synthetic-marks",
         "synthetic-tier-b", "synthetic-tier-c"]

@pytest.mark.parametrize("name", NAMES)
def test_fixture_compiles(name):
    pdf = compile_typst((FIXTURES / f"{name}.typ").read_text(encoding="utf-8"), "pdf")
    assert pdf.startswith(b"%PDF")

def test_marks_fixture_covers_every_inline_case():
    src = (FIXTURES / "synthetic-marks.typ").read_text(encoding="utf-8")
    # Position, not mere presence: a bare `"*" in src` is satisfied by almost any file
    # and would not notice the one case these fixtures exist to pin.
    assert re.search(r"^- .*\n// - .*\n- ", src, re.M)        # comment between two bullets
    assert re.search(r"\*[^*\n]*#link\(\"[^\"]+\"\)\[[^\]]+\][^*\n]*\*", src)  # link in bold
    assert re.search(r"#link\(\"[^\"]+\"\)\[[^\]]+\]", src)
    assert re.search(r"\[nested .*\]", src)                   # nested content block
    for escape in ["\\&", "\\@", "\\*", "\\~", "\\$"]:
        assert escape in src

def test_en_fixture_has_a_dead_let_and_a_used_one():
    src = (FIXTURES / "synthetic-en.typ").read_text(encoding="utf-8")
    assert "author: name," in src          # used through one hop
    assert src.count("phone") == 1         # declared once, referenced never
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_fixtures.py -v`
Expected: FAIL — `FileNotFoundError` on `synthetic-en.typ`

- [ ] **Step 3: Write the five fixtures**

All content invented — no real names, addresses, employers or URLs. Each must import
`@preview/basic-resume:0.2.9` and compile standalone. What each has to contain is the
table in the spec's **測試 fixture** section; read it and satisfy every cell. The two that
matter most:

- `synthetic-marks.typ` — a bullet with a link inside bold, a `[nested content block]`,
  the five escapes, and **a `// ` commented-out bullet between two live bullets**.
- `synthetic-tier-b.typ` — must contain a unit the parser cannot serialise back, not
  merely a `#grid`: a bullet carrying an unparsed `#func(...)` gives an opaque run, which
  is what forces Tier B. A `#grid` with no string-literal args and no bullets is only
  "setup" and leaves the document Tier A.
- `synthetic-tier-c.typ` — **no editable unit of any kind.** Dropping `#work`/`#edu`/
  `#project`, headings and bullets is not enough: a `resume.with(author: "…")` argument is
  an editable `arg` unit and a bare prose line is an editable `para` unit. Model it on the
  `needs_inline_data` template family (4 of the 38 supported templates): every piece of
  content lives in a `#let data = (…)` dictionary rendered by a `#for` loop, so the parser
  sees a non-literal `#let`, an unrecognised loop, and no prose.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_fixtures.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Copy the prototype in as reference**

`parser.js` is the POC's `parse3.js`; `prototype.html` is the published page. Add a
two-line header comment to each: reference for the spec, not built, not imported.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/tests/test_typst_fixtures.py apps/backend/tests/fixtures/typst \
  docs/superpowers/prototypes
git commit -m "test(typst): synthetic fixtures for structured editing"
```

---

### Task 2: Projection — blocks, args and the report

**Files:**
- Create: `apps/backend/app/services/typst_projection.py`
- Test: `apps/backend/tests/test_typst_projection.py`

**Interfaces:**
- Consumes: `FIXTURES` from Task 1.
- Produces:

```python
@dataclass(frozen=True)
class Mark:      kind: str; start: int; end: int; url: str | None = None
@dataclass(frozen=True)
class Unit:
    id: str; kind: str; span: tuple[int, int]; value: str; editable: bool
    key: str | None = None; style: bool = False; role: str | None = None
    marks: tuple[Mark, ...] = (); opaque: tuple[str, ...] = ()
    escapes: tuple[str, ...] = ()
@dataclass(frozen=True)
class Block:
    id: str; kind: str; name: str | None; outer: tuple[int, int]; content_end: int
    args: tuple[str, ...] = (); unit: str | None = None
    bullets: tuple["Block", ...] = (); notes: tuple[tuple[int, int], ...] = ()
    body: tuple[int, int] | None = None      # a `#ident(...)[…]` content block
    body_units: tuple[str, ...] = ()         # the line units inside that body
@dataclass(frozen=True)
class Section:
    id: str; title_unit: str | None; outer: tuple[int, int]; body_start: int
    blocks: tuple[Block, ...]
@dataclass(frozen=True)
class Report: structures: dict[str, int]; skipped_args: dict[str, int]; lints: tuple[str, ...]
@dataclass(frozen=True)
class Projection:
    hash: str; tier: str; sections: tuple[Section, ...]
    units: dict[str, Unit]; report: Report

STYLE_KEYS: frozenset[str]
def is_style_key(key: str) -> bool
def parse_projection(source: str) -> Projection
```

- [ ] **Step 1: Write the failing tests**

```python
def test_recognises_shapes_not_names():
    p = parse_projection(read("synthetic-en"))
    names = {b.name for s in p.sections for b in s.blocks if b.kind == "comp"}
    assert {"work", "edu", "project"} <= names        # #project has no emitter

def test_let_indirection_one_hop():
    p = parse_projection(read("synthetic-en"))
    author = next(u for u in p.units.values() if u.key == "author")
    assert author.value == "Avery Quill"              # from `#let name = "..."`
    assert author.editable

def test_dead_let_is_linted():
    p = parse_projection(read("synthetic-en"))
    assert any("phone" in l for l in p.report.lints)

def test_style_keys_are_marked_not_hidden():
    p = parse_projection(read("synthetic-en"))
    accent = next(u for u in p.units.values() if u.key == "accent-color")
    assert accent.style and accent.editable
    assert not next(u for u in p.units.values() if u.key == "title").style

def test_unparsed_lines_never_become_units():
    p = parse_projection(read("synthetic-en"))
    for u in p.units.values():
        # Every unit here is a keyed argument, so a leading `#` may only be a hex
        # colour — `accent-color: "#26428b"` is content, `#project(` never is.
        assert (not u.value.lstrip().startswith("#")
                or re.fullmatch(r"#[0-9a-fA-F]{3,8}", u.value))
    assert p.report.structures["#pagebreak"] == 1
    # synthetic-en carries no comment; the marks fixture has exactly one.
    assert parse_projection(read("synthetic-marks")).report.structures["comment"] == 1

def test_an_empty_string_argument_is_still_an_editable_unit():
    # `#project(dates: "")` is ordinary in the wild, and a form that cannot fill an
    # empty field cannot fill in a date the template already declares.
    p = parse_projection(read("synthetic-en"))
    empty = [u for u in p.units.values() if u.kind == "arg" and u.value == ""]
    assert empty and all(u.editable and u.span[0] == u.span[1] for u in empty)

@pytest.mark.parametrize("name,tier", [("synthetic-en", "A"), ("synthetic-zh", "A"),
                                        ("synthetic-marks", "A"), ("synthetic-tier-b", "B"),
                                        ("synthetic-tier-c", "C")])
def test_every_fixture_grades_the_tier_it_exists_for(name, tier):
    # Task 1 could only check these verdicts with the throwaway prototype parser, so
    # nothing in the suite held them. This is where they get pinned for real.
    assert parse_projection(read(name)).tier == tier

def test_crlf_and_missing_final_newline_keep_spans_exact():   # Review Focus 4
    src = read("synthetic-en").replace("\n", "\r\n").rstrip("\r\n")
    p = parse_projection(src)
    for u in p.units.values():
        # The invariant is that a span holds its own unit, not that it is non-empty:
        # an empty string argument is a unit with an empty span.
        assert unescape_string(src[u.span[0]:u.span[1]]) == u.value
        assert "\r" not in u.value

def test_a_multi_line_expression_is_not_prose():
    # Found while building the Tier C fixture: the prototype read the continuation
    # lines of a multi-line `#let data = (...)` as prose, producing editable units
    # full of Typst syntax — anti-goal #1 wearing a different hat.
    src = read("synthetic-tier-c")
    p = parse_projection(src)
    assert p.tier == "C"
    for u in p.units.values():
        assert "(" not in u.value and ":" not in u.value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_projection.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.typst_projection`

- [ ] **Step 3: Implement `parse_projection(source: str) -> Projection` in `app/services/typst_projection.py`**

Only `line`-less structure in this task: sections from `^== (.+)$`, blocks from
`^#ident(` with balanced-paren scanning, `key: "literal"` args (recursing one level into a
`key: call(...)` value so `dates-helper(start-date:, end-date:)` yields two args), one hop
through top-level `#let ident = "literal"`, and everything unclassified recorded in
`report.structures` rather than turned into a unit. **A line that continues an unterminated
expression from an earlier line is part of that construct, never prose:** carry the
paren/bracket depth across lines while scanning, and when a line starts at a depth above
zero, skip to the end of the construct. Without this a multi-line `#let data = (…)` yields
editable units full of Typst syntax, which Task 1 hit while building the Tier C fixture. `line` units come in Task 3; leave
`Section.title_unit` and `Block.unit` as `None` here. `STYLE_KEYS` holds
`{"font", "paper", "lang", "margin", "size", "leading"}` plus any key ending `-color`,
`-position` or `-font`. Tier is `C` when no unit is editable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_projection.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/typst_projection.py apps/backend/tests/test_typst_projection.py
git commit -m "feat(typst): parse a resume's blocks and string arguments"
```

---

### Task 3: Projection — line units, inline marks and the round-trip invariant

**Files:**
- Modify: `apps/backend/app/services/typst_projection.py`
- Test: `apps/backend/tests/test_typst_projection.py`

**Interfaces:**
- Consumes: Task 2's types.
- Produces: `def serialize_unit(unit: Unit) -> str` — a unit's exact source slice,
  rebuilt from `value` + `marks` + `opaque` + `escapes`. Task 5 writes through this.

- [ ] **Step 1: Write the failing tests**

```python
def test_bullet_marks_use_plain_text_offsets():
    p = parse_projection(read("synthetic-marks"))
    u = unit_by_text(p, "Cut p95 from 1.8s to 240ms")
    strong = [m for m in u.marks if m.kind == "strong"][0]
    assert u.value[strong.start:strong.end] == "p95"

def test_link_inside_bold_round_trips():
    p = parse_projection(read("synthetic-marks"))
    u = unit_by_text(p, "Shipped the queue runner")
    assert {m.kind for m in u.marks} == {"strong", "link"}
    assert serialize_unit(u) == read("synthetic-marks")[u.span[0]:u.span[1]]

def test_heading_with_markup_is_a_line_unit():                 # Review Focus 1
    p = parse_projection(read("synthetic-marks"))
    title = p.units[[s for s in p.sections if s.title_unit][0].title_unit]
    assert title.role == "heading" and title.marks
    assert serialize_unit(title) == read("synthetic-marks")[title.span[0]:title.span[1]]

def test_escape_policy_follows_the_source():
    p = parse_projection(read("synthetic-marks"))
    raw = unit_by_text(p, "Research & Development")      # a bare &
    esc = unit_by_text(p, "Systems Analysis & Design")   # written \&
    assert "&" not in raw.escapes and "&" in esc.escapes
    for u in (raw, esc):
        assert serialize_unit(u) == read("synthetic-marks")[u.span[0]:u.span[1]]

def test_commented_bullet_does_not_split_the_run():
    p = parse_projection(read("synthetic-marks"))
    comp = [b for s in p.sections for b in s.blocks if b.kind == "comp"][0]
    assert len(comp.bullets) == 2 and len(comp.notes) == 1

def test_unserialisable_line_is_read_only():
    p = parse_projection(read("synthetic-tier-b"))
    assert any(not u.editable for u in p.units.values())
    assert p.tier == "B"

def test_unprojected_content_inside_a_component_is_not_tier_a():
    # A positional string argument (`#tag("Open to relocation")`) and a grid cell are
    # content the form does not show. Grading that document Tier A would claim full
    # coverage while hiding half a résumé — Tier A has to mean what it says, even
    # though projecting these is deliberately out of scope.
    p = parse_projection(read("synthetic-tier-b"))
    assert p.report.skipped_args.get("(positional)", 0) == 3
    assert p.tier == "B"

def test_a_content_body_projects_its_lines():
    # `#align(left)[…]` is where basic-resume documents put the professional summary,
    # so if its body is not projected the form cannot edit a first-class résumé field.
    p = parse_projection(read("synthetic-en"))
    align = next(b for s in p.sections for b in s.blocks if b.name == "align")
    assert align.body and align.body_units
    summary = p.units[align.body_units[0]]
    assert summary.role == "para" and summary.editable and summary.value.strip()
    assert serialize_unit(summary) == read("synthetic-en")[summary.span[0]:summary.span[1]]

@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks",
                                  "synthetic-tier-b", "synthetic-tier-c"])
def test_every_line_is_a_unit_a_block_or_a_counted_structure(name):
    # The design promises the report accounts for everything the form does not project
    # ("沒有被偷偷忽略"). After Task 2 a plain text line outside a block was in neither
    # place; this is the invariant that keeps that from becoming a permanent blind spot.
    src = read(name)
    p = parse_projection(src)
    spans = [u.span for u in p.units.values()]
    outers = [b.outer for s in p.sections for b in s.blocks]
    unaccounted = []
    for start, line in line_offsets(src):          # (offset, text) per line
        if not line.strip():
            continue
        end = start + len(line)
        if any(a < end and start < b for a, b in spans + outers):
            continue
        unaccounted.append(line.strip())
    # Compared as a SET, not a count: a count-based assertion is satisfied by any N
    # counted structures anywhere, which invites inflating the report to make the
    # arithmetic work. The golden says which lines the form does not project, so both
    # over- and under-recognition fail it.
    assert unaccounted == json.loads(
        (FIXTURES / f"{name}.unprojected.json").read_text("utf-8"))

@pytest.mark.parametrize("name", ["synthetic-en", "synthetic-zh", "synthetic-marks"])
def test_rewriting_every_unit_changes_nothing(name):           # Invariant 1
    src = read(name)
    p = parse_projection(src)
    out = src
    for u in sorted(p.units.values(), key=lambda x: -x.span[0]):
        out = out[:u.span[0]] + serialize_unit(u) + out[u.span[1]:]
    assert out == src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_projection.py -v`
Expected: FAIL — `serialize_unit` not defined

- [ ] **Step 3: Implement line units and `serialize_unit` in `app/services/typst_projection.py`**

A `line` unit is one text range: a bullet's text after `- `, a bare paragraph line, or a
heading's title. Tokenise it recursively into text / `*strong*` / `_emph_` /
`#link("url")[body]` / `[group]` / opaque runs; flatten to `value` (plain text) plus
`marks` with plain-text offsets. `escapes` is the set of characters that appear escaped in
this range and are **not** in the canonical set
`\\ # $ * _ [ ] < > @ ~ `` ` ``; `serialize_unit` escapes the canonical set plus
`escapes`, and emits `//` as `/\/`. A unit is `editable` only when
`serialize_unit(unit) == source[span]`; an opaque run makes the document Tier B.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_projection.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Generate the golden unit dump and read it before committing**

The spec lists `synthetic-en.units.json` as the baseline for the parser. Its contents are
byte-offset spans, so it cannot be hand-written — it is a generated snapshot, and it
belongs here rather than in Task 1, because only now are line units in the dump.

Write `apps/backend/tests/fixtures/typst/synthetic-en.units.json` from
`parse_projection(read("synthetic-en"))`: every unit in document order, as
`{id, kind, key, role, span, value, editable}`, two-space indented, `ensure_ascii=False`,
with a trailing newline. Add the test that pins it:

```python
def test_en_projection_matches_the_golden_dump():
    got = [dump(u) for u in sorted(parse_projection(read("synthetic-en")).units.values(),
                                   key=lambda u: u.span[0])]
    assert got == json.loads((FIXTURES / "synthetic-en.units.json").read_text("utf-8"))
```

**Then read the file yourself** before committing: every `value` must be text a person
would recognise from the fixture, no `#`-prefixed syntax, no empty values. A golden
committed without being read pins whatever the parser happened to do.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/services/typst_projection.py apps/backend/tests/test_typst_projection.py \
  apps/backend/tests/fixtures/typst/synthetic-en.units.json
git commit -m "feat(typst): line units with inline marks that round-trip exactly"
```

---

### Task 4: Projection endpoint

**Files:**
- Create: `apps/backend/app/schemas_editing.py`
- Modify: `apps/backend/app/routers/resumes.py`
- Test: `apps/backend/tests/test_projection_api.py`

**Interfaces:**
- Consumes: `parse_projection`.
- Produces: `GET /v1/resumes/{resume_id}/projection` → `ProjectionOut`; pydantic models
  `MarkOut`, `UnitOut`, `BlockOut`, `SectionOut`, `ReportOut`, `ProjectionOut`, and

```python
class IntentIn(BaseModel):
    hash: str
    kind: str          # set_arg | set_line | insert_bullet | insert_item
                       # | insert_section | delete | move_section
    unit: str | None = None       # set_arg, set_line
    expect: str | None = None     # set_arg, set_line — the value the client believes it has
    value: str | None = None      # set_arg
    text: str | None = None       # set_line
    marks: list[MarkOut] = []     # set_line
    target: str | None = None     # insert_bullet, insert_item, delete, move_section
    dir: str | None = None        # move_section: "up" | "down"
```

  `EditOut` is filled in Task 6.

- [ ] **Step 1: Write the failing test**

```python
def test_projection_returns_units_for_the_owner(client, resume_with(FIXTURE_EN)):
    r = client.get(f"/v1/resumes/{rid}/projection")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "A" and body["hash"] == source_hash(FIXTURE_EN)
    assert body["units"]["u0"]["kind"] in {"arg", "line"}

def test_projection_is_not_persisted(client, resume_with(FIXTURE_EN), db):
    client.get(f"/v1/resumes/{rid}/projection")
    assert db.query(models.ResumeContent).count() == 0     # Global Constraint

def test_projection_refuses_a_stranger(client, client_other, resume_with(FIXTURE_EN)):
    # The owner assertion is what makes this test mean anything: `in (403, 404)` alone
    # passes when the route does not exist, because 404 is then the honest answer.
    assert client.get(f"/v1/resumes/{rid}/projection").status_code == 200
    assert client_other.get(f"/v1/resumes/{rid}/projection").status_code in (403, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_projection_api.py -v`
Expected: FAIL — 404, route not registered

- [ ] **Step 3: Implement the schemas and the route**

`ProjectionOut` mirrors Task 2's dataclasses field for field, `span` as a two-item list.
The handler is `def projection(...)` beside `preview` in `routers/resumes.py`, using the
same `owner_context` / `load_resume_for_owner` pair every other resume route uses, so
guests work unchanged. No caching, no writes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_projection_api.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/schemas_editing.py apps/backend/app/routers/resumes.py \
  apps/backend/tests/test_projection_api.py
git commit -m "feat(api): serve a resume's field projection"
```

---

### Task 5: Value intents — `set_arg` and `set_line`

**Files:**
- Create: `apps/backend/app/services/typst_intents.py`
- Test: `apps/backend/tests/test_typst_intents.py`

**Interfaces:**
- Consumes: `parse_projection`, `serialize_unit`, Task 4's `IntentIn`.
- Produces:

```python
class IntentError(Exception):
    def __init__(self, code: str, detail: str = "") -> None   # code: "expect_mismatch" | "unsupported"
def apply_intent(source: str, projection: Projection, intent: IntentIn) -> str
```

- [ ] **Step 1: Write the failing tests**

```python
def test_set_arg_replaces_one_span_and_nothing_else():
    src = read("synthetic-en"); p = parse_projection(src)
    u = next(x for x in p.units.values() if x.key == "title")
    out = apply_intent(src, p, IntentIn(kind="set_arg", unit=u.id,
                                        expect=u.value, value="Staff Engineer"))
    assert out[:u.span[0]] == src[:u.span[0]]
    assert out[u.span[0] + len("Staff Engineer"):] == src[u.span[1]:]

def test_set_line_rebuilds_marks():
    src = read("synthetic-marks"); p = parse_projection(src)
    u = unit_by_text(p, "Cut p95 from 1.8s to 240ms")
    out = apply_intent(src, p, IntentIn(kind="set_line", unit=u.id, expect=u.value,
        text="Cut p95 to 180ms", marks=[MarkOut(kind="strong", start=4, end=7)]))
    assert "*p95*" in out and parse_projection(out).tier == "A"

def test_set_line_keeps_this_unit_s_escape_policy():
    src = read("synthetic-marks"); p = parse_projection(src)
    u = unit_by_text(p, "Systems Analysis & Design")
    out = apply_intent(src, p, IntentIn(kind="set_line", unit=u.id, expect=u.value,
                                       text="Systems Analysis & Design II", marks=[]))
    assert "Analysis \\& Design II" in out

def test_emptied_line_stays_a_valid_bullet():                  # Review Focus 3
    src = read("synthetic-marks"); p = parse_projection(src)
    u = unit_by_text(p, "Cut p95 from 1.8s to 240ms")
    out = apply_intent(src, p, IntentIn(kind="set_line", unit=u.id, expect=u.value,
                                       text="", marks=[]))
    assert "**" not in out and "- \n" in out
    assert parse_projection(out) is not None

def test_expect_mismatch_is_refused():
    src = read("synthetic-en"); p = parse_projection(src)
    u = next(x for x in p.units.values() if x.key == "title")
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(kind="set_arg", unit=u.id,
                                      expect="something else", value="x"))
    assert e.value.code == "expect_mismatch"

def test_a_value_that_would_reparse_as_structure_is_refused():
    # Found in Task 3: `serialize_unit` does not guard a value starting with `- ` or
    # `= `, so a paragraph whose text begins that way writes a line that re-parses as
    # a bullet or a heading — the document's structure changes behind the person.
    src = read("synthetic-en"); p = parse_projection(src)
    u = next(x for x in p.units.values() if x.role == "para")
    # Typst has five structural line openers: `-` and `+` are list items, `1.` is an
    # enum, `/ ` is a term list, `=` is a heading. A value beginning with any of them
    # re-parses as structure.
    for bad in ["- not a bullet", "+ not a bullet", "1. not an enum",
                "/ not a term: x", "= not a heading"]:
        with pytest.raises(IntentError) as e:
            apply_intent(src, p, IntentIn(kind="set_line", unit=u.id, expect=u.value,
                                          text=bad, marks=[]))
        assert e.value.code == "unsupported"
    # And the guard must track the parser rather than a prefix blacklist, or it
    # refuses edits that are safe: none of these re-parse as structure, so all of
    # them must be accepted. A guard stricter than the parser is a form that will
    # not let someone type their own numbers.
    for ok in ["=5% YoY", "-20% latency", "+15 points", "1.8s to 240ms",
               "C/C++ and /dev/null"]:
        apply_intent(src, p, IntentIn(kind="set_line", unit=u.id, expect=u.value,
                                      text=ok, marks=[]))

def test_a_read_only_unit_is_refused():
    src = read("synthetic-tier-b"); p = parse_projection(src)
    u = next(x for x in p.units.values() if not x.editable)
    with pytest.raises(IntentError) as e:
        apply_intent(src, p, IntentIn(kind="set_line", unit=u.id, expect=u.value,
                                      text="x", marks=[]))
    assert e.value.code == "unsupported"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_intents.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.typst_intents`

- [ ] **Step 3: Implement `apply_intent` for `set_arg` and `set_line`**

Pure: source in, source out, no DB and no compile. `expect` is compared against the unit's
current `value` before anything is written. `set_arg` writes `typst_value`-escaped text
into the span; `set_line` builds a `Unit` with the new `text`/`marks` and this unit's own
`escapes`, then writes `serialize_unit` of it. An empty `text` writes an empty span and
drops every mark. Raise `IntentError("unsupported")` for a unit that is not editable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_intents.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/typst_intents.py apps/backend/tests/test_typst_intents.py
git commit -m "feat(typst): apply value intents to a resume's source"
```

---

### Task 6: Edit endpoint — guards, compile gate, revision, response

**Files:**
- Modify: `apps/backend/app/schemas_editing.py`, `apps/backend/app/routers/resumes.py`
- Test: `apps/backend/tests/test_edit_api.py`

**Interfaces:**
- Consumes: `apply_intent`, `IntentError`, `compile_typst`, `compile_preview_pages`,
  `set_draft_source`, `latest_revision`, `source_hash`.
- Produces: `POST /v1/resumes/{resume_id}/edit` → `EditOut` with
  `{hash, typst_source, projection, preview}`; error codes `stale_hash`,
  `expect_mismatch`, `compile_failed`, `unsupported` in `detail["code"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_edit_writes_source_and_returns_the_new_projection(client, resume_with(FIXTURE_EN)):
    body = intent_for_title(client, rid, "Staff Engineer")
    r = client.post(f"/v1/resumes/{rid}/edit", json=body)
    assert r.status_code == 200
    out = r.json()
    assert "Staff Engineer" in out["typst_source"]
    assert out["hash"] == source_hash(out["typst_source"])
    assert out["preview"]["pages"]

def test_stale_hash_is_refused_and_writes_nothing(client, resume_with(FIXTURE_EN), db):
    body = intent_for_title(client, rid, "A"); body["hash"] = "0" * 64
    before = db.get(models.Resume, rid).typst_source
    r = client.post(f"/v1/resumes/{rid}/edit", json=body)   # Review Focus 2
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_hash"
    assert db.get(models.Resume, rid).typst_source == before

def test_compile_failure_writes_nothing(client, resume_with(FIXTURE_EN), db):
    before = db.get(models.Resume, rid).typst_source
    r = client.post(f"/v1/resumes/{rid}/edit",
                    json=intent_for_title(client, rid, 'He said "hi'))
    assert r.json()["detail"]["code"] == "compile_failed"
    assert db.get(models.Resume, rid).typst_source == before
    assert db.query(models.ResumeRevision).count() == 1

def test_value_edit_does_not_coalesce_into_someone_else_s_revision(client, resume_with(FIXTURE_EN), db):
    set_draft_source(db, resume, FIXTURE_EN + "\n// chat wrote this\n")  # Invariant 8
    db.commit()
    n = db.query(models.ResumeRevision).count()
    client.post(f"/v1/resumes/{rid}/edit", json=intent_for_title(client, rid, "B"))
    assert db.query(models.ResumeRevision).count() == n + 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_edit_api.py -v`
Expected: FAIL — 404, route not registered

- [ ] **Step 3: Implement the `/edit` handler**

`async def edit(...)`. Order: load the resume, compare `body.hash` against
`source_hash(resume.typst_source)` (409 `stale_hash`), `apply_intent`,
`await asyncio.to_thread(compile_typst, candidate, "pdf")` inside `try` (400
`compile_failed` with the first stderr line), then `db.refresh(resume)` and re-check the
hash before writing. Coalesce only when `body.hash == latest_revision(...).content_hash`,
else `coalesce=False`. Reuse that one compile for the response's `preview` by calling
`compile_preview_pages` through `asyncio.to_thread` as well. Map `IntentError.code` to
400 with `detail={"code": ...}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_edit_api.py tests/test_typst_intents.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/schemas_editing.py apps/backend/app/routers/resumes.py \
  apps/backend/tests/test_edit_api.py
git commit -m "feat(api): apply a field edit behind a compile gate"
```

---

### Task 7: Structural intents

**Files:**
- Modify: `apps/backend/app/services/typst_intents.py`,
  `apps/backend/app/routers/resumes.py`
- Test: `apps/backend/tests/test_typst_intents.py`,
  `apps/backend/tests/test_edit_api.py`

**Interfaces:**
- Consumes: Task 5's `apply_intent`, Task 6's `/edit` handler.
- Produces: the same `apply_intent` handling `insert_bullet`, `insert_item`,
  `insert_section`, `delete`, `move_section`; plus
  `def normalize_blanks(source: str) -> str` and
  `def is_structural(kind: str) -> bool` — the `/edit` handler passes
  `coalesce=not is_structural(body.kind)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_insert_bullet_lands_at_the_block_s_content_edge():
    src = read("synthetic-en"); p = parse_projection(src)
    b = first_comp(p)
    out = apply_intent(src, p, IntentIn(kind="insert_bullet", target=b.id, hash=h(src)))
    assert len(first_comp(parse_projection(out)).bullets) == len(b.bullets) + 1

def test_insert_item_copies_the_section_s_own_shape():
    src = read("synthetic-en"); p = parse_projection(src)
    sec = section_named(p, "Projects")
    out = apply_intent(src, p, IntentIn(kind="insert_item", target=sec.id, hash=h(src)))
    comps = [b for b in section_named(parse_projection(out), "Projects").blocks
             if b.kind == "comp"]
    assert comps[-1].name == "project"        # not "work"

def test_delete_takes_the_outer_span_and_leaves_no_orphan():
    src = read("synthetic-en"); p = parse_projection(src)
    b = first_comp(p)
    out = apply_intent(src, p, IntentIn(kind="delete", target=b.id, hash=h(src)))
    assert "\n\n\n" not in out and not out.lstrip().startswith("-")

def test_deleting_the_last_item_takes_the_heading_too():
    src = read("synthetic-en"); p = parse_projection(src)
    sec = only_child_section(p)
    out = apply_intent(src, p, IntentIn(kind="delete", target=sec.blocks[0].id, hash=h(src)))
    assert sec.title_unit and p.units[sec.title_unit].value not in out

def test_move_section_swaps_two_outer_spans():
    src = read("synthetic-en"); p = parse_projection(src)
    titles = [p.units[s.title_unit].value for s in p.sections if s.title_unit]
    out = apply_intent(src, p, IntentIn(kind="move_section",
                                        target=p.sections[-1].id, dir="up", hash=h(src)))
    after = [p2.units[s.title_unit].value
             for s in (p2 := parse_projection(out)).sections if s.title_unit]
    assert after[-2:] == [titles[-1], titles[-2]]

def test_a_structural_edit_never_coalesces(client, resume_with(FIXTURE_EN), db):
    # tests/test_edit_api.py — a structure change is always its own revision, so that
    # undo reaches the document as it was before the block disappeared
    n = db.query(models.ResumeRevision).count()
    body = intent_for_title(client, rid, "A")                      # a value edit first
    client.post(f"/v1/resumes/{rid}/edit", json=body)
    p = client.get(f"/v1/resumes/{rid}/projection").json()
    client.post(f"/v1/resumes/{rid}/edit", json={
        "hash": p["hash"], "kind": "insert_bullet", "target": first_comp_id(p)})
    assert db.query(models.ResumeRevision).count() == n + 2        # not coalesced

def test_five_add_delete_cycles_leave_the_file_byte_identical():     # Invariant 4
    src = read("synthetic-en")
    out = src
    for _ in range(5):
        p = parse_projection(out)
        out = apply_intent(out, p, IntentIn(kind="insert_bullet",
                                           target=first_comp(p).id, hash=h(out)))
        p = parse_projection(out)
        out = apply_intent(out, p, IntentIn(kind="delete",
                                            target=first_comp(p).bullets[-1].id, hash=h(out)))
    assert out == src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_intents.py -k structural -v`
Expected: FAIL — `IntentError("unsupported")` for `insert_bullet`

- [ ] **Step 3: Implement the five structural intents**

`delete` cuts `Block.outer` (or `Section.outer` when the section's last contentful block
goes). `insert_bullet` splices at `Block.content_end`. `insert_item` copies the source
text of the section's last `comp`, replacing each of its string-literal arg spans with
`（待填）`. `insert_section` appends `== 新段落\n\n（待填）\n\n` and is
`IntentError("unsupported")` when the document has no `== ` heading. `move_section` is one
three-part splice. Every structural result goes through `normalize_blanks` (collapse runs
of three or more newlines to two, ensure a trailing newline) — that normaliser is what
makes insert and delete each other's inverse.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_intents.py tests/test_edit_api.py -v`
Expected: PASS (12 + 5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/typst_intents.py apps/backend/app/routers/resumes.py \
  apps/backend/tests/test_typst_intents.py apps/backend/tests/test_edit_api.py
git commit -m "feat(typst): insert, delete and move blocks by outer span"
```

---

### Task 8: Anchor bands for the preview

**Files:**
- Create: `apps/backend/app/services/typst_anchors.py`
- Modify: `apps/backend/app/routers/resumes.py` (return `bands` in `EditOut.preview`
  and from `/preview`)
- Test: `apps/backend/tests/test_typst_anchors.py`

**Interfaces:**
- Consumes: `parse_projection`, `typst_binary`, `extract_text_layer`.
- Produces:

```python
@dataclass(frozen=True)
class Band: page: int; y: float; height: float
def label_shadow(source: str, projection: Projection) -> str
def anchor_positions(shadow: str) -> dict[str, tuple[int, float]]   # unit id -> (page, y)
def anchor_bands(source: str, projection: Projection) -> dict[str, list[Band]]
```

- [ ] **Step 1: Write the failing tests**

```python
pytestmark = pytest.mark.skipif(not typst_available(), reason="typst binary not available")

def test_labels_do_not_move_the_layout():                      # Invariant 5
    src = read("synthetic-en"); p = parse_projection(src)
    plain = extract_text_layer(compile_typst(src, "pdf"))
    shadow = extract_text_layer(compile_typst(label_shadow(src, p), "pdf"))
    assert [len(x["spans"]) for x in plain] == [len(x["spans"]) for x in shadow]
    for a, b in zip(plain, shadow):
        for sa, sb in zip(a["spans"], b["spans"]):
            assert abs(sa["y"] - sb["y"]) < 0.001 and sa["text"] == sb["text"]

def test_every_line_unit_gets_a_position():
    src = read("synthetic-en"); p = parse_projection(src)
    pos = anchor_positions(label_shadow(src, p))
    assert {u.id for u in p.units.values() if u.role} <= set(pos)

def test_a_wrapped_line_yields_one_band_per_visual_line():     # Review Focus 5
    src = read("synthetic-long")      # a 2000-char bullet, added to the fixture set here
    p = parse_projection(src)
    bands = anchor_bands(src, p)
    long_unit = max((u for u in p.units.values() if u.role == "bullet"),
                    key=lambda u: len(u.value))
    assert len(bands[long_unit.id]) >= 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_anchors.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.typst_anchors`

- [ ] **Step 3: Implement the three functions**

`label_shadow` appends ` <uNN>` at each `line` unit's span end, descending, so earlier
offsets stay valid — a bare label is metadata and measurably does not move the layout,
while a `#metadata(...)` element does. `anchor_positions` runs
`typst eval --root <tmpdir> --format json 'query(selector(label("u0")).or(label("u1"))…).map(it => (id: str(it.label), p: it.location().page(), y: it.location().position().y.pt()))' --in <shadow>`
through the same binary resolution and font/package paths `compile_typst` uses, and parses
the JSON array. `anchor_bands` assigns each text-layer line to the last unit whose
`(page, y)` is at or above it, then emits one `Band` per line. Cache by
`source_hash(source)` in a module-level dict capped at 32 entries.

Also add `synthetic-long.typ` (one 2000-character bullet) to the Task 1 fixture set and to
`NAMES` in `test_typst_fixtures.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_anchors.py tests/test_typst_fixtures.py -v`
Expected: PASS (3 + 8 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/typst_anchors.py apps/backend/tests/test_typst_anchors.py \
  apps/backend/tests/fixtures/typst/synthetic-long.typ apps/backend/tests/test_typst_fixtures.py \
  apps/backend/app/routers/resumes.py
git commit -m "feat(typst): locate each line on the rendered page"
```

---

### Task 9: Frontend pure modules

**Files:**
- Create: `apps/frontend/lib/rich-marks.ts`, `apps/frontend/lib/rich-marks.test.ts`
- Create: `apps/frontend/lib/field-ownership.ts`, `apps/frontend/lib/field-ownership.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces:

```ts
export type Mark = { kind: "strong" | "emph" | "link"; start: number; end: number; url?: string };
export function marksToHtml(text: string, marks: Mark[], opaque: string[]): string;
export function domToMarks(root: HTMLElement): { text: string; marks: Mark[] };
export type SaveOwner = "shell" | "fields";
export function mayPutSource(owner: SaveOwner): boolean;
```

- [ ] **Step 1: Write the failing tests**

```ts
// rich-marks.test.ts — node:test + assert/strict
test("marksToHtml nests a link inside bold", () => { /* assert on the <b><a> shape */ });
test("domToMarks is the inverse of marksToHtml", () => {
  // build the HTML, parse it with a minimal node tree, assert text and marks come back equal
});
test("domToMarks keeps an opaque chip verbatim", () => { /* data-raw survives */ });
test("domToMarks strips a browser-inserted <div>", () => { /* text is kept, no mark */ });
test("mark offsets are code points, not UTF-16 units", () => {
  // The server's offsets are Python code points. CJK agrees with UTF-16, but one
  // astral character is two UTF-16 units, so `.slice()` would be off by one per
  // emoji and bold the wrong words. Index with Array.from / the spread form.
  const text = "🚀 shipped *fast*";       // one astral char before the mark
  // assert the mark the module reports is the one that round-trips through the server's
  // code-point indexing, i.e. [...text].slice(start, end).join("") === "fast"
});

// field-ownership.test.ts
test("the shell may not PUT source while the fields tab owns it", () => {
  assert.equal(mayPutSource("fields"), false);
  assert.equal(mayPutSource("shell"), true);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/frontend && ./node_modules/.bin/tsx --test lib/rich-marks.test.ts lib/field-ownership.test.ts`
Expected: FAIL — cannot find module `./rich-marks`

- [ ] **Step 3: Implement both modules**

`marksToHtml` emits `<b>`, `<i>`, `<a data-url>` and
`<span data-r="op" contenteditable="false" data-raw>`; `domToMarks` walks `childNodes`,
accumulating plain text and recording a mark for each `b`/`strong`, `i`/`em` and `a`,
recursing so a link inside bold yields both. **Both functions index text by code point**
(`Array.from(text)` / `[...text]`), never by `.length` or `.slice`: the server's offsets
are Python code points, and one astral character is two UTF-16 units, so UTF-16 indexing
bolds the wrong words in any line containing an emoji. Unknown elements contribute their text and no
mark. `mayPutSource` is the whole guard: one owner at a time, defined here so it is
testable without a DOM.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/frontend && ./node_modules/.bin/tsx --test lib/rich-marks.test.ts lib/field-ownership.test.ts`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/lib/rich-marks.ts apps/frontend/lib/rich-marks.test.ts \
  apps/frontend/lib/field-ownership.ts apps/frontend/lib/field-ownership.test.ts
git commit -m "feat(editor): marks <-> DOM conversion and the source-ownership guard"
```

---

### Task 10: The fields tab

**Files:**
- Create: `apps/frontend/components/editor/StructuredPanel.tsx`,
  `apps/frontend/components/editor/RichLine.tsx`
- Modify: `apps/frontend/lib/api.ts`, `apps/frontend/lib/editor-tab.ts`,
  `apps/frontend/components/editor/EditorShell.tsx`,
  `apps/frontend/messages/{en,zh-TW,zh-CN}.json`

**Interfaces:**
- Consumes: `/projection`, `/edit`, `rich-marks`, `field-ownership`.
- Produces: `getProjection(id: string): Promise<Projection>`,
  `postEdit(id: string, body: IntentBody): Promise<EditResult>`; a `"fields"` value for
  the editor tab; `StructuredPanel` props
  `{ resumeId: string; source: string; onAdopt: (src: string) => void }`.

- [ ] **Step 1: Note that this task has no failing-test step**

This repo has no frontend component test runner (`AGENTS.md` §269: only pure modules get
`*.test.ts`, run by hand). Task 9 carries this task's testable logic. The gate here is
Step 4: the typecheck, the linter on the touched files, and the named manual check.

- [ ] **Step 2: Implement the client and the panel**

`api.ts` gains the two calls and their types, parsed defensively like `parsePreviewPages`
does. `StructuredPanel` renders sections → blocks → `arg` inputs and `RichLine`
contenteditables, with style args in a `<details>` collapsed by default, read-only units
shown with their reason, and nothing at all for `report.structures`. `RichLine` renders
`marksToHtml` and, on commit (blur, Enter, or 2s idle), sends `set_line` built from
`domToMarks`. Every input gets `text-base md:text-sm`. An `arg` row is a plain `<input>`,
so the popover of Task 12 cannot reach it — the row carries a short hint saying a string
argument cannot hold formatting, rather than letting someone try and get literal
asterisks in the PDF.

- [ ] **Step 3: Wire the tab and the ownership guard in `EditorShell.tsx`**

Add `"fields"` to the tab union and the tab strip. While that tab is mounted the shell
sets the owner to `"fields"`, and both the 400ms autosave and `flushSave()` consult
`mayPutSource` before any `putResumeSource`. Every `/edit` response is adopted with
`setSource(out.typst_source)` and `setPreviewUrls` / `setTextLayers` from `out.preview`.

- [ ] **Step 4: Verify**

Run: `cd apps/frontend && npm run build:public && ./node_modules/.bin/eslint components/editor/StructuredPanel.tsx components/editor/RichLine.tsx components/editor/EditorShell.tsx`
Expected: build succeeds; eslint reports no errors on those three files.

Then the manual check, in zh-TW: open a résumé, switch to 欄位, change a 職稱, switch to
原始碼 and back, leave the editor and return — the change must still be there (that is
Review Focus 2 and the autosave guard, which no automated test in this repo can cover).

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/components/editor/StructuredPanel.tsx \
  apps/frontend/components/editor/RichLine.tsx apps/frontend/lib/api.ts \
  apps/frontend/lib/editor-tab.ts apps/frontend/components/editor/EditorShell.tsx \
  apps/frontend/messages
git commit -m "feat(editor): a fields tab that edits values through intents"
```

---

### Task 11: Structural controls

**Files:**
- Modify: `apps/frontend/components/editor/StructuredPanel.tsx`,
  `apps/frontend/messages/{en,zh-TW,zh-CN}.json`

**Interfaces:**
- Consumes: the five structural intents from Task 7.
- Produces: nothing new.

- [ ] **Step 1: Implement the controls**

Per block: a delete button that arms on the first click and shows how many units and bytes
go with it, and 「＋ 加一條要點」. Per section: ↑ / ↓ / delete and 「＋ 照這個段落的寫法
加一筆」. One 「＋ 加一個新段落」 at the end, hidden when `insert_section` is unsupported
for this document.

- [ ] **Step 2: Verify**

Run: `cd apps/frontend && npm run build:public && ./node_modules/.bin/eslint components/editor/StructuredPanel.tsx`
Expected: build succeeds, no eslint errors.

Manual, in zh-TW: add a bullet, delete it, move a section up and down, undo through the
history panel — the document returns to where it started.

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/components/editor/StructuredPanel.tsx apps/frontend/messages
git commit -m "feat(editor): add, delete and move blocks from the fields tab"
```

---

### Task 12: Selection popover for bold, italic and links

**Files:**
- Create: `apps/frontend/components/editor/FormatPopover.tsx`
- Modify: `apps/frontend/components/editor/RichLine.tsx`,
  `apps/frontend/messages/{en,zh-TW,zh-CN}.json`

**Interfaces:**
- Consumes: `domToMarks`.
- Produces: `FormatPopover` props
  `{ host: HTMLElement | null; onChanged: () => void }`.

- [ ] **Step 1: Implement the popover**

Shows beside the selection when there is one inside a `RichLine`, or when the caret sits
inside an `<a>`. Buttons B / I / 連結 (／改網址 ＋ 移除), `⌘B` / `⌘I` / `⌘K`, flipping below
the selection when there is no room above and clamped to the panel's width. Every button
cancels `mousedown` so the surface keeps focus and its selection. **Opening the URL row
clones the `Range` and restores it before wrapping** — focusing the input drops the
selection otherwise, which is the whole reason the old top-of-card toolbar felt wrong.
Bold and italic go through `document.execCommand` after
`execCommand("styleWithCSS", false, false)`; a link is wrapped by hand with
`range.extractContents()`. Each change commits the line through `set_line`.

- [ ] **Step 2: Verify**

Run: `cd apps/frontend && npm run build:public && ./node_modules/.bin/eslint components/editor/FormatPopover.tsx components/editor/RichLine.tsx`
Expected: build succeeds, no eslint errors.

Manual, in zh-TW: select two words in a bullet, `⌘B`; select two more, `⌘K`, type a URL,
Enter; click the link again and change its URL; put a link inside bold. After each, switch
to 原始碼 and confirm the Typst reads the way the spec says it should.

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/components/editor/FormatPopover.tsx \
  apps/frontend/components/editor/RichLine.tsx apps/frontend/messages
git commit -m "feat(editor): format a selection where the selection is"
```

---

### Task 13: Preview line highlight

**Files:**
- Create: `apps/frontend/components/editor/PreviewHighlight.tsx`
- Modify: `apps/frontend/components/editor/EditorPreview.tsx`,
  `apps/frontend/components/editor/EditorShell.tsx`,
  `apps/frontend/components/editor/StructuredPanel.tsx`

**Interfaces:**
- Consumes: `bands` from Task 8's `/edit` and `/preview` responses.
- Produces: `PreviewHighlight` props
  `{ bands: Band[]; kind: "edit" | "delete"; pageWidth: number }`.

- [ ] **Step 1: Implement the band layer**

A sibling of `PreviewTextLayer` inside the same page box, laid out in `cqw` exactly the way
that component does, so it tracks zoom and resize without measuring. The focused unit's
bands get the accent treatment; an armed delete gets the danger one over every unit inside
the block. `StructuredPanel` reports the focused unit id upward; `EditorShell` holds it.

- [ ] **Step 2: Verify**

Run: `cd apps/frontend && npm run build:public && ./node_modules/.bin/eslint components/editor/PreviewHighlight.tsx components/editor/EditorPreview.tsx`
Expected: build succeeds, no eslint errors.

Manual, in zh-TW: click a bullet that wraps — the highlight must cover every line of it and
no neighbour; arm a delete on a block — the band must cover that block only; zoom the
preview — the bands must stay on their lines.

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/components/editor/PreviewHighlight.tsx \
  apps/frontend/components/editor/EditorPreview.tsx \
  apps/frontend/components/editor/EditorShell.tsx \
  apps/frontend/components/editor/StructuredPanel.tsx
git commit -m "feat(editor): highlight the rendered line a field owns"
```

---

### Task 14: Emitter-backed snippet for an empty section

**Files:**
- Modify: `apps/backend/app/services/typst_intents.py`
- Test: `apps/backend/tests/test_typst_intents.py`

**Interfaces:**
- Consumes: `app/services/emitters/_common.py` (`heading_block`, `bullet_lines`,
  `dates_expr`), `app/services/emitters/__init__.py` (`emitter_for`).
- Produces: nothing new.

- [ ] **Step 1: Write the failing tests**

```python
def test_insert_item_into_an_empty_section_uses_the_emitter(monkeypatch):
    src = read("synthetic-en").replace(WORK_BLOCK, "")      # leave the heading, no blocks
    p = parse_projection(src)
    out = apply_intent(src, p, IntentIn(kind="insert_item",
                                        target=section_named(p, "Experience").id, hash=h(src)))
    assert "dates-helper(" in out

def test_emit_parse_round_trips_for_every_pinned_emitter():            # Invariant 2
    for name, version in deterministic_pairs():
        source = emitter_for(name, version).emit(GOLDEN_CONTENT).source
        p = parse_projection(source)
        out = source
        for u in sorted(p.units.values(), key=lambda x: -x.span[0]):
            out = out[:u.span[0]] + serialize_unit(u) + out[u.span[1]:]
        assert out == source, name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_intents.py -k emitter -v`
Expected: FAIL — the empty-section insert raises `unsupported`

- [ ] **Step 3: Implement the fallback**

When a section has no `comp` to copy, look up `emitter_for(name, version)` from the
document's `#import` line; if there is one, build the snippet from `_common.py`'s helpers,
otherwise keep raising `IntentError("unsupported")`. Reuse
`tests/fixtures/golden_content.py` for `GOLDEN_CONTENT`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_typst_intents.py tests/test_typst_projection.py -v`
Expected: PASS (14 + 14 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/typst_intents.py apps/backend/tests/test_typst_intents.py
git commit -m "feat(typst): fall back to the emitter's own grammar for an empty section"
```
