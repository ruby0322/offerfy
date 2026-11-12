# Resume Facts and Preferences Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every user a match profile — facts extracted from their resume,
plus preferences they own and edit — and a deterministic count of postings that
match it.

**Architecture:** Lift the resume extraction out of `profile.py` into
`app/services/resume_facts.py` so one build serves two consumers: `resume_facts`
(the user's own data, no special consent) and `talent_profiles` (a
consent-gated projection whose stored bytes must not move, because
`disclosure_events.profile_content_hash` cites them). Add user-owned
`job_preferences`, seeded once from the facts and thereafter written only by the
user. `app/jobs/match_sweep.py` holds the deterministic hard-filter prefilter;
the count is computed on demand in SQL.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, pytest (SQLite
in-memory via `tests/conftest.py`), Next.js App Router, next-intl, `node:test`
via `tsx`.

**Spec:** `docs/superpowers/specs/2026-11-12-job-discovery-loop-design.md` —
this plan implements **Slice 2** of its Decomposition. Slice 1 shipped
(`job_facets`, `enrich.py`, faceted `/jobs`).

## Decisions taken before this plan, which it does not relitigate

Two questions the spec left reachable but not closed were settled by the user
on 2026-11-12:

1. **Extraction runs without the `talent_introduction` grant.** Today
   `build_profile` returns `no_consent` and the resume is never sent to the
   provider. After this slice, extraction runs for any user with a published
   resume, because `resume_facts` is the user's own data serving the user's own
   benefit; `talent_profiles` is still written only under consent. **This is a
   privacy-relevant behaviour change and Task 9 carries the policy copy** — it
   is a deliverable of this slice, not an afterthought.
2. **The count is labelled "N matches", not "N new matches".** The spec's
   prefilter excludes postings with an `applications` row and postings in the
   user's last four digests; both tables arrive in Slices 3 and 4. Rather than
   invent a seventh table for per-user read-state, Slice 2 counts what the
   filters it has can express, and the word "new" waits for Slice 3.

## Global Constraints

- **`talent_profiles` must not change.** No migration touches it, and for a
  fixed input the bytes written to `profile_json` and the value of every
  derived column must be identical before and after the extraction move.
  `disclosure_events.profile_content_hash` is evidence; if those bytes move,
  the ledger stops citing what was disclosed. Task 1 exists to prove this.
- **Contact scrubbing applies to both consumers.** `scrub_contacts` runs before
  anything is written, to `resume_facts` as well as `talent_profiles`. The
  matcher has no use for contact details and a derived table that cannot hold
  them is cheaper to reason about than one that may.
- **One taxonomy, both sides.** Seniority and function vocabularies come from
  `data/job_taxonomy.json` via `app/services/jobs/enrich.py`, never from a
  second list. Seeding preferences reuses `enrich.function_of`,
  `enrich.seniority_of` and `enrich.location_facets` against the person's own
  role titles and locations. If the job side says `staff` and the resume side
  says `Staff Engineer` as free text, the match is noise.
- **Pipe-delimited columns use the `skills_text` idiom:** `"|a|b|"`, so
  `LIKE '%|ml|%'` cannot match `html`. This applies to `target_functions` and
  `target_locations`.
- Derived JSON columns are `Text` holding canonical JSON, never `JSON`/`JSONB`
  — the same reasoning as `profile_json` and `sentence_hashes`: stable bytes
  across a round trip.
- Every provider call site must appear in `test_billable_call_sites_are_wrapped`
  (`tests/test_metering.py:73`). Moving a call site does not exempt it;
  `preflight` enforces and `system_guard` records without ever returning 429.
- Alembic revisions are numbered from `alembic heads` **at implementation
  time**. This plan writes `030_resume_facts` and `031_job_preferences` with
  `down_revision` chaining from `029_company_boilerplate`, which is head as of
  2026-11-12 — re-check, because concurrent branches collide on revisions.
- No test runs Alembic; `conftest.py` builds the schema with `create_all`. Each
  revision is checked by eye against the model **and** applied once against a
  throwaway Postgres (never the configured database — the only live Postgres on
  this host is production).
- The suite has 2 pre-existing failures in `tests/test_grants.py` and one
  pre-existing `tsc` error in `lib/admin-ingest.test.ts`. Neither is this
  plan's. Baseline at branch point: **1152 passing**.
- i18n keys must be added to all three of `messages/{en,zh-TW,zh-CN}.json`;
  `lib/i18n-parity.test.ts` enforces it.

## Review Focus

Input classes the design implies that no task's happy path exercises, most
likely to bite first. Each has its test assigned to the task that owns the code.

1. **Consent withdrawal must delete `talent_profiles` and leave `resume_facts`
   standing.** `delete_profile_for_user()` is the withdrawal path; if it starts
   cascading into facts, withdrawing from employer search silently destroys the
   user's own matching. — Task 3
2. **A user with no published resume** must get a working, empty preferences
   editor and a zero count, not a 500 — seeding reads facts that do not exist.
   — Task 6
