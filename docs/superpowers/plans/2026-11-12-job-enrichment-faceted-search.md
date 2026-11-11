# Job enrichment and faceted search — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive structured facets (location, seniority, function) for every job posting at ingest, and replace the ATS-vendor filter on `/jobs` with facets a job seeker would actually use.

**Architecture:** A new `app/services/jobs/enrich.py` reads closed vocabularies from `data/job_taxonomy.json` and derives three facets per posting with no provider call, writing them to a derived `job_facets` table gated on a `description_hash` so a posting is enriched once per content version rather than once per six-hour pass. `/v1/jobs` gains facet filters plus a result total, and the frontend search form swaps its `source` dropdown for location, seniority, function and date-posted.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, pytest (SQLite in-memory); Next.js App Router, next-intl, `node:test` via `tsx`.

**Spec:** [docs/superpowers/specs/2026-11-12-job-discovery-loop-design.md](../specs/2026-11-12-job-discovery-loop-design.md) — this plan implements **slice 1** only.

## Global Constraints

- **No provider call on the ingest path.** Enrichment is deterministic only. `offerfy-ingest.service` must not fail for reasons outside its control.
- **Unknown is kept, not dropped.** Follow `scope.py`: a false negative silently loses a good posting and nobody notices. Unparseable facets are `unknown`/`None`, never a reason to skip a row.
- **Seniority vocabulary, exactly:** `intern`, `new_grad`, `junior`, `mid`, `senior`, `staff_plus`, `manager`, `unknown`.
- **Function vocabulary, exactly:** `backend`, `frontend`, `fullstack`, `ml`, `data`, `infra`, `security`, `hardware`, `product`, `design`, `other`.
- **Alembic revision numbers are not fixed in this plan.** Concurrent branches collide on revisions. Run `cd apps/backend && .venv/bin/alembic heads` and number from there; set `down_revision` to the head you actually saw.
- **`ensure_schema()` builds production from the models with `create_all`** (`app/db.py:62`), and `conftest.py` does the same for tests. The migration and the model must describe the same object — named constraints in both, or a later `drop_constraint` finds nothing. No test runs alembic; the migration is checked by eye against the model.
- **`messages/{en,zh-TW,zh-CN}.json` must gain keys together.** A missing key throws at render time, it does not fall back. `lib/i18n-parity.test.ts` enforces this.
- **Never commit `apps/frontend/next-env.d.ts`.** `next dev` and `next build` flip it; it is already modified in the working tree. Check `git status` before every commit.
- **`npm run build:public` is the typecheck gate.** `npm run lint` is unusable as one (~121k problems on a clean tree); lint only the files you touched with `./node_modules/.bin/eslint path/to/file.tsx`.
- **Product copy must never say** CareerOS, Roleloop, Offerly, Offerloop or RenderResume.
- Backend tests: `cd apps/backend && .venv/bin/python -m pytest`. Frontend pure-module tests: `cd apps/frontend && ./node_modules/.bin/tsx --test lib/<file>.test.ts`.

## Review Focus

Input classes the spec implies that no task's happy-path tests would exercise. Each has a test assigned to the task that owns the code.

1. **`location` is NULL.** The column is nullable and real postings omit it. Facet derivation must return `None` facets rather than raising, and the row must still be enriched. → Task 2.
2. **CJK titles and locations.** The product ships zh-TW and zh-CN, and `spotlight.json` already carries `資深`, `後端`, `工程師`. Needle matching must casefold correctly on non-ASCII and `資深` must reach `senior`. → Task 1.
3. **A 200,000-character description.** `MAX_DESCRIPTION_CHARS` is 200k. Hashing and the function-keyword scan must stay bounded and must not be quadratic. → Task 3.
4. **A facet filter combined with a cursor.** `_after_cursor` plus a new filter must neither drop rows at a page boundary nor return the same row twice. → Task 5.
5. **11k existing rows with no `job_facets` row at all.** Filtering on a facet joins the derived table, so unenriched postings vanish from filtered results. The join must be explicit, a facet-free query must still return everything, and the backfill must be runnable before the UI exposes the filters. → Task 3 (backfill) and Task 5 (join semantics).

---

