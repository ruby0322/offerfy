# Company Boilerplate Stripping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `function_of` reading a company's mission statement: exclude
sentences that recur across most of one company's postings before matching
any description needle.

**Architecture:** A new `company_boilerplate` table stores, per company, the
sha256 of every sentence appearing in at least half that company's active
postings. `enrich_jobs` strips those sentences from `description_text`
before calling `function_of`, and hashes the *stripped* text into
`JobFacets.description_hash`, so a boilerplate change invalidates the gate
by itself and the next enrichment re-derives. Recompute runs once per
catalog pass, and re-enriches the postings of any company whose set
changed — necessary because the Workday pipeline enriches only
newly-upserted ids and five of the six largest employers in the catalogue
are on it.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, pytest (SQLite
in-memory via `tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-11-12-job-discovery-loop-design.md`
— this plan amends Slice 1, whose `function_of` design the production
catalogue falsified. The spec's own rationale ("a title is the employer's
deliberate choice of what the role is; a description is boilerplate that
happens to mention adjacent technologies") anticipated this failure and
mitigated it only with title-beats-description, which does nothing for the
75% of this catalogue whose titles carry no function signal.

## Evidence this plan argues from

Measured against the live catalogue (21,773 active postings) on 2026-11-12,
after the forced re-enrichment that fixed the `llm`-inside-"fulfillment"
needle:

- `ml` held 5,328 postings (24.2%). **4,792 of them (90%) were classified
  by description, not title.**
- All 400 sampled Anduril postings shared **one identical sentence** —
  *"…Anduril is committed to bringing cutting-edge autonomy, AI, computer
  vision, sensor fusion, and networking technology…"* — filing 1,813 of
  Anduril's 2,375 active postings (76%) as machine learning, including
  "People Business Partner", "Senior Buyer" and "Senior Aerodynamics
  Engineer".
- Candidate fixes, measured end to end:

  | | current | title-only | needle ≥2 occurrences | **this plan** |
  |---|---|---|---|---|
  | `other` | 28.0% | 75.0% | 60.3% | **34.1%** |
  | `ml` | 24.2% | 2.5% | 6.6% | **15.9%** |

  Title-only and the occurrence threshold both fix `ml` by making the
  filter bar useless. This plan is the only one that keeps `other`
  near its current share.
- A document-frequency cutoff of 0.30 and one of 0.50 produce
  **byte-identical** output, because boilerplate is bimodal: a sentence
  sits in ~100% of a company's postings or well under 30%. The cutoff is
  therefore not a tuning knob, and 0.50 is chosen as the one that strips
  less.
- 1,806 boilerplate sentences across 87 companies — small enough to store
  per company as a JSON array of hashes.

Residual, and not in scope: even at 15.9% `ml` is probably still somewhat
inflated. The remainder is Micron and NVIDIA, where ML vocabulary pervades
genuinely varied, non-boilerplate text. No sentence-frequency method
reaches that.

## Global Constraints

- `enrich_jobs` flushes but never commits — the caller owns the
  transaction. Every new DB helper in this plan follows the same contract.
- A derivation failure must cost only the facets row, never the posting,
  the pass or the commit. Both ingest call sites already wrap enrichment in
  `db.begin_nested()`; anything this plan adds inside a pass gets the same
  containment.
- Needle vocabularies live in `data/job_taxonomy.json`, never hardcoded in
  Python. This plan adds no needles, so it adds no JSON.
- Thresholds are module-level named constants in
  `app/services/jobs/boilerplate.py`, not literals at the call site:
  `BOILERPLATE_MIN_POSTINGS = 5`, `BOILERPLATE_DOC_FREQUENCY = 0.5`,
  `MIN_SENTENCE_CHARS = 25`.
- `ensure_schema()` is additive `create_all` and is called only from
  `app/main.py` lifespan. The Alembic revision is checked by eye against
  the model, as `028_job_facets.py` states — no test runs Alembic.
- The suite has 2 pre-existing failures in `tests/test_grants.py` and one
  pre-existing `tsc` error in `lib/admin-ingest.test.ts`, both reproducible
  at merge-base. Neither is this plan's to fix; the suite is green at
  1116 passing plus those 2.
- Never point `DATABASE_URL` at the live Postgres from a test or a
  migration. The only live Postgres on this host is production.

## Review Focus

Input classes the design implies that no task's happy path exercises, most
likely to bite first. Each has its test assigned to the task that owns the
code.

1. **A company whose every sentence is boilerplate** (near-duplicate
   postings) strips to the empty string; `function_of` must fall back to
   the title and return `other`, not crash or match on `" "`. — Task 1
2. **A needle spanning a junction created by stripping**: removing a middle
   sentence must not join two survivors into text that matches a needle
   neither contained. — Task 1
3. **CJK descriptions** have no `[.!?]` terminators, so the splitter yields
   one giant sentence; that sentence is then either wholly boilerplate or
   wholly kept. Must not crash, and a CJK posting must not lose its whole
   description because two postings share it. — Task 1
4. **Company names that differ only by case or surrounding whitespace**
   across boards key to different boilerplate rows, silently halving each
   one's document frequency below the cutoff. — Task 3
5. **A company dropping below `BOILERPLATE_MIN_POSTINGS`** between
   recomputes must have its stale boilerplate row removed, or postings keep
   being stripped against a set computed from a corpus that no longer
   exists. — Task 3

---

### Task 1: Sentence splitting and boilerplate arithmetic

Pure functions, no database. This is the whole algorithm; Tasks 2–5 only
give it storage and call sites.

**Files:**
- Create: `apps/backend/app/services/jobs/boilerplate.py`
- Test: `apps/backend/tests/test_jobs_boilerplate.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `BOILERPLATE_MIN_POSTINGS: int = 5`
  - `BOILERPLATE_DOC_FREQUENCY: float = 0.5`
  - `MIN_SENTENCE_CHARS: int = 25`
  - `RECOMPUTE_BATCH_SIZE: int = 500` — declared here, **not** imported
    from `enrich._BACKFILL_BATCH_SIZE`. Task 4 makes `enrich` import from
    this module, so an import the other way would be circular.
  - `split_sentences(text: str) -> list[str]` — casefolded, stripped,
    only segments longer than `MIN_SENTENCE_CHARS`.
  - `sentence_hash(sentence: str) -> str` — sha256 hex of one
    already-split sentence.
  - `boilerplate_hashes(descriptions: Iterable[str]) -> frozenset[str]` —
    empty when the corpus is smaller than `BOILERPLATE_MIN_POSTINGS`.
  - `strip_boilerplate(text: str, hashes: frozenset[str]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def test_split_sentences_folds_and_drops_short_fragments():
    long_one = "This Sentence Is Comfortably Longer Than The Minimum."
    assert split_sentences(f"{long_one} Too short.") == [long_one.casefold()]


def test_boilerplate_is_the_sentence_in_at_least_half_the_corpus():
    mission = "Anduril is committed to bringing cutting-edge autonomy and computer vision."
    docs = [f"{mission} We need a {role}." for role in ("buyer", "thermal engineer")]
    docs.append("An unrelated posting with no shared sentence whatsoever here.")
    hashes = boilerplate_hashes(docs * 2)
    assert sentence_hash(mission.casefold()) in hashes
    assert len(hashes) == 1


def test_corpus_below_the_minimum_has_no_boilerplate():
    doc = "A sentence repeated across every single one of these few postings."
    assert boilerplate_hashes([doc] * (BOILERPLATE_MIN_POSTINGS - 1)) == frozenset()


def test_strip_removes_only_the_boilerplate_sentence():
    mission = "We bring cutting-edge autonomy and computer vision to the world."
    body = "You will own our payments ledger and its reconciliation batch."
    kept = strip_boilerplate(f"{mission} {body}", frozenset({sentence_hash(mission.casefold())}))
    assert "computer vision" not in kept
    assert "payments ledger" in kept


# Review Focus 1
def test_stripping_every_sentence_yields_empty_text_not_a_crash():
    only = "Every sentence in this posting is shared company boilerplate here."
    assert strip_boilerplate(only, frozenset({sentence_hash(only.casefold())})) == ""


# Review Focus 2 -- newline-separated bullets, which carry no terminator, so
# a space-join really would splice "computer" and "vision" into the needle.
def test_stripping_does_not_splice_a_needle_across_the_junction():
    first = "Tooling we use day to day includes a bespoke computer"
    middle = "This middle line is shared boilerplate and gets removed"
    last = "vision and robotics stack maintained by the platform team"
    kept = strip_boilerplate(
        "\n".join((first, middle, last)), frozenset({sentence_hash(middle.casefold())})
    )
    assert "computer vision" not in kept


# Review Focus 3
def test_cjk_description_without_terminators_survives_as_one_sentence():
    cjk = "我們正在尋找一位資深後端工程師負責支付系統的設計與維護工作"
    assert split_sentences(cjk) == [cjk]
    assert strip_boilerplate(cjk, frozenset()) == cjk
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_boilerplate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.jobs.boilerplate'`

- [ ] **Step 3: Implement the module**

Split on `re.split(r"(?<=[.!?])\s+|\n+", text)`. Casefold once, up front.
`strip_boilerplate` must rejoin survivors with `"\n"`, not `" "` — that is
what makes Review Focus 2 pass, because needles are space-padded and a
newline is not a space inside a padded needle.

`boilerplate_hashes` counts *distinct* sentences per document
(`set(split_sentences(d))`) so a sentence repeated inside one posting still
counts once toward document frequency, then keeps those at
`>= BOILERPLATE_DOC_FREQUENCY * len(corpus)`.

Write the module docstring to carry the two facts a later reader cannot
re-derive: that the 0.30/0.50 cutoffs measured byte-identical because
boilerplate is bimodal, and that `MIN_SENTENCE_CHARS` exists so a generic
short fragment ("Apply now.") is never treated as a company's boilerplate.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_boilerplate.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/boilerplate.py apps/backend/tests/test_jobs_boilerplate.py
git commit -m "feat(jobs): detect and strip per-company boilerplate sentences"
```

---

### Task 2: `company_boilerplate` table and migration

**Files:**
- Modify: `apps/backend/app/models.py` (add after `JobFacets`)
- Create: `apps/backend/alembic/versions/029_company_boilerplate.py`
- Test: `apps/backend/tests/test_jobs_boilerplate.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `CompanyBoilerplate` with columns
  `company: Mapped[str]` `String(255)` primary key,
  `sentence_hashes: Mapped[str]` `Text` (a JSON array of hex digests),
  `posting_count: Mapped[int]`,
  `computed_at: Mapped[datetime]` `DateTime(timezone=True)`.

- [ ] **Step 1: Write the failing test**

```python
def test_company_boilerplate_round_trips_its_hash_array(db_session):
    db_session.add(CompanyBoilerplate(
        company="Anduril",
        sentence_hashes=json.dumps(["a" * 64, "b" * 64]),
        posting_count=2375,
        computed_at=datetime.now(timezone.utc),
    ))
    db_session.flush()
    row = db_session.get(CompanyBoilerplate, "Anduril")
    assert json.loads(row.sentence_hashes) == ["a" * 64, "b" * 64]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_boilerplate.py::test_company_boilerplate_round_trips_its_hash_array -v`
Expected: FAIL — `ImportError: cannot import name 'CompanyBoilerplate'`

- [ ] **Step 3: Add the model**

`Text` holding a JSON array, not `JSON`/`JSONB` — the same idiom
`TalentProfile.profile_json` already uses in this file, and stated there:
nothing queries inside the blob.

Docstring must say why this is keyed on the free-text `company` string
rather than a board or source: boilerplate is a property of the employer's
ad template, and one employer can appear on two boards (Anduril on
greenhouse, Micron on workday) whose postings share the same mission
paragraph.

- [ ] **Step 4: Write the Alembic revision**

`revision = "029_company_boilerplate"`, `down_revision = "028_job_facets"`.
Plain `create_table`, no dialect guard, matching `028_job_facets.py`.
Docstring notes, as 028 does, that no test runs Alembic — conftest builds
the schema with `create_all`, so this file is checked by eye against the
model.

- [ ] **Step 5: Verify the test passes and the head is single**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_boilerplate.py -v && .venv/bin/alembic heads`
Expected: PASS, and exactly one head — `029_company_boilerplate (head)`

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/029_company_boilerplate.py apps/backend/tests/test_jobs_boilerplate.py
git commit -m "feat(jobs): add company_boilerplate table"
```

---

### Task 3: Recompute and load boilerplate from the database

**Files:**
- Modify: `apps/backend/app/services/jobs/boilerplate.py`
- Test: `apps/backend/tests/test_jobs_boilerplate.py` (append)

**Interfaces:**
- Consumes: Task 1's `boilerplate_hashes`, `split_sentences`; Task 2's
  `CompanyBoilerplate`.
- Produces:
  - `normalize_company(name: str) -> str` — `" ".join(name.split()).casefold()`.
    The single definition of the storage and lookup key; Task 4 imports it
    too.
  - `recompute_boilerplate(db: Session, *, now: datetime | None = None) -> set[str]`
    — rebuilds every company's row from active postings, returns the set of
    **normalized** company names whose hash set changed (including
    companies whose row was deleted). Flushes, never commits.
  - `load_boilerplate(db: Session, companies: Collection[str]) -> dict[str, frozenset[str]]`
    — one bulk query; keys are normalized names, and companies with no row
    are absent from the mapping.

**Test helper:** these tests need real `Job` rows, and
`tests/test_jobs_enrich.py`'s `_add_job` is private to that module. Add a
local `_add_jobs(db_session, company, descriptions, *, title="Engineer")`
to `tests/test_jobs_boilerplate.py`, modelled on
`tests/test_jobs_enrich.py:18`. `Job.source_id` is required and carries a
`jobs_source_source_id_key` unique constraint, so the helper must mint a
distinct one per row.

- [ ] **Step 1: Write the failing tests**

```python
MISSION = "We bring cutting-edge autonomy and computer vision to every mission."
BODY = "You will own supplier contracts and the quarterly sourcing review."


def test_recompute_stores_the_shared_sentence_and_reports_the_change(db_session):
    _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    assert recompute_boilerplate(db_session) == {"anduril"}
    assert sentence_hash(MISSION.casefold()) in load_boilerplate(db_session, ["Anduril"])["anduril"]


def test_recompute_is_idempotent_and_reports_no_change_the_second_time(db_session):
    _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    recompute_boilerplate(db_session)
    assert recompute_boilerplate(db_session) == set()


def test_inactive_postings_are_not_part_of_the_corpus(db_session):
    jobs = _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    for job in jobs[:3]:
        job.is_active = False
    db_session.flush()
    recompute_boilerplate(db_session)
    assert load_boilerplate(db_session, ["Anduril"]) == {}


# Review Focus 5
def test_company_falling_below_the_minimum_loses_its_stale_row(db_session):
    jobs = _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(6)])
    recompute_boilerplate(db_session)
    for job in jobs[:2]:
        job.is_active = False
    db_session.flush()
    assert recompute_boilerplate(db_session) == {"anduril"}
    assert load_boilerplate(db_session, ["Anduril"]) == {}
    assert db_session.get(CompanyBoilerplate, "anduril") is None


# Review Focus 4
def test_company_names_differing_by_case_or_space_share_one_row(db_session):
    # Three each: below BOILERPLATE_MIN_POSTINGS apart, at it together.
    _add_jobs(db_session, "Anduril", [f"{MISSION} {BODY} Role {i}." for i in range(3)])
    _add_jobs(db_session, "  anduril ", [f"{MISSION} {BODY} Role {i}." for i in range(3, 6)])
    assert recompute_boilerplate(db_session) == {"anduril"}
    assert sentence_hash(MISSION.casefold()) in load_boilerplate(db_session, ["Anduril"])["anduril"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_boilerplate.py -v`
Expected: FAIL — `ImportError: cannot import name 'recompute_boilerplate'`

- [ ] **Step 3: Implement both functions**

`recompute_boilerplate` groups active postings by a **normalized** company
key — `" ".join(company.split()).casefold()` — and stores the row under
that normalized key, which is what makes Review Focus 4 pass. Document in
the docstring that `load_boilerplate` must normalize its lookup the same
way, and give both the one shared helper `normalize_company(name: str) -> str`
rather than two copies of the expression.

Select only `Job.company` and `Job.description_text`, and walk them with
`yield_per(RECOMPUTE_BATCH_SIZE)` — the whole catalogue's
`description_text` is ~200MB and must never be resident at once.
Accumulate per-company sentence-hash counters and a per-company posting
count, never the texts themselves.

Delete the rows of companies that no longer clear
`BOILERPLATE_MIN_POSTINGS`, and count those deletions as changes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_boilerplate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/boilerplate.py apps/backend/tests/test_jobs_boilerplate.py
git commit -m "feat(jobs): recompute and load per-company boilerplate"
```

---

### Task 4: Strip boilerplate inside `enrich_jobs`

The behavioural change. Everything before this task is inert.

**Files:**
- Modify: `apps/backend/app/services/jobs/enrich.py`
  (`_ENRICH_COLUMNS`, `_enrich_job_rows`, `description_hash` docstring)
- Modify: `apps/backend/app/models.py` (`JobFacets.description_hash` docstring)
- Test: `apps/backend/tests/test_jobs_enrich.py` (append)

**Interfaces:**
- Consumes: Task 3's `load_boilerplate`, `normalize_company`; Task 1's
  `strip_boilerplate`.
- Produces: no signature changes. `enrich_jobs` keeps
  `(db, *, job_ids=None, force=False, now=None) -> int`.

- [ ] **Step 1: Write the failing tests**

These go in `tests/test_jobs_enrich.py`, which already has the `_add_job`
helper (`tests/test_jobs_enrich.py:18`); `source_id` is required and must
be unique per row.

```python
MISSION = "We bring cutting-edge autonomy and computer vision to every mission."
SOURCING = "You will own supplier contracts and the quarterly sourcing review."


def _anduril_buyers(db_session, count):
    return [
        _add_job(db_session, source_id=f"a{i}", company="Anduril",
                 title=f"Senior Buyer {i}", description_text=f"{MISSION} {SOURCING}")
        for i in range(count)
    ]


def test_boilerplate_sentence_does_not_decide_the_function(db_session):
    _anduril_buyers(db_session, 6)
    db_session.flush()
    recompute_boilerplate(db_session)
    enrich_jobs(db_session)
    assert {f.function for f in db_session.query(JobFacets).all()} == {"other"}


def test_a_real_ml_posting_keeps_its_function(db_session):
    _anduril_buyers(db_session, 5)
    _add_job(db_session, source_id="perception", company="Anduril",
             title="Perception Engineer",
             description_text=f"{MISSION} You will train deep learning models for perception.")
    db_session.flush()
    recompute_boilerplate(db_session)
    enrich_jobs(db_session)
    fn = {j.title: f.function
          for j, f in db_session.query(Job, JobFacets).join(JobFacets, JobFacets.job_id == Job.id)}
    assert fn["Perception Engineer"] == "ml"
    assert fn["Senior Buyer 0"] == "other"


def test_the_hash_gate_reacts_to_a_boilerplate_change(db_session):
    jobs = _anduril_buyers(db_session, 6)
    db_session.flush()
    recompute_boilerplate(db_session)
    enrich_jobs(db_session)
    assert enrich_jobs(db_session) == 0          # nothing changed: gate holds
    for job in jobs[:2]:
        job.is_active = False
    db_session.flush()
    recompute_boilerplate(db_session)            # mission drops below the cutoff
    assert enrich_jobs(db_session) == 4          # rewritten unforced, by the hash alone
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_enrich.py -k boilerplate_or_hash_gate -v`
Expected: FAIL — the first asserts `{"ml"}` against expected `{"other"}`

- [ ] **Step 3: Wire stripping into the derivation**

Add `Job.company` to `_ENRICH_COLUMNS`. In `_enrich_job_rows`, bulk-load
boilerplate for the distinct normalized companies in the batch (one query
alongside the existing `JobFacets` lookup — keep the two-query shape the
`enrich_jobs` docstring promises; do not add a query per row), strip each
row's `description_text`, and pass the stripped text to **both**
`function_of` and `description_hash`.

Hashing the stripped text rather than the raw text is what makes the gate
self-invalidating: when a company's boilerplate changes, every one of its
postings hashes differently and is re-derived with no `force=True`. Say
this in `description_hash`'s docstring and in
`JobFacets.description_hash`'s, both of which currently assert the hash is
of `description_text` alone and will otherwise be wrong.

`seniority_of` and `location_facets` keep reading title and location, not
the description — unchanged.

- [ ] **Step 4: Run the full backend suite**

Run: `cd apps/backend && .venv/bin/pytest -q`
Expected: PASS, with only the 2 pre-existing `tests/test_grants.py`
failures. Several existing `test_jobs_enrich.py` cases assert a `function`
derived from a description; those stay green because a corpus under
`BOILERPLATE_MIN_POSTINGS` has no boilerplate at all. If one fails, it has
found a real behaviour change — report it rather than editing the
assertion.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/enrich.py apps/backend/app/models.py apps/backend/tests/test_jobs_enrich.py
git commit -m "fix(jobs): strip company boilerplate before deriving function"
```

---

### Task 5: Run recompute once per catalog pass

**Files:**
- Modify: `apps/backend/app/services/jobs/ingest.py` (`ingest_catalog`)
- Modify: `apps/backend/app/jobs/survey_facets.py`
- Test: `apps/backend/tests/test_jobs_ingest.py` (append)

**Interfaces:**
- Consumes: Task 3's `recompute_boilerplate`, `normalize_company`; Task 4's
  `enrich_jobs`.
- Produces: `refresh_boilerplate(db: Session, *, now: datetime | None = None) -> int`
  in `app/services/jobs/ingest.py` — recomputes, re-enriches the active
  postings of every changed company, returns the number of facet rows
  rewritten. Flushes, never commits. `ingest_catalog(db, client) -> CatalogResult`
  keeps its signature.

**Why a named function and not three lines inside `ingest_catalog`:** no
existing test calls `ingest_catalog` — the suite tests `ingest_board` and
monkeypatches `ingest_module.enrich_jobs` (see
`tests/test_jobs_ingest.py:396`). `ingest_catalog` needs the real board
config and live HTTP, so behaviour buried inside it is effectively
untestable. The behaviour goes in `refresh_boilerplate`, tested directly;
`ingest_catalog`'s own test only checks that it calls it.

**Test helpers:** these live in `tests/test_jobs_ingest.py`, which has
neither `_add_jobs` (Task 3 put that in `tests/test_jobs_boilerplate.py`)
nor the `MISSION`/`SOURCING` constants. Add a local copy of each; the
helper here also needs a `source` keyword, because these rows must be
`source="workday"` for the test to mean what it says.

- [ ] **Step 1: Write the failing tests**

```python
def test_refresh_reenriches_a_workday_company_that_no_pass_would_revisit(db_session):
    # Workday postings are never re-upserted by a catalog pass, so the
    # explicit re-enrichment inside refresh_boilerplate is the only thing
    # that can correct them. Seed 6 sharing a mission sentence and enrich
    # them BEFORE any boilerplate exists, so each starts mission-derived.
    _add_jobs(db_session, "Anduril", [f"{MISSION} {SOURCING}"] * 6, source="workday")
    db_session.flush()
    enrich_jobs(db_session)
    assert {f.function for f in db_session.query(JobFacets)} == {"ml"}

    assert refresh_boilerplate(db_session) == 6
    assert {f.function for f in db_session.query(JobFacets)} == {"other"}


def test_refresh_is_a_no_op_when_no_company_changed(db_session):
    _add_jobs(db_session, "Anduril", [f"{MISSION} {SOURCING}"] * 6, source="workday")
    db_session.flush()
    refresh_boilerplate(db_session)
    assert refresh_boilerplate(db_session) == 0


def test_catalog_pass_refreshes_boilerplate_before_the_board_loop(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(ingest_module, "refresh_boilerplate", lambda db, **kw: calls.append("refresh") or 0)
    monkeypatch.setattr(ingest_module, "load_boards", lambda: [])
    ingest_catalog(db_session, client=None)
    assert calls == ["refresh"]


def test_a_refresh_failure_does_not_fail_the_pass(db_session, monkeypatch):
    def boom(db, **kw):
        raise RuntimeError("derivation exploded")
    monkeypatch.setattr(ingest_module, "refresh_boilerplate", boom)
    monkeypatch.setattr(ingest_module, "load_boards", lambda: [])
    ingest_catalog(db_session, client=None)   # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_ingest.py -k boilerplate -v`
Expected: FAIL — `cannot import name 'refresh_boilerplate'`

- [ ] **Step 3: Implement `refresh_boilerplate` and call it from `ingest_catalog`**

`refresh_boilerplate` calls `recompute_boilerplate(db)`, then for each
changed normalized company re-enriches that company's active posting ids
via `enrich_jobs(db, job_ids=...)`, summing the rows written. Match the
company by the normalized key, not the raw string.

Call it in `ingest_catalog` before `for board in load_boards()`, so the
pass's own per-board enrichment already sees the fresh set.

The re-enrichment is not optional and is the reason this step cannot be a
bare recompute: `ingest_workday` enriches only `new_ids`, and five of the
six largest employers in the catalogue — Micron (2,731 postings), NVIDIA
(1,846), Applied Materials (1,813), Intel, NXP — are Workday boards whose
existing rows no pass would otherwise revisit. Put that reasoning in a
comment at the call site; it is not recoverable from the code.

Wrap the whole step in `db.begin_nested()` and swallow-and-record, the same
contract and for the same reason as the two existing `enrich_jobs` call
sites: a failure here must cost the facets, never the 119-board pass.

- [ ] **Step 4: Extend the facets survey**

Add to `survey(db)`'s read-only dict: `companies_with_boilerplate`,
`boilerplate_sentences_total`, and `description_driven_function` (postings
whose `function` is not `other` and whose title alone derives nothing).
That last number is the one that would have caught this bug — it stood at
4,792 of 5,328 for `ml`. Keep the script read-only; it must still never
call `enrich_jobs`.

- [ ] **Step 5: Run the full backend suite**

Run: `cd apps/backend && .venv/bin/pytest -q`
Expected: PASS, only the 2 pre-existing `tests/test_grants.py` failures.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/services/jobs/ingest.py apps/backend/app/jobs/survey_facets.py apps/backend/tests/test_jobs_ingest.py
git commit -m "feat(jobs): recompute boilerplate once per catalog pass"
```

---

### Task 6: Production remediation

The migration and the first recompute must be run by hand against
production, because this session cannot write to the live database without
the user running the command. Nothing here is automated.

**Files:**
- Create: `apps/backend/app/jobs/recompute_boilerplate.py` (CLI entrypoint,
  mirroring `app/jobs/ingest_catalog.py`'s `main(argv) -> int` shape)

**Interfaces:**
- Consumes: Task 3's `recompute_boilerplate`, Task 4's `enrich_jobs`.
- Produces: `python -m app.jobs.recompute_boilerplate`, printing
  `companies_changed=N boilerplate_sentences=N reenriched=N`.

- [ ] **Step 1: Write the entrypoint**

Opens a session, calls `recompute_boilerplate(db)`, then
`enrich_jobs(db, force=True)`, commits, prints the counts, returns 0. Force
is right here and only here: the very first recompute must rewrite rows
whose stored hash predates the hash-input change in Task 4.

- [ ] **Step 2: Verify against a throwaway Postgres, never production**

Migrations have no test coverage and the only live Postgres on this host is
production. Bring up a scratch Postgres, run `alembic upgrade head` against
it, and confirm `029_company_boilerplate` applies cleanly — SQLite ignores
`VARCHAR` length, so the suite cannot catch a column that is too narrow,
which is exactly how `JobFacets.city` escaped in Slice 1.

- [ ] **Step 3: Hand the user the production commands**

Do not run these. Present them for the user to run:

```bash
cd /home/ruby0322/offerfy/apps/backend && .venv/bin/alembic upgrade head
```

```bash
cd /home/ruby0322/offerfy/apps/backend && .venv/bin/python -m app.jobs.recompute_boilerplate
```

- [ ] **Step 4: Confirm the measured outcome on production**

Run after the user reports back:

```bash
curl -sS http://127.0.0.1:8001/v1/jobs/facets | python3 -c "import json,sys; d=json.load(sys.stdin); t=sum(r['count'] for r in d['functions']); print(sorted(((r['value'], r['count'], f\"{100*r['count']/t:.1f}%\") for r in d['functions']), key=lambda x:-x[1]))"
```

Expected, from the measurement this plan argues from: `other` ≈ 34.1%,
`ml` ≈ 15.9%, and Anduril's share of `ml` collapsed. Treat a materially
different distribution as a finding to report, not a number to explain
away.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/jobs/recompute_boilerplate.py
git commit -m "feat(jobs): add boilerplate recompute entrypoint"
```