3. **Empty preferences must not match the entire catalogue.** A user who saves
   nothing has `target_functions = ""`; a naive `LIKE '%||%'` matches
   everything. Decide and pin whether empty means "no constraint on this axis"
   or "no matches", per axis. — Task 8
4. **Seniority band arithmetic at the ends of the ladder.** "within one band"
   has no band below `intern` or above `manager`, and `unknown` is not on the
   ladder at all — it must not silently sort as position 0. — Task 8
5. **Extraction now runs for non-consenting users**, so a contact detail that
   slips past `scrub_contacts` lands in a table that a user who declined
   employer processing never agreed to populate. Pin that scrubbing runs on the
   facts path specifically, not only on the profile path. — Task 3

---

### Task 1: Extract `resume_facts.py`, leaving `talent_profiles` byte-identical

Pure code movement. No behaviour change, no new table, no consent change. This
task exists so that the risky move is provable on its own before anything is
built on it.

**Files:**
- Create: `apps/backend/app/services/resume_facts.py`
- Modify: `apps/backend/app/services/profile.py`
- Test: `apps/backend/tests/test_resume_facts.py`

**Interfaces:**
- Consumes: nothing.
- Produces, all moved verbatim out of `profile.py`:
  - `PROMPT`, `_schema()`, `PROFILE_TIMEOUT`, `PROFILE_FALLBACK_MODEL`
  - `scrub_contacts(payload: TalentProfilePayload) -> TalentProfilePayload`
  - `clamp_payload(payload: TalentProfilePayload) -> TalentProfilePayload`
  - `derived_columns(payload: TalentProfilePayload) -> dict[str, Any]`
    (today's private `_derived`, renamed because two modules now call it)
  - `bundle(db, user) -> tuple[Resume, str, str, list[str]] | None` (today's
    `_bundle`), `input_hash(revision_id, source, facts) -> str` (today's
    `_input_hash`), `request_body(model, source, facts) -> dict[str, Any]`
    (today's `_request_body`), `usage_of`, `is_empty`
- `profile.py` imports these and keeps `build_profile`, `profile_for_user`,
  `delete_profile_for_user`, `payload_of`/`detail_of`/`summary_of`,
  `readable_profiles*`, `record_build_spend`, `is_stale`, `source_resume`.

- [ ] **Step 1: Write the failing test**

The byte-identity test is the point of this task. Capture the bytes through the
real write path, not by calling the helpers directly.

```python
def test_talent_profile_bytes_are_unchanged_by_the_extraction_move(db_session):
    """The disclosure ledger cites profile_json's bytes; they must not move."""
    payload = _fixture_payload()          # a fully-populated TalentProfilePayload
    from app.services import resume_facts
    assert resume_facts.clamp_payload(payload) == profile.clamp_payload(payload)
    assert resume_facts.scrub_contacts(payload) == profile.scrub_contacts(payload)
    assert resume_facts.derived_columns(payload) == EXPECTED_DERIVED

def test_derived_columns_pipe_delimits_skills():
    d = resume_facts.derived_columns(_payload(skills=["Python", "Go"]))
    assert d["skills_text"] == "|python|go|"
```

`_fixture_payload()` is a local helper you write in this module: a
fully-populated `TalentProfilePayload` with every field set, including
`roles[]` and `education[]`, since `derived_columns` reads all of them.

`EXPECTED_DERIVED` is a literal dict written from the CURRENT behaviour —
generate it by running today's `profile._derived` on the fixture **before**
moving anything, and paste the result. A test that calls both implementations
proves they agree; a test against a frozen literal proves they did not both
drift.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_resume_facts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.resume_facts'`

- [ ] **Step 3: Move the code**

Move, do not copy. A duplicated prompt or schema is the defect this task is
written to avoid — two copies will drift and the employer and match sides will
silently disagree. `profile.py` imports from `resume_facts`, never the reverse.

Keep `profile.py`'s module docstring accurate about what now lives where, and
give `resume_facts.py` a docstring stating why the extraction exists: one
extraction, two consumers, two purposes.

- [ ] **Step 4: Run the full suite**

Run: `cd apps/backend && .venv/bin/pytest -q`
Expected: PASS, only the 2 pre-existing `tests/test_grants.py` failures. Every
existing `tests/test_talent_*.py` test must pass **unedited** — this task
changes no behaviour, so an existing test that needs editing means the move was
not faithful. Report it rather than editing the assertion.

This holds for Task 1 specifically because the `httpx` call stays in
`build_profile`, which stays in `profile.py`. `tests/test_talent_profile.py:148`'s
`_stub` monkeypatches `app.services.profile.httpx.AsyncClient`, so it keeps
working. Task 3 is where that stops being true.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/resume_facts.py apps/backend/app/services/profile.py apps/backend/tests/test_resume_facts.py
git commit -m "refactor(profile): extract resume extraction into resume_facts"
```

---

### Task 2: `resume_facts` table and migration

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/030_resume_facts.py`
- Test: `apps/backend/tests/test_resume_facts.py` (append)

**Interfaces:**
- Produces: `ResumeFacts` — `id` `String(36)` PK, `user_id` FK→`users`
  `ON DELETE CASCADE` with `UniqueConstraint("user_id")`, `source_resume_id`
  FK→`resumes` `ON DELETE CASCADE`, `source_revision_id` `String(36)`,
  `input_hash` `String(64)`, `schema_version` `Integer`, `facts_json` `Text`,
  `skills_text` `Text`, `search_text` `Text`, `years_experience` nullable
  `Integer`, `built_at` `DateTime(timezone=True)`, `model` `String(64)`.

Mirror `TalentProfile`'s column types exactly — read it in `app/models.py`
first. Where this plan and that model disagree on a type or length, the model
wins and the discrepancy goes in your report.

- [ ] **Step 1: Write the failing test**

```python
def test_resume_facts_round_trips_and_is_unique_per_user(db_session):
    db_session.add(_facts(user_id=u.id, skills_text="|python|"))
    db_session.flush()
    db_session.expire_all()                      # force a real read, not the identity map
    row = db_session.query(ResumeFacts).filter_by(user_id=u.id).one()
    assert json.loads(row.facts_json)["skills"] == ["Python"]
    db_session.add(_facts(user_id=u.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest tests/test_resume_facts.py -k round_trips -v`
Expected: FAIL — `ImportError: cannot import name 'ResumeFacts'`

- [ ] **Step 3: Add the model and the revision**

`revision = "030_resume_facts"`, `down_revision = "029_company_boilerplate"`
(confirm against `alembic heads` first). Plain `create_table`, matching
`028_job_facets.py`'s style and docstring register.

The model docstring says why this is a separate table from `talent_profiles`
rather than a flag on it: different consent lifetime, different deletion rule,
and `talent_profiles`' bytes are cited by an append-only ledger.

- [ ] **Step 4: Verify the schema and the head**

Run: `cd apps/backend && .venv/bin/pytest tests/test_resume_facts.py -v && .venv/bin/alembic heads`
Expected: PASS, exactly one head.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/030_resume_facts.py apps/backend/tests/test_resume_facts.py
git commit -m "feat(resume): add resume_facts table"
```

---

### Task 3: One build, two consumers

The behaviour change. Extraction stops being consent-gated; writing
`talent_profiles` stays consent-gated.

**Files:**
- Modify: `apps/backend/app/services/resume_facts.py`
- Modify: `apps/backend/app/services/profile.py`
- Test: `apps/backend/tests/test_resume_facts.py` (append) and
  `apps/backend/tests/test_talent_profile.py` (append — the existing profile
  build tests live there; the other two profile modules are
  `test_talent_api.py` and `test_talent_disclosure.py`)

**Interfaces:**
- Consumes: Task 1's helpers, Task 2's `ResumeFacts`.
- Produces:
  - `async def build_facts(db, user) -> FactsBuildResult` — runs the extraction
    with **no consent check**, scrubs, clamps, writes `ResumeFacts`. Never
    raises; returns an error string on the result as `ProfileBuildResult` does.
  - `facts_for_user(db, user) -> ResumeFacts | None`
  - `facts_are_stale(db, user, facts) -> bool` — same `input_hash` comparison
    as `profile.is_stale`.
  - `build_profile(db, user)` keeps its signature and its `no_consent` return,
    but is now a projection: it calls the shared extraction once and writes
    both rows under one `input_hash` when consent is present.

- [ ] **Step 1: Write the failing tests**

Reuse `tests/test_talent_profile.py`'s existing helpers rather than inventing
any — it already has `_signed_in`, `_resume`, `_grant(client)`,
`_stub(monkeypatch, StubProfileLlm(_said(_profile())))` and
`_ready(client, db_session, monkeypatch, sub=...)` ("a consenting user with a
published resume and a stubbed provider"). For the no-consent cases you need
`_ready`'s setup *without* its `_grant` call, so write one sibling helper,
`_unconsenting(client, db_session, monkeypatch, sub=...)`, next to it.

```python
# Review Focus 5
async def test_facts_are_built_without_talent_introduction_consent(
    client, db_session, monkeypatch, openai_enabled
):
    user, _ = _unconsenting(client, db_session, monkeypatch, sub="sub-nocon")
    result = await resume_facts.build_facts(db_session, user)
    assert result.error is None
    assert facts_for_user(db_session, user) is not None
    assert db_session.query(TalentProfile).filter_by(user_id=user.id).count() == 0


# Review Focus 5
async def test_contacts_are_scrubbed_on_the_facts_path(
    client, db_session, monkeypatch, openai_enabled
):
    user, _ = _unconsenting(client, db_session, monkeypatch, sub="sub-scrub")
    _stub(monkeypatch, StubProfileLlm(_said(_profile(
        summary="reach me at a@b.com or +1 555 123 4567"))))
    await resume_facts.build_facts(db_session, user)
    blob = facts_for_user(db_session, user).facts_json
    assert "a@b.com" not in blob and "555" not in blob


async def test_one_build_writes_both_rows_under_one_input_hash(
    client, db_session, monkeypatch, openai_enabled
):
    user, _ = _ready(client, db_session, monkeypatch, sub="sub-both")
    stub = _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    await profile.build_profile(db_session, user)
    assert facts_for_user(db_session, user).input_hash == \
           db_session.query(TalentProfile).filter_by(user_id=user.id).one().input_hash
    assert len(stub.calls) == 1     # one extraction serving two consumers, not two


def test_facts_are_stale_when_the_published_revision_moves(db_session, user):
    facts = facts_for_user(db_session, user)
    assert resume_facts.facts_are_stale(db_session, user, facts) is False
    _publish_new_revision(db_session, user)
    assert resume_facts.facts_are_stale(db_session, user, facts) is True


# Review Focus 1
def test_withdrawing_consent_deletes_the_profile_and_keeps_the_facts(db_session, user):
    # profile + facts both present, then withdraw
    assert profile.delete_profile_for_user(db_session, user) is True
    assert db_session.query(TalentProfile).filter_by(user_id=user.id).count() == 0
    assert facts_for_user(db_session, user) is not None
```

`StubProfileLlm` (`tests/test_talent_profile.py:21`) already records every call
in a `self.calls` list, so `len(stub.calls) == 1` needs no new test machinery.
That assertion is the only thing standing between "one extraction, two
consumers" and a silent doubling of provider spend for every consenting user.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_resume_facts.py -v`
Expected: FAIL — `build_facts` undefined.

- [ ] **Step 3: Implement**

`build_facts` carries the provider call, so it carries the metering wrapper.
Add it to `test_billable_call_sites_are_wrapped`'s inventory in the same commit
— a new call site that the inventory does not know about is the failure that
test exists to catch.

`delete_profile_for_user` must be left alone except to confirm in its docstring
that it deliberately does not touch `resume_facts`, and why.

**The one sanctioned existing-test edit.** Moving the provider call into
`resume_facts.py` breaks `tests/test_talent_profile.py:148`'s
`_stub`, which monkeypatches `app.services.profile.httpx.AsyncClient`. Re-point
it at whichever module now owns the call. That is a patch-target correction, not
a behaviour change: **no assertion in that file may change**. If re-pointing
`_stub` is not enough to keep every one of those tests green, the projection is
not faithful — report it rather than adjusting expectations.

Where both rows are wanted, build once and write both under one `input_hash`;
where only facts are wanted, write only facts. Do not call the provider twice.

- [ ] **Step 4: Run the full suite**

Run: `cd apps/backend && .venv/bin/pytest -q`
Expected: PASS, only the 2 pre-existing failures. Watch
`tests/test_metering.py` in particular.

- [ ] **Step 5: Commit**

```bash
git add -A apps/backend/app apps/backend/tests
git commit -m "feat(resume): build facts without consent, profile as a gated projection"
```

---

### Task 4: `job_preferences` table and migration

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/031_job_preferences.py`
- Test: `apps/backend/tests/test_job_preferences.py`

**Interfaces:**
- Produces: `JobPreference` — `id` PK, `user_id` FK→`users` `ON DELETE CASCADE`
  with `UniqueConstraint("user_id")`, `target_functions` `Text` (pipe-delimited),
  `target_locations` `Text` (pipe-delimited), `target_seniority` `String(16)`
  nullable, `remote_required` nullable `Boolean` (tri-state, matching
  `jobs.remote`), `activity` `String(16)` (`actively_looking` | `browsing` |
  `paused`, default `actively_looking`), `updated_at`
  `DateTime(timezone=True)`.

- [ ] **Step 1: Write the failing test**

```python
def test_job_preference_round_trips_pipe_delimited_fields(db_session):
    db_session.add(JobPreference(user_id=u.id, target_functions="|backend|infra|",
                                 target_locations="|tw|us|", target_seniority="senior",
                                 remote_required=None, activity="actively_looking"))
    db_session.flush(); db_session.expire_all()
    row = db_session.query(JobPreference).filter_by(user_id=u.id).one()
    assert row.target_functions == "|backend|infra|"
    assert row.remote_required is None          # tri-state survives the round trip
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest tests/test_job_preferences.py -v`
Expected: FAIL — `ImportError: cannot import name 'JobPreference'`

- [ ] **Step 3: Add the model and the revision**

`revision = "031_job_preferences"`, `down_revision = "030_resume_facts"`.
`activity` gets no CHECK constraint, matching `028_job_facets.py`'s stated
reasoning: the vocabulary may grow and one writer owns it.

The docstring records that this table is **seeded once and thereafter written
only by the user** — a rebuild of `resume_facts` must never overwrite it. That
rule is the whole point of it being a separate table from the facts.

- [ ] **Step 4: Verify**

Run: `cd apps/backend && .venv/bin/pytest tests/test_job_preferences.py -v && .venv/bin/alembic heads`
Expected: PASS, exactly one head.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/031_job_preferences.py apps/backend/tests/test_job_preferences.py
git commit -m "feat(jobs): add job_preferences table"
```

---

### Task 5: Seed preferences from facts, through the shared taxonomy

The "one taxonomy, both sides" requirement made concrete. Seeding must not
invent a second vocabulary.

**Files:**
- Create: `apps/backend/app/services/jobs/preferences.py`
- Test: `apps/backend/tests/test_job_preferences.py` (append)

**Interfaces:**
- Consumes: Task 2's `ResumeFacts`, Task 4's `JobPreference`, and
  `app/services/jobs/enrich.py`'s `function_of`, `seniority_of`,
  `location_facets`, `default_taxonomy`.
- Produces:
  - `seed_from_facts(facts: ResumeFacts | None) -> dict[str, Any]` — pure, no
    DB write, returns the field values a new `JobPreference` would take.
  - `get_or_seed(db, user) -> JobPreference` — returns the existing row
    untouched, or creates one from `seed_from_facts`. Flushes, never commits.

- [ ] **Step 1: Write the failing tests**

```python
def test_seed_derives_function_and_seniority_from_the_latest_role():
    facts = _facts(roles=[{"title": "Staff Backend Engineer", "employer": "X"}],
                   locations=["Taipei, Taiwan"], years_experience=9)
    seed = seed_from_facts(facts)
    assert seed["target_functions"] == "|backend|"
    assert seed["target_seniority"] == "staff_plus"
    assert seed["target_locations"] == "|tw|"


def test_seed_uses_the_job_taxonomy_not_a_second_vocabulary():
    # every seeded function must be a member of the taxonomy's function list
    valid = {r.function for r in default_taxonomy().function_rules} | {"other"}
    seed = seed_from_facts(_facts(roles=[{"title": "Senior Data Scientist"}]))
    assert set(filter(None, seed["target_functions"].split("|"))) <= valid


# Review Focus 2
def test_seed_from_no_facts_is_empty_not_an_error():
    seed = seed_from_facts(None)
    assert seed["target_functions"] == "" and seed["target_seniority"] is None
    assert seed["activity"] == "actively_looking"


def test_get_or_seed_never_overwrites_a_users_own_row(db_session):
    existing = JobPreference(user_id=u.id, target_functions="|design|", activity="paused")
    db_session.add(existing); db_session.flush()
    assert get_or_seed(db_session, u).target_functions == "|design|"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_job_preferences.py -v`
Expected: FAIL — `ModuleNotFoundError` on `app.services.jobs.preferences`.

- [ ] **Step 3: Implement**

`function_of` takes `(title, description_text)`; pass the role title and an
empty description so only title needles fire — a resume has no job description
and the description needles are tuned for employer boilerplate, which is the
opposite of what a role title is.

For seniority, prefer `seniority_of(latest_role_title)`; fall back to a
years_experience band only when the title yields `unknown`. Put the band
thresholds in this module as named constants, and say in the docstring that the
title is preferred because it is the person's own words, the same bias
`enrich.py` takes.

For locations, run `location_facets` over each entry of `facts.locations` and
seed the **country** codes, lowercase, pipe-delimited. City is too narrow to
seed a filter from and region too broad.

- [ ] **Step 4: Run the tests**

Run: `cd apps/backend && .venv/bin/pytest tests/test_job_preferences.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/preferences.py apps/backend/tests/test_job_preferences.py
git commit -m "feat(jobs): seed job preferences from resume facts via the shared taxonomy"
```

---

### Task 6: Preferences API

**Files:**
- Create: `apps/backend/app/routers/preferences.py`
- Modify: `apps/backend/app/main.py` (register the router)
- Modify: `apps/backend/app/schemas_jobs.py`
- Test: `apps/backend/tests/test_job_preferences_api.py`

**Interfaces:**
- Consumes: Task 5's `get_or_seed`.
- Produces:
  - `GET /v1/me/job-preferences` → `JobPreferenceOut` — seeds on first call and
    persists the seed, so the editor and the matcher agree about what the user
    is being matched on.
  - `PUT /v1/me/job-preferences` with `JobPreferenceIn` → `JobPreferenceOut`.
  - Schemas take and return `target_functions` / `target_locations` as **lists
    of strings**, not pipe-delimited text. The pipe encoding is a storage
    detail; an API that leaks it makes every client reimplement the delimiter.

- [ ] **Step 1: Write the failing tests**

**There is no authenticated-client fixture.** `tests/conftest.py` provides only
`client` and `db_session`. Sign a user in the way `tests/test_consent.py:62`
does — copy its `_signed_in(client, db_session, sub=...)` helper, which creates
the user and sets `SESSION_COOKIE` to `_sign(user.id, get_settings().auth_token_secret)`.
For a published resume, copy `_resume(db_session, user, *, source=..., publish=True)`
from `tests/test_talent_profile.py`; seeding reads `ResumeFacts`, so these tests
also need a facts row — write it directly rather than running a build.

```python
def test_get_seeds_on_first_call_and_persists_it(client, db_session):
    user = _signed_in(client, db_session)
    _facts(db_session, user, roles=[{"title": "Staff Backend Engineer"}],
           locations=["Taipei, Taiwan"])
    body = client.get("/v1/me/job-preferences").json()
    assert body["target_functions"] == ["backend"]
    assert db_session.query(JobPreference).filter_by(user_id=user.id).count() == 1


# Review Focus 2
def test_get_for_a_user_with_no_resume_returns_empty_not_500(client, db_session):
    _signed_in(client, db_session)                      # no resume, no facts
    r = client.get("/v1/me/job-preferences")
    assert r.status_code == 200
    assert r.json()["target_functions"] == []
    assert r.json()["target_seniority"] is None


def test_put_rejects_a_function_outside_the_taxonomy(client, db_session):
    _signed_in(client, db_session)
    r = client.put("/v1/me/job-preferences", json={"target_functions": ["astrology"]})
    assert r.status_code == 422


def test_put_rejects_an_unknown_activity(client, db_session):
    _signed_in(client, db_session)
    assert client.put("/v1/me/job-preferences", json={"activity": "sleeping"}).status_code == 422


def test_put_round_trips_the_tri_state_remote_flag(client, db_session):
    _signed_in(client, db_session)
    for value in (True, False, None):
        got = client.put("/v1/me/job-preferences", json={"remote_required": value}).json()
        assert got["remote_required"] is value


def test_put_is_a_replacement_so_an_omitted_field_is_cleared(client, db_session):
    _signed_in(client, db_session)
    client.put("/v1/me/job-preferences", json={"target_functions": ["backend"]})
    assert client.put("/v1/me/job-preferences", json={}).json()["target_functions"] == []


def test_one_users_preferences_are_invisible_to_another(client, db_session):
    a = _signed_in(client, db_session, sub="sub-a")
    client.put("/v1/me/job-preferences", json={"target_functions": ["design"]})
    _signed_in(client, db_session, sub="sub-b")         # re-signs the same client as B
    assert client.get("/v1/me/job-preferences").json()["target_functions"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_job_preferences_api.py -v`
Expected: FAIL — 404 on an unregistered route.

- [ ] **Step 3: Implement**

Validate `target_functions`, `target_seniority` and `target_locations` against
the taxonomy at the schema boundary and return 422 on an unknown member, the
same way `app/routers/jobs.py` validates its facet parameters — read that
first and match its shape.

`PUT` is a full replacement, not a patch: a field omitted from the body is
cleared, and the docstring says so. A partial-update endpoint cannot express
"I no longer want a seniority filter".

- [ ] **Step 4: Run the full suite**

Run: `cd apps/backend && .venv/bin/pytest -q`
Expected: PASS, only the 2 pre-existing failures.

- [ ] **Step 5: Commit**

```bash
git add -A apps/backend/app apps/backend/tests
git commit -m "feat(api): job preferences read and replace endpoints"
```

---

### Task 7: Preferences editor UI

**Files:**
- Create: `apps/frontend/components/jobs/PreferenceEditor.tsx`
- Create: `apps/frontend/app/[locale]/preferences/page.tsx`
- Modify: `apps/frontend/lib/jobs.ts`
- Modify: `apps/frontend/messages/en.json`, `zh-TW.json`, `zh-CN.json`
- Test: `apps/frontend/lib/jobs.test.ts` (append)

**Interfaces:**
- Consumes: Task 6's two endpoints.
- Produces: `fetchJobPreferences()`, `saveJobPreferences(input)` in
  `lib/jobs.ts`, typed with `JobPreference` mirroring `JobPreferenceOut`.

- [ ] **Step 1: Write the failing test**

`lib/jobs.test.ts` is `node:test` via `tsx` and tests the client functions, not
the component. Pin the two things a typo would break silently:

```ts
test("saveJobPreferences sends lists, not pipe-delimited strings", async () => { ... })
test("fetchJobPreferences maps an absent seniority to null, not the string 'null'", async () => { ... })
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/frontend && npx tsx --test lib/jobs.test.ts`
Expected: FAIL — exports undefined.

- [ ] **Step 3: Build the client and the editor**

Follow `components/jobs/JobSearchForm.tsx` for the select/label idiom — it
already renders the function, seniority and country vocabularies with the
`labeled()` fallback (`key ? t(key) : value`), and reusing it keeps one set of
option labels across search and preferences. Reuse its i18n keys rather than
adding parallel ones; add keys only for what is genuinely new (the activity
vocabulary, the remote tri-state, the page's own copy).

Mirror `app/[locale]/settings/page.tsx` for the page shell and the
save-and-confirm interaction.

The form states plainly, above the fields, that these preferences drive
matching and that the user owns them — seeded once from their resume, never
overwritten afterwards.

- [ ] **Step 4: Verify the build and i18n parity**

Run: `cd apps/frontend && npx tsx --test lib/jobs.test.ts && npx tsc --noEmit && npx tsx --test lib/i18n-parity.test.ts`
Expected: PASS. The only `tsc` error permitted is the pre-existing
`lib/admin-ingest.test.ts` one.

- [ ] **Step 5: Commit**

```bash
git add -A apps/frontend
git commit -m "feat(web): job preferences editor"
```

---

### Task 8: The deterministic prefilter

**Files:**
- Create: `apps/backend/app/jobs/match_sweep.py`
- Test: `apps/backend/tests/test_match_sweep.py`

**Interfaces:**
- Consumes: Task 4's `JobPreference`, `JobFacets`, `Job`,
  `app/services/jobs/store.py`'s `freshness_cutoff`.
- Produces:
  - `SENIORITY_LADDER: tuple[str, ...]` — the ordered ladder, read from
    `data/job_taxonomy.json`, excluding off-ladder members.
  - `prefilter_query(db, pref: JobPreference)` — a SQLAlchemy query over `Job`
    joined to `JobFacets`, returning the hard-filtered set. `count_matches`
    wraps it, and Slice 4 reads the rows themselves for digest candidates, so
    it is public rather than private despite having one caller today.
  - `count_matches(db, pref) -> int`

**A naming discrepancy in the spec, resolved:** the Decomposition and the "Two
cadences" section both say `app/jobs/match_sweep.py`; the Testing section calls
the same thing `prefilter.py`. This plan uses `match_sweep.py`, because two
sections name it that and one does not.

**Scope boundary, stated so an implementer does not invent tables:** the spec's
prefilter also excludes postings with an `applications` row and postings in the
user's last four digests. **Neither table exists until Slices 3 and 4.** Write
the filters this slice can express and leave a single named seam
(`_exclusions(query, pref)`) where the later two attach. Do not create
`applications` or `digests` here.

- [ ] **Step 1: Write the failing tests**

```python
def test_hard_filters_exclude_inactive_stale_and_mismatched(db_session):
    # one matching posting plus one of each failure: inactive, stale
    # last_seen_at, wrong function, wrong country
    assert count_matches(db_session, pref) == 1


# Review Focus 4 -- the ladder is intern < new_grad < junior < mid < senior
# < staff_plus; "manager" and "unknown" are NOT on it.
@pytest.mark.parametrize(
    "target,seniority,admitted",
    [
        ("intern", "intern", True), ("intern", "new_grad", True),
        ("intern", "junior", False),                 # two bands up
        ("staff_plus", "senior", True), ("staff_plus", "staff_plus", True),
        ("staff_plus", "mid", False),
        ("senior", "mid", True), ("senior", "staff_plus", True),
        ("senior", "junior", False),
    ],
)
def test_seniority_admits_only_within_one_band(db_session, target, seniority, admitted):
    _posting(db_session, seniority=seniority, function="backend", country="TW")
    pref = _pref(target_seniority=target, target_functions="|backend|", target_locations="|tw|")
    assert count_matches(db_session, pref) == (1 if admitted else 0)


# Review Focus 4
@pytest.mark.parametrize("off_ladder", ["unknown", "manager"])
def test_off_ladder_seniorities_are_never_admitted_by_band_arithmetic(db_session, off_ladder):
    _posting(db_session, seniority=off_ladder, function="backend", country="TW")
    pref = _pref(target_seniority="intern", target_functions="|backend|", target_locations="|tw|")
    assert count_matches(db_session, pref) == 0


# Review Focus 3
def test_an_empty_axis_is_no_constraint_but_the_always_on_filters_still_apply(db_session):
    _posting(db_session, function="design", country="JP")          # matches: axes empty
    _posting(db_session, function="design", country="JP", is_active=False)
    _posting(db_session, function="design", country="JP", stale=True)
    pref = _pref(target_functions="", target_locations="", target_seniority=None)
    assert count_matches(db_session, pref) == 1


@pytest.mark.parametrize(
    "required,posting_remote,admitted",
    [(True, True, True), (True, False, False),
     (False, True, False), (False, False, True),
     (None, True, True), (None, False, True)],
)
def test_remote_tri_state(db_session, required, posting_remote, admitted):
    _posting(db_session, function="backend", country="TW", remote=posting_remote)
    pref = _pref(target_functions="|backend|", target_locations="|tw|", remote_required=required)
    assert count_matches(db_session, pref) == (1 if admitted else 0)


def test_location_matches_the_whole_country_code_not_a_substring(db_session):
    _posting(db_session, function="backend", country="TW")
    _posting(db_session, function="backend", country="TWX")   # must not match "|tw|"
    assert count_matches(db_session, _pref(target_functions="|backend|",
                                           target_locations="|tw|")) == 1


def test_a_remote_posting_satisfies_a_location_filter_it_does_not_match(db_session):
    # spec: (country/region/city in target_locations) OR remote satisfies remote_required
    _posting(db_session, function="backend", country="US", remote=True)
    assert count_matches(db_session, _pref(target_functions="|backend|",
                                           target_locations="|tw|",
                                           remote_required=True)) == 1
```

`_posting(db_session, **kw)` and `_pref(**kw)` are local helpers you write:
the first inserts a `Job` plus its `JobFacets` row (defaulting to active, fresh
`last_seen_at`, and a distinct `source_id` — `jobs_source_source_id_key` is
unique), the second builds an unsaved `JobPreference`. `stale=True` sets
`last_seen_at` older than `freshness_cutoff()`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_match_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError` on `app.jobs.match_sweep`.

- [ ] **Step 3: Implement**

Decide and document, per axis, what an empty value means. The plan's ruling,
which the tests above encode: **an empty axis is no constraint on that axis**,
because a user who has told us nothing about location should still see matches
rather than none; but `is_active`, the freshness cutoff and the existence of a
`JobFacets` row always apply. Say this in the module docstring — the opposite
reading is defensible and a later reader must know which was chosen.

Seniority is the one axis with ordering. Build the band from
`SENIORITY_LADDER.index()`, clamp at both ends, and treat `unknown` and
`manager` explicitly rather than letting them fall into index arithmetic:
`unknown` is not a level and `manager` is a different axis from seniority. The
taxonomy lists both inside `seniority_rules`, so filtering them out of the
ladder is a deliberate step, not an oversight — write that down.

Match pipe-delimited axes with `LIKE '%|' || value || '|%'`, never a bare
substring.

- [ ] **Step 4: Run the full suite**

Run: `cd apps/backend && .venv/bin/pytest -q`
Expected: PASS, only the 2 pre-existing failures.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/jobs/match_sweep.py apps/backend/tests/test_match_sweep.py
git commit -m "feat(jobs): deterministic per-user match prefilter"
```

---

### Task 9: Surface the count, and the privacy copy

Makes the slice visible and discharges the policy obligation from Decision 1.

**Files:**
- Modify: `apps/backend/app/routers/preferences.py`
- Modify: `apps/frontend/app/[locale]/dashboard/page.tsx`
- Modify: `apps/frontend/lib/jobs.ts`
- Modify: `apps/frontend/messages/{en,zh-TW,zh-CN}.json`
- Modify: the user-facing privacy copy — locate it first
  (`grep -rl "privacy" apps/frontend/app apps/frontend/messages`) and report
  what you found before editing; if there is no privacy page, say so and put
  the sentence on the preferences editor instead.
- Test: `apps/backend/tests/test_job_preferences_api.py` (append)

**Interfaces:**
- Consumes: Task 8's `count_matches`, Task 6's router.
- Produces: `GET /v1/me/matches/count` → `{"count": int}`.

- [ ] **Step 1: Write the failing test**

```python
def test_match_count_is_zero_for_a_user_with_no_preferences(client, user_without_resume):
    assert client.get("/v1/me/matches/count").json() == {"count": 0}

def test_match_count_reflects_the_prefilter(client, db_session, user):
    # seed 3 matching and 2 non-matching postings
    assert client.get("/v1/me/matches/count").json() == {"count": 3}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/backend && .venv/bin/pytest tests/test_job_preferences_api.py -k match_count -v`
Expected: FAIL — 404.

- [ ] **Step 3: Implement the endpoint and the dashboard line**

On `/dashboard`, add one line near the top: the count, the one-line profile
summary the spec's header describes ("senior · backend, infra · Taiwan or
remote"), and two links — *Edit preferences* and *Browse all roles* (`/jobs`).
This is deliberately not the full dashboard; Slice 3 rebuilds that page. Do not
restructure the existing resume list.

Fetch the count alongside the page's existing mount calls rather than adding a
fourth sequential request — the page already runs `getMe` → `claimResumes` →
`listResumes` in series and the spec calls that a waterfall worth not growing.

The copy says **matches**, not **new matches** (Decision 2).

- [ ] **Step 4: Write the privacy sentence**

One sentence, in all three locales, saying that resume content is sent to the
model provider to extract the facts used for job matching, that this happens
for every user with a published resume, and that it is separate from the
employer-introduction consent. Do not overclaim: it is extraction for matching,
not sharing with employers.

- [ ] **Step 5: Verify**

Run: `cd apps/backend && .venv/bin/pytest -q`
Run: `cd apps/frontend && npx tsc --noEmit && npx tsx --test lib/i18n-parity.test.ts`
Expected: PASS; only the 2 pre-existing backend failures and the pre-existing
`admin-ingest.test.ts` `tsc` error.

- [ ] **Step 6: Commit**

```bash
git add -A apps/backend apps/frontend
git commit -m "feat(web): surface the match count and the extraction privacy note"
```