### Task 1: Taxonomy file, seniority and function derivation

**Files:**
- Create: `apps/backend/data/job_taxonomy.json`
- Create: `apps/backend/app/services/jobs/enrich.py`
- Test: `apps/backend/tests/test_jobs_enrich.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `load_taxonomy(path: Path | None = None) -> Taxonomy` and `default_taxonomy() -> Taxonomy` (`lru_cache(maxsize=1)`), mirroring `scope.py`'s `load_scope`/`default_scope`.
  - `SeniorityRule` — frozen dataclass `(seniority: str, needles: tuple[str, ...])`.
  - `FunctionRule` — frozen dataclass `(function: str, title_needles: tuple[str, ...], description_needles: tuple[str, ...])`.
  - `Taxonomy` — frozen dataclass with `seniority_rules: tuple[SeniorityRule, ...]`, `function_rules: tuple[FunctionRule, ...]`, `location_countries: dict[str, str]`, `location_regions: dict[str, str]`, `remote_needles: tuple[str, ...]`.
  - `seniority_of(title: str, taxonomy: Taxonomy | None = None) -> str`
  - `function_of(title: str, description_text: str, taxonomy: Taxonomy | None = None) -> str`

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from app.services.jobs.enrich import function_of, load_taxonomy, seniority_of

@pytest.mark.parametrize("title,expected", [
    ("Staff Software Engineer, Infrastructure", "staff_plus"),
    ("Principal Engineer", "staff_plus"),
    ("Member of Technical Staff", "senior"),
    ("Senior Backend Engineer", "senior"),
    ("SDE II", "mid"),
    ("Software Engineer II", "mid"),
    ("Software Engineer", "mid"),
    ("New Grad Software Engineer", "new_grad"),
    ("Software Engineer, University Graduate", "new_grad"),
    ("Software Engineering Intern", "intern"),
    ("Junior Developer", "junior"),
    ("Engineering Manager, Billing", "manager"),
    ("資深後端工程師", "senior"),
    ("Wizard of Light Bulb Moments", "unknown"),
])
def test_seniority_of(title, expected):
    assert seniority_of(title) == expected

@pytest.mark.parametrize("title,description,expected", [
    ("Backend Engineer", "", "backend"),
    ("Frontend Engineer", "", "frontend"),
    ("Full Stack Engineer", "", "fullstack"),
    ("Machine Learning Engineer", "", "ml"),
    ("Research Scientist", "We train large language models.", "ml"),
    ("Data Engineer", "", "data"),
    ("Site Reliability Engineer", "", "infra"),
    ("Security Engineer", "", "security"),
    ("Silicon Design Engineer", "", "hardware"),
    ("Product Manager", "", "product"),
    ("Product Designer", "", "design"),
    ("後端工程師", "", "backend"),
    ("Software Engineer", "", "other"),
])
def test_function_of(title, description, expected):
    assert function_of(title, description) == expected

def test_function_falls_back_to_description_keywords():
    assert function_of("Engineer, Platform", "You will own our Kubernetes fleet.") == "infra"

def test_taxonomy_file_covers_both_vocabularies_exactly():
    taxonomy = load_taxonomy()
    assert {rule.seniority for rule in taxonomy.seniority_rules} <= {
        "intern", "new_grad", "junior", "mid", "senior", "staff_plus", "manager",
    }
    assert {rule.function for rule in taxonomy.function_rules} <= {
        "backend", "frontend", "fullstack", "ml", "data", "infra",
        "security", "hardware", "product", "design",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.jobs.enrich'`

- [ ] **Step 3: Write `apps/backend/data/job_taxonomy.json`**

Four keys: `seniority_rules` (ordered list of `{seniority, any: [needles]}`), `function_rules` (ordered list of `{function, title_any: [...], description_any: [...]}`), `location_countries` and `location_regions` (needle → canonical value maps), `remote_needles`.

Rule order is the resolution order, so write the most specific first: `staff_plus` and `manager` before `senior`, `new_grad` and `intern` before `junior`. Include the catalogue's own idioms — "member of technical staff", "sde ii", "university graduate" — and the CJK needles already proven in `data/spotlight.json` (`資深`, `後端`, `前端`, `資料`, `工程師`).

- [ ] **Step 4: Implement the loader and the two derivations in `apps/backend/app/services/jobs/enrich.py`**

Follow `scope.py`'s shape exactly: frozen dataclasses, needles casefolded at load time, `lru_cache(maxsize=1)` on `default_taxonomy()`.

Both derivations match on `f" {title.casefold()} "` so a needle can anchor on word boundaries without a regex, the way `title_in_scope` does. First matching rule wins. `seniority_of` returns `"mid"` when the title names an engineering role with no level needle and `"unknown"` when nothing matches at all. `function_of` consults `description_any` only when no `title_any` rule matched, and returns `"other"` as its fallback.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/backend/data/job_taxonomy.json apps/backend/app/services/jobs/enrich.py apps/backend/tests/test_jobs_enrich.py
git commit -m "feat(jobs): derive seniority and function from a shared taxonomy"
```

---

### Task 2: Location parsing

**Files:**
- Modify: `apps/backend/app/services/jobs/enrich.py`
- Modify: `apps/backend/tests/test_jobs_enrich.py`

**Interfaces:**
- Consumes: `Taxonomy`, `default_taxonomy()` from Task 1.
- Produces:
  - `LocationFacets` — frozen dataclass `(country: str | None, region: str | None, city: str | None)`.
  - `location_facets(location: str | None, *, source: str, remote: bool | None, taxonomy: Taxonomy | None = None) -> LocationFacets`

- [ ] **Step 1: Write the failing tests**

Strings shaped like each ATS's real output. `region` is the coarse grouping the UI filters on (`apac`, `emea`, `north_america`); `country` is ISO 3166-1 alpha-2.

```python
from app.services.jobs.enrich import LocationFacets, location_facets

@pytest.mark.parametrize("raw,source,expected", [
    ("San Francisco, CA", "greenhouse", LocationFacets("US", "north_america", "San Francisco")),
    ("Taipei, Taiwan", "greenhouse", LocationFacets("TW", "apac", "Taipei")),
    ("London, UK", "lever", LocationFacets("GB", "emea", "London")),
    ("Remote - United States", "lever", LocationFacets("US", "north_america", None)),
    ("Hsinchu, Hsinchu City, Taiwan", "workday", LocationFacets("TW", "apac", "Hsinchu")),
    ("新竹市", "workday", LocationFacets("TW", "apac", "新竹市")),
    ("Multiple Locations", "smartrecruiters", LocationFacets(None, None, None)),
])
def test_location_facets(raw, source, expected):
    assert location_facets(raw, source=source, remote=None) == expected

def test_location_none_yields_empty_facets_without_raising():
    assert location_facets(None, source="ashby", remote=True) == LocationFacets(None, None, None)

def test_remote_flag_does_not_invent_a_country():
    assert location_facets("Remote", source="ashby", remote=True).country is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -k location -v`
Expected: FAIL — `ImportError: cannot import name 'location_facets'`

- [ ] **Step 3: Implement `location_facets` in `apps/backend/app/services/jobs/enrich.py`**

Split the raw string on `,` and `-`, strip, then match each segment against `location_countries` longest-needle-first so "United States" wins over "State". `region` is derived from the resolved `country`, never parsed independently. `city` is the first segment that matched no country and no region needle, and is `None` when the string is a placeholder (`remote_needles`, "multiple locations") or when nothing survives.

A location that resolves to no country returns all-`None` rather than guessing — the Global Constraints' unknown-is-kept rule applies to the facet, not to the row.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -v`
Expected: PASS (all of Task 1's tests still passing)

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/enrich.py apps/backend/tests/test_jobs_enrich.py
git commit -m "feat(jobs): parse ATS location strings into country, region and city"
```

---

### Task 3: `job_facets` table, hash-gated writer, backfill

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/<next>_job_facets.py`
- Modify: `apps/backend/app/services/jobs/enrich.py`
- Modify: `apps/backend/tests/test_jobs_enrich.py`

**Interfaces:**
- Consumes: `seniority_of`, `function_of`, `location_facets` from Tasks 1–2.
- Produces:
  - `JobFacets` model — `job_id` (String(36), PK, FK `jobs.id` `ondelete="CASCADE"`), `country` (String(2), nullable), `region` (String(32), nullable), `city` (String(255), nullable), `seniority` (String(16), not null), `function` (String(16), not null), `description_hash` (String(64), not null), `enriched_at` (DateTime(timezone=True), not null).
  - `description_hash(text: str) -> str` — sha256 hex.
  - `enrich_jobs(db: Session, *, job_ids: list[str] | None = None, force: bool = False, now: datetime | None = None) -> int` — returns rows written. `job_ids=None` means every active posting.

- [ ] **Step 1: Write the failing tests**

```python
from app.models import Job, JobFacets
from app.services.jobs.enrich import description_hash, enrich_jobs

def test_enrich_writes_facets_for_a_job(db_session):
    job = _add_job(db_session, source_id="anthropic:1",
                   title="Staff Software Engineer, Infrastructure",
                   location="San Francisco, CA", description_text="Own the fleet.")
    assert enrich_jobs(db_session, job_ids=[job.id]) == 1
    row = db_session.get(JobFacets, job.id)
    assert (row.seniority, row.function, row.country, row.region) == (
        "staff_plus", "infra", "US", "north_america")

def test_enrich_is_a_noop_when_the_description_has_not_changed(db_session):
    job = _add_job(db_session, source_id="anthropic:2", description_text="Same text.")
    enrich_jobs(db_session, job_ids=[job.id])
    assert enrich_jobs(db_session, job_ids=[job.id]) == 0

def test_enrich_rewrites_when_the_description_changes(db_session):
    job = _add_job(db_session, source_id="anthropic:3", title="Engineer, Platform",
                   description_text="You will write Django.")
    enrich_jobs(db_session, job_ids=[job.id])
    job.description_text = "You will own our Kubernetes fleet."
    db_session.commit()
    assert enrich_jobs(db_session, job_ids=[job.id]) == 1
    assert db_session.get(JobFacets, job.id).function == "infra"

def test_force_rewrites_an_unchanged_row(db_session):
    job = _add_job(db_session, source_id="anthropic:4")
    enrich_jobs(db_session, job_ids=[job.id])
    assert enrich_jobs(db_session, job_ids=[job.id], force=True) == 1

def test_enrich_backfills_every_active_job_when_no_ids_are_given(db_session):
    _add_job(db_session, source_id="a:1")
    _add_job(db_session, source_id="a:2")
    _add_job(db_session, source_id="a:3", is_active=False)
    assert enrich_jobs(db_session) == 2

def test_enrich_handles_a_maximum_length_description(db_session):
    from app.services.jobs.types import MAX_DESCRIPTION_CHARS
    job = _add_job(db_session, source_id="a:4", description_text="x" * MAX_DESCRIPTION_CHARS)
    assert enrich_jobs(db_session, job_ids=[job.id]) == 1
    assert len(db_session.get(JobFacets, job.id).description_hash) == 64

def test_deleting_a_job_cascades_to_its_facets(db_session):
    job = _add_job(db_session, source_id="a:5")
    enrich_jobs(db_session, job_ids=[job.id])
    db_session.delete(job)
    db_session.commit()
    assert db_session.get(JobFacets, job.id) is None
```

Copy the `_add_job` helper from `tests/test_jobs_api.py:6` into this module, or import it — do not re-derive it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -v`
Expected: FAIL — `ImportError: cannot import name 'JobFacets' from 'app.models'`

- [ ] **Step 3: Add the `JobFacets` model to `apps/backend/app/models.py`**

Place it immediately after `Job`. Docstring states it is derived state that can be dropped and rebuilt, which is why it is a separate table rather than six more columns on `jobs` — the same reasoning `TalentProfile` and `ResumeContent` already carry. `seniority` and `function` are not null because `unknown`/`other` are real values in their vocabularies.

`function` is a non-reserved keyword in Postgres and SQLAlchemy quotes identifiers for you, so the column keeps that name rather than being renamed to match the query parameter — do not rename it.

- [ ] **Step 4: Write the alembic revision**

Run `cd apps/backend && .venv/bin/alembic heads` first and set `down_revision` to what it prints. Plain `create_table` with no dialect guard, matching `026_resume_contents.py`. Index `country`, `seniority` and `function` — Task 5 filters on all three. No CHECK constraint on the vocabularies: a vocabulary that grows would need a migration to widen, and `enrich.py` is the single writer.

- [ ] **Step 5: Implement `description_hash` and `enrich_jobs` in `apps/backend/app/services/jobs/enrich.py`**

`enrich_jobs` selects the target jobs, left-joins existing facets, and skips any row whose stored `description_hash` already equals the computed one unless `force`. Compute the hash from `description_text` alone. Flush but do not commit — the caller owns the transaction, as `upsert_jobs` does.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -v`
Expected: PASS

- [ ] **Step 7: Verify the migration against a throwaway Postgres**

Migrations have no test coverage in this repository and the only live Postgres is production. Create a scratch database, run `alembic upgrade head` then `alembic downgrade -1`, and confirm the table shape matches `JobFacets`. Do not point alembic at the production URL.

- [ ] **Step 8: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions apps/backend/app/services/jobs/enrich.py apps/backend/tests/test_jobs_enrich.py
git commit -m "feat(jobs): store derived facets, gated on the description hash"
```

---

### Task 4: Wire enrichment into ingest

**Files:**
- Modify: `apps/backend/app/services/jobs/ingest.py`
- Modify: `apps/backend/app/services/jobs/ingest_workday.py`
- Modify: `apps/backend/tests/test_jobs_ingest.py`

**Interfaces:**
- Consumes: `enrich_jobs` from Task 3.
- Produces: no new names. `ingest_board` and the Workday pass write facets for every posting they upsert.

- [ ] **Step 1: Write the failing tests**

```python
def test_ingest_board_writes_facets(db_session):
    # Build the board and transport the way the existing ingest_board tests in
    # this module already do, with one posting titled "Senior Backend Engineer"
    # located "Taipei, Taiwan".
    result = ingest_board(db_session, client, board)
    assert result.status == "ok"
    row = db_session.query(JobFacets).one()
    assert (row.seniority, row.function, row.country) == ("senior", "backend", "TW")

def test_ingest_board_does_not_fail_when_a_title_is_unparseable(db_session):
    # One posting whose title matches no needle. The pass still reports ok and
    # the facets row exists with seniority "unknown".
    result = ingest_board(db_session, client, board)
    assert result.status == "ok"
    assert db_session.query(JobFacets).one().seniority == "unknown"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_ingest.py -k facets -v`
Expected: FAIL — no `job_facets` rows written

- [ ] **Step 3: Call `enrich_jobs` from both ingest paths**

In `ingest_board`, after `upsert_jobs` and before `record_run`, enrich exactly the postings this board upserted — pass their ids rather than letting it scan the catalogue, so a 119-board pass does not walk 11k rows 119 times. The Workday pass does the same after its own upsert in `ingest_workday.py`.

Enrichment must not be able to fail the pass: wrap the call so an exception is recorded in the run's `error_snippet` and the postings still land. A posting with no facets row is invisible to a filtered query but still visible on `/jobs` unfiltered, which is the correct degradation.

- [ ] **Step 4: Run the full backend suite**

Run: `cd apps/backend && .venv/bin/python -m pytest -q`
Expected: PASS. Two unrelated failures are expected in a worktree, where `node_modules`, the `typst` CLI and the package cache live only in the main checkout.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/ingest.py apps/backend/app/services/jobs/ingest_workday.py apps/backend/tests/test_jobs_ingest.py
git commit -m "feat(jobs): enrich postings as they are ingested"
```

---

### Task 5: Faceted `/v1/jobs`, result total, facet options

**Files:**
- Modify: `apps/backend/app/schemas_jobs.py`
- Modify: `apps/backend/app/routers/jobs.py`
- Modify: `apps/backend/tests/test_jobs_api.py`

**Interfaces:**
- Consumes: `JobFacets` from Task 3; `default_taxonomy()` from Task 1 for validation.
- Produces:
  - `GET /v1/jobs` gains `country: str | None`, `region: str | None`, `seniority: str | None`, `function: str | None`, `posted_within_days: int | None` (`ge=1, le=90`).
  - `JobList` gains `total: int`.
  - `GET /v1/jobs/facets` → `JobFacetOptions` with `countries`, `regions`, `seniorities`, `functions`, each a `list[JobFacetOption]` of `{value: str, count: int}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_filter_by_seniority(client, db_session):
    _add_job(db_session, source_id="a:1", title="Staff Engineer")
    _add_job(db_session, source_id="a:2", title="Junior Developer")
    enrich_jobs(db_session); db_session.commit()
    body = client.get("/v1/jobs?seniority=staff_plus").json()
    assert [item["title"] for item in body["items"]] == ["Staff Engineer"]

def test_filter_by_country_and_function(client, db_session):
    # One Taipei backend posting and one San Francisco ML posting.
    body = client.get("/v1/jobs?country=TW&function=backend").json()
    assert body["total"] == 1

def test_invalid_facet_value_is_422(client):
    assert client.get("/v1/jobs?seniority=wizard").status_code == 422
    assert client.get("/v1/jobs?function=vibes").status_code == 422

def test_total_counts_the_filtered_set_not_the_page(client, db_session):
    # 25 matching active postings, default limit 20.
    body = client.get("/v1/jobs?seniority=mid").json()
    assert len(body["items"]) == 20
    assert body["total"] == 25

def test_unfiltered_list_still_returns_jobs_with_no_facets_row(client, db_session):
    _add_job(db_session, source_id="a:9", title="Engineer")  # never enriched
    body = client.get("/v1/jobs").json()
    assert body["total"] == 1

def test_facet_filter_excludes_jobs_with_no_facets_row(client, db_session):
    _add_job(db_session, source_id="a:9", title="Engineer")  # never enriched
    assert client.get("/v1/jobs?seniority=mid").json()["total"] == 0

def test_cursor_paging_under_a_facet_filter_yields_each_row_once(client, db_session):
    # 25 matching postings; walk every page via next_cursor.
    seen, cursor = [], None
    while True:
        url = "/v1/jobs?seniority=mid&limit=10" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 25

def test_posted_within_days_filters_on_posted_at(client, db_session):
    # One posting 3 days old, one 60 days old, both active and fresh.
    assert client.get("/v1/jobs?posted_within_days=7").json()["total"] == 1

def test_facets_endpoint_counts_only_active_fresh_postings(client, db_session):
    # One active fresh TW posting, one inactive TW posting, one TW posting
    # last seen beyond JOB_FRESHNESS_DAYS.
    body = client.get("/v1/jobs/facets").json()
    assert {"value": "TW", "count": 1} in body["countries"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_api.py -v`
Expected: FAIL — unknown query parameters are ignored, so the filter assertions fail; `total` is absent from the response

- [ ] **Step 3: Add `total`, `JobFacetOption` and `JobFacetOptions` to `apps/backend/app/schemas_jobs.py`**

- [ ] **Step 4: Add the facet filters and the total to `_filtered_query` and `list_jobs` in `apps/backend/app/routers/jobs.py`**

Validate `seniority` and `function` against `default_taxonomy()` and raise `HTTPException(422, "Invalid seniority")` on a miss, matching the existing `source` branch. Join `JobFacets` **only when a facet filter is present**, and make it an inner join when it is: an unenriched posting genuinely does not match `seniority=mid`, and a left join would either admit it or require a `NULL` guard at every filter.

`total` is a `func.count()` over the same filtered query before ordering and cursoring. Keep the existing `_posted_at_order()` and `_after_cursor()` untouched — the cursor is over `(posted_at, id)` and stays correct under any additional `WHERE`.

- [ ] **Step 5: Add the `/v1/jobs/facets` route**

Place it **above** `GET /v1/jobs/{job_id}`, or `facets` is captured as a job id — the same ordering `/v1/jobs/sitemap` and `/v1/jobs/featured` already depend on. Count over `_sitemap_conditions()` so the options describe the postings a visitor can actually reach, grouped per facet column, `NULL` values omitted.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_api.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/schemas_jobs.py apps/backend/app/routers/jobs.py apps/backend/tests/test_jobs_api.py
git commit -m "feat(jobs): filter the catalog by location, seniority, function and age"
```

---

### Task 6: Faceted search form and result count

**Files:**
- Modify: `apps/frontend/lib/jobs.ts`
- Create: `apps/frontend/lib/jobs.test.ts`
- Modify: `apps/frontend/components/jobs/JobSearchForm.tsx`
- Modify: `apps/frontend/components/jobs/JobIndex.tsx`
- Modify: `apps/frontend/app/[locale]/jobs/page.tsx`
- Modify: `apps/frontend/messages/en.json`, `messages/zh-TW.json`, `messages/zh-CN.json`
- Modify: `apps/frontend/app/[locale]/globals.css`

**Interfaces:**
- Consumes: Task 5's query parameters and `JobList.total`, `GET /v1/jobs/facets`.
- Produces:
  - `JobQuery` gains `country`, `region`, `seniority`, `function`, `postedWithinDays` (all `string | undefined`); `source` is removed from the type.
  - `JobFacetOptions` type and `fetchJobFacets(): Promise<JobFacetOptions | null>`.
  - `JobList` type gains `total: number`.

- [ ] **Step 1: Write the failing tests**

```ts
import assert from "node:assert/strict";
import test from "node:test";
import { hasJobFilters, jobListParams, jobQueryString } from "./jobs";

test("jobListParams carries every facet", () => {
  const params = jobListParams({ q: "backend", country: "TW", seniority: "senior",
    function: "backend", postedWithinDays: "7" });
  assert.equal(params.get("country"), "TW");
  assert.equal(params.get("seniority"), "senior");
  assert.equal(params.get("function"), "backend");
  assert.equal(params.get("posted_within_days"), "7");
});

test("jobListParams omits blank facets", () => {
  const params = jobListParams({ country: "", seniority: undefined });
  assert.equal(params.has("country"), false);
  assert.equal(params.has("seniority"), false);
});

test("hasJobFilters sees a facet-only query", () => {
  assert.equal(hasJobFilters({ seniority: "senior" }), true);
  assert.equal(hasJobFilters({}), false);
});

test("jobQueryString drops limit but keeps facets", () => {
  assert.equal(jobQueryString({ country: "TW" }), "?country=TW");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/frontend && ./node_modules/.bin/tsx --test lib/jobs.test.ts`
Expected: FAIL — `country` is not a property of `JobQuery`

- [ ] **Step 3: Extend `JobQuery`, `jobListParams`, `hasJobFilters` and add `fetchJobFacets` in `apps/frontend/lib/jobs.ts`**

Remove `source` from `JobQuery` and from `jobListParams`; the backend keeps the parameter for the admin views. Leave `JobSource` and `sourceLabel` in place — `JobResults` and `JobView` still render the attribution, and historical `taiwanjobs` rows depend on it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/frontend && ./node_modules/.bin/tsx --test lib/jobs.test.ts`
Expected: PASS

- [ ] **Step 5: Add the message keys to all three locale files**

New keys under `jobs`: `countryLabel`, `countryAll`, `regionLabel`, `regionAll`, `seniorityLabel`, `seniorityAll`, `functionLabel`, `functionAll`, `postedLabel`, `postedAll`, `posted7`, `posted30`, `resultCount`, plus one key per seniority and function value for the option labels. Remove `sourceLabel` and `sourceAll`.

`resultCount` takes a `{count}` placeholder. Add to `en.json`, `zh-TW.json` and `zh-CN.json` in the same commit — a missing key throws at render time.

- [ ] **Step 6: Verify locale parity**

Run: `cd apps/frontend && ./node_modules/.bin/tsx --test lib/i18n-parity.test.ts`
Expected: PASS

- [ ] **Step 7: Rebuild `JobSearchForm.tsx` and show the count in `JobIndex.tsx`**

The form stays a `method="get"` form with no client state — it is server-driven today and nothing here needs that to change. Replace the `source` select with selects for seniority, function, country and date-posted, populated from the `fetchJobFacets()` result passed down from `jobs/page.tsx` (which already awaits `fetchJobList` and `fetchFeaturedJobs` in parallel — add this to the same `Promise.all`). A facet whose options list comes back empty is not rendered.

`JobIndex` renders `t("resultCount", { count: list.total })` above the results. Read the new facet parameters in `jobs/page.tsx`'s `searchParams` with the existing `first()` helper.

Any `<select>` or `<input>` must carry ≥16px type on a phone (`text-base md:text-sm`), or iOS zooms on focus and never zooms back. Extend `.jobs-filters` in `globals.css` to wrap onto multiple rows at narrow widths rather than overflowing.

- [ ] **Step 8: Typecheck and lint the touched files**

Run: `cd apps/frontend && npm run build:public`
Expected: builds with no type errors
Run: `cd apps/frontend && ./node_modules/.bin/eslint components/jobs/JobSearchForm.tsx components/jobs/JobIndex.tsx lib/jobs.ts`
Expected: no errors

- [ ] **Step 9: Verify in the running app**

Start the frontend against the real backend and load `/jobs`. Confirm the facet selects populate, that submitting one narrows the list and updates the count, and that the ATS-source dropdown is gone. Check `git status` and confirm `next-env.d.ts` is not staged.

- [ ] **Step 10: Commit**

```bash
git add apps/frontend/lib/jobs.ts apps/frontend/lib/jobs.test.ts apps/frontend/components/jobs apps/frontend/app/\[locale\]/jobs/page.tsx apps/frontend/messages apps/frontend/app/\[locale\]/globals.css
git commit -m "feat(jobs): replace the ATS filter with real facets and a result count"
```

---

### Task 7: Facet coverage survey

**Files:**
- Create: `apps/backend/app/jobs/survey_facets.py`
- Modify: `apps/backend/tests/test_jobs_enrich.py`

**Interfaces:**
- Consumes: `enrich_jobs` and `JobFacets` from Task 3.
- Produces: `python -m app.jobs.survey_facets [--limit N]`, printing per-source counts and the share of rows landing on `unknown` seniority, `other` function and `NULL` country.

**Why this exists:** the taxonomy needle lists have to be tuned against the live catalogue, as `data/job_scope.json` was, and a session cannot read the production database. This is the artifact the maintainer runs to get those numbers back. Precedent: `app/jobs/survey_templates.py`.

- [ ] **Step 1: Write the failing test**

```python
def test_survey_reports_unknown_shares(db_session, capsys):
    _add_job(db_session, source_id="a:1", title="Senior Backend Engineer", location="Taipei, Taiwan")
    _add_job(db_session, source_id="a:2", title="Wizard of Light Bulb Moments", location=None)
    enrich_jobs(db_session); db_session.commit()
    from app.jobs.survey_facets import survey
    report = survey(db_session)
    assert report["total"] == 2
    assert report["unknown_seniority"] == 1
    assert report["null_country"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -k survey -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.jobs.survey_facets'`

- [ ] **Step 3: Implement `survey(db: Session) -> dict` and a `main(argv)` CLI in `apps/backend/app/jobs/survey_facets.py`**

Follow `app/jobs/refresh_profiles.py`'s `main` shape: `argparse`, build the engine and session, print, return an exit code. Read-only — it must never write. Group the counts by `jobs.source` as well as reporting the totals, so a single board's naming convention can be identified as the cause.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_jobs_enrich.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/jobs/survey_facets.py apps/backend/tests/test_jobs_enrich.py
git commit -m "feat(jobs): survey facet coverage across the live catalog"
```

---

## Deployment order

The filters must not go live before the backfill, or `/jobs` shows facet dropdowns that match nothing for the 11k existing postings.

1. Merge and deploy the backend (Tasks 1–5, 7). `ensure_schema()` creates `job_facets` on API start; the alembic revision is for the record and for a database built by migration.
2. Run the backfill once: `cd apps/backend && .venv/bin/python -c "from app.db import get_engine, get_session_factory; from app.services.jobs.enrich import enrich_jobs; get_engine(); s = get_session_factory()(); print(enrich_jobs(s)); s.commit()"`
3. Run `python -m app.jobs.survey_facets` and tune `data/job_taxonomy.json` if the `unknown` share is high enough to make a facet useless. Re-run the backfill with `force=True` after any taxonomy change.
4. Deploy the frontend (Task 6) per AGENTS.md §3: `cp -al .next .next.bak-$(date +%Y%m%d-%H%M%S)`, `npm run build:public`, verify the `/api` rewrite is `:8001`, restart, smoke.
