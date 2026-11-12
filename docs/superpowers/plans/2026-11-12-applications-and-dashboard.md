# Applications and the Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a job hunt a spine — an `applications` row per (person, posting) — and make `/dashboard` the loop's home, reading in loop order from one request.

**Architecture:** One new table and one new service own every status transition; the router layer is thin over it. `GET /v1/dashboard` is a single aggregate that composes four existing readers (preferences, match count, resume facts, disclosure counts) plus the new application list, replacing the page's current `getMe` → `claimResumes` → `listResumes` waterfall rather than adding to it. The match axis gains its exclusion through `match_sweep._exclusions`, the seam slice 2 left empty.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, pytest (SQLite in-memory via `tests/conftest.py`), Next.js App Router, next-intl, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-11-12-job-discovery-loop-design.md` — read the `applications` entry under Data model, Surfaces/Dashboard, Surfaces/`/jobs`, Testing, the slice 3 Decomposition line, and `## Amended 2026-11-12, after slice 2 shipped`. Slices 1 and 2 are shipped and deployed to production.

## Global Constraints

- **Alembic revision number comes from `alembic heads` at implementation time, not from this plan.** Head was `031_job_preferences` when this was written; concurrent branches collide on revision numbers. Number from whatever `alembic heads` prints.
- **The only live Postgres on this host is production.** Never point `DATABASE_URL` or alembic at it from a test or scratch run. `Context impl SQLiteImpl` in alembic output means the environment did not load — stop there. Verify the migration up *and* down against a throwaway database before it goes near the host.
- **`applications` is user-owned and must stay unreachable from every employer-facing read** — `readable_profiles_query` (`app/services/profile.py:264`), `app/routers/talent.py`, `app/routers/talent_me.py`. Same boundary slice 2 holds for `resume_facts`.
- **The pipeline list must not filter `Job.is_active`.** Matching always filters it; your own history must not. A posting you applied to goes inactive on the next ingest pass, and the row has to survive that.
- **No new billable call site.** The resume-health rebuild calls the existing, already-metered `POST /v1/me/resume-facts/refresh`. If `tests/test_metering.py::test_billable_call_sites_are_wrapped` fails, a provider call was added that this slice does not want — fix the call, not `EXPECTED_CALL_SITES`.
- **Another session holds uncommitted work in `apps/frontend/messages/{en,zh-TW,zh-CN}.json`, `globals.css`, `Hero.tsx`, `app/[locale]/page.tsx`, `lib/seo.ts` and `components/landing/`.** It asked to be messaged before those files are touched. For the three locale files, stage HEAD-plus-your-lines blobs (`git show HEAD:<path>` → insert → `git hash-object -w` → `git update-index --cacheinfo`) so that session's edits are neither committed nor lost. Never redirect (`>`) into them.
- **i18n parity is exact**: every key added to `en.json` exists in `zh-TW.json` and `zh-CN.json`. 914 keys each at the time of writing.
- Never commit `apps/frontend/next-env.d.ts`.
- Two pre-existing failures are not yours: `tests/test_grants.py::test_an_open_ended_grant_keeps_counting` and `::test_revoking_bites_immediately_not_at_the_boundary` (both date-dependent). One pre-existing `tsc` error in `lib/admin-ingest.test.ts`.

## Review Focus

Five conditions the spec implies that no task's happy path would exercise. Each has a test in the task that owns the code.

1. **The brand-new signed-in person** — no preference row, no `resume_facts`, no applications, no tailors — loads `/dashboard` and gets all five sections empty at once. Every section must name its own state rather than render blank or 500. The previous slice shipped four separate defects of exactly this shape. *(Task 7, Task 9)*
2. **Saving a posting that already has an application row** (already tailored, already applied). `UniqueConstraint(user_id, job_id)` makes the naive insert a 500; it must advance or no-op instead, and must never move a row backwards from `applied` to `saved`. *(Task 2)*
3. **A guest creating a tailor with `job_id`.** `POST /v1/resumes/{id}/tailors` accepts `user: User | None` — guests legitimately tailor. `applications.user_id` is NOT NULL, so the linkage cannot be written. *(Task 6)*
4. **An application whose posting has gone inactive.** The pipeline must still show it; only matching filters `is_active`. *(Task 2)*
5. **Cross-user access** to confirm, advance or unsave someone else's application — 404, never 403 and never a silent write. *(Task 5)*

## File Structure

**Backend**
- `app/models.py` — add `Application`. Read-only elsewhere.
- `alembic/versions/0NN_applications.py` — one table.
- `app/services/jobs/applications.py` *(new)* — every status transition. The only module that writes `Application.status`.
- `app/services/resume_health.py` *(new)* — the health card's three facts, composed from existing readers.
- `app/jobs/match_sweep.py` — fill `_exclusions`.
- `app/routers/applications.py` *(new)* — save/unsave, advance, confirm.
- `app/routers/dashboard.py` *(new)* — `GET /v1/dashboard`, composition only, no business logic.
- `app/schemas_jobs.py` — application and dashboard schemas.
- `app/schemas.py`, `app/routers/resumes.py` — optional `job_id` on tailor creation.
- `app/main.py` — register the two new routers.

**Frontend**
- `lib/applications.ts` *(new)* — application types and the three action calls.
- `lib/dashboard.ts` *(new)* — the aggregate type and its one fetch.
- `components/jobs/JobActions.tsx` *(new, client)* — Save and *Tailor for this role*, mounted by the server-rendered `JobView`.
- `components/dashboard/ApplicationList.tsx` *(new, client)* — grouped list, one action per row.
- `components/dashboard/ResumeHealthCard.tsx` *(new, client)* — report plus *Rebuild now*.
- `components/dashboard/MatchCountCard.tsx` *(new)* — slot 2 until slice 4: the count plus the spec's *Browse all roles* link to `/jobs`, which is the pushed-to person's only way to go hunting.
- `components/dashboard/EmployerViews.tsx` *(new)* — disclosure count and recency.
- `app/[locale]/dashboard/page.tsx` — restructure to the spec's five-section order, one fetch.
- `messages/{en,zh-TW,zh-CN}.json` — new keys.

---

### Task 1: The `applications` table

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/0NN_applications.py`
- Test: `apps/backend/tests/test_applications_model.py`

**Interfaces:**
- Produces: `Application` with `id` (String(36), uuid default, matching `JobPreference`), `user_id` (FK `users.id`, `ON DELETE CASCADE`, NOT NULL), `job_id` (FK `jobs.id`, `ON DELETE CASCADE`, NOT NULL), `tailor_resume_id` (FK `resumes.id`, `ON DELETE SET NULL`, nullable), `status` (String(16), NOT NULL), `source` (String(16), NOT NULL), `applied_at` (DateTime(timezone=True), nullable), `applied_confirmed_at` (DateTime(timezone=True), nullable), `last_status_at` (DateTime(timezone=True), NOT NULL, `onupdate=_utcnow` as `JobPreference.updated_at` does), `notes` (Text, nullable), `created_at`. `UniqueConstraint("user_id", "job_id", name="applications_user_id_job_id_key")`, index on `user_id`.
- `STATUSES = ("saved", "tailored", "applied", "interviewing", "closed")` and `SOURCES = ("digest", "search")` live in `app/services/jobs/applications.py` (Task 2), not in the model — no CHECK constraint, matching `JobPreference.activity`.

- [ ] **Step 1: Write the failing tests**

```python
def test_application_round_trips(db_session): ...                      # every column survives a flush/expire/reload
def test_one_application_per_user_and_job(db_session):                 # IntegrityError on the second insert
def test_deleting_the_tailor_keeps_the_application(db_session):
    # ON DELETE SET NULL: "deleting the tailor must not erase the record
    # that you applied" (spec, Data model)
def test_deleting_the_user_removes_the_application(db_session): ...    # CASCADE
def test_last_status_at_moves_on_update(db_session): ...               # onupdate fires
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_applications_model.py -v`
Expected: FAIL, `ImportError: cannot import name 'Application'`.

- [ ] **Step 3: Add `Application` to `app/models.py`**

Place it after `JobPreference`. Follow that class for column style and the `_utcnow` default/onupdate. Docstring says what the table is for and why `tailor_resume_id` is SET NULL rather than CASCADE.

- [ ] **Step 4: Write the migration**

`alembic heads` first; name the revision from its output, `down_revision` = that head. `op.create_table` plus the unique constraint and the `user_id` index; `downgrade` drops the table.

- [ ] **Step 5: Verify both directions against a throwaway Postgres**

Create a scratch database (never production — see Global Constraints), `alembic upgrade head`, confirm the table, constraint, both FK delete rules and the index, then `alembic downgrade -1` and confirm the table is gone. Record the commands and output in the task report.

- [ ] **Step 6: Run the suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: the 5 new tests pass; only the 2 known `test_grants.py` failures remain.

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/0NN_applications.py apps/backend/tests/test_applications_model.py
git commit -m "feat(jobs): add the applications table"
```

---

### Task 2: The status machine

**Files:**
- Create: `apps/backend/app/services/jobs/applications.py`
- Test: `apps/backend/tests/test_applications_service.py`

**Interfaces:**
- Consumes: `Application` (Task 1).
- Produces:
  - `STATUSES: tuple[str, ...]`, `SOURCES: tuple[str, ...]`, `GROUP_ORDER = ("interviewing", "applied", "tailored", "saved")`
  - `save(db, *, user, job_id, source="search") -> Application` — creates at `saved`, or returns the existing row untouched if one exists. Never moves a row backwards.
  - `unsave(db, *, user, job_id) -> bool` — deletes only a row still at `saved`; returns False (and deletes nothing) for any further status. True when it deleted.
  - `link_tailor(db, *, user, job_id, tailor_resume_id, source="search") -> Application` — upserts and advances to `tailored`, setting `tailor_resume_id`.
  - `mark_applied(db, *, user, job_id) -> Application` — advances to `applied`, stamps `applied_at`, leaves `applied_confirmed_at` NULL.
  - `confirm_applied(db, *, application) -> Application` — stamps `applied_confirmed_at`.
  - `revert_applied(db, *, application) -> Application` — back to `tailored`, clears `applied_at` and `applied_confirmed_at`.
  - `set_status(db, *, application, status) -> Application` — for the self-reported `interviewing`/`closed` moves; rejects a value outside `STATUSES` with `ValueError`.
  - `pipeline(db, *, user) -> list[Application]` — every row for the user, ordered by `GROUP_ORDER` then `last_status_at` descending, `Job` eager-loaded. **Does not filter `Job.is_active`.**
  - `applied_job_ids(db, *, user) -> set[str]` — used by Task 3.

- [ ] **Step 1: Write the failing tests**

```python
def test_save_creates_a_saved_row(db_session): ...
def test_saving_an_already_tailored_posting_does_not_move_it_backwards(db_session):
    # Review Focus 2: the unique constraint makes the naive insert a 500
    row = link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t.id)
    again = save(db_session, user=u, job_id=j.id)
    assert again.id == row.id and again.status == "tailored"
def test_unsave_removes_only_a_saved_row(db_session):
    assert unsave(db_session, user=u, job_id=saved.id) is True
    assert unsave(db_session, user=u, job_id=applied.id) is False   # and the row still exists
def test_mark_applied_leaves_the_confirmation_owed(db_session):
    assert row.status == "applied" and row.applied_at is not None
    assert row.applied_confirmed_at is None
def test_confirm_then_never_owed_again(db_session): ...
def test_revert_applied_returns_to_tailored_and_clears_both_stamps(db_session): ...
def test_set_status_rejects_an_unknown_value(db_session): ...         # pytest.raises(ValueError)
def test_pipeline_orders_by_group_then_recency(db_session): ...       # GROUP_ORDER exactly
def test_pipeline_keeps_an_application_whose_posting_went_inactive(db_session):
    # Review Focus 4: matching filters is_active; your own history must not
def test_link_tailor_is_idempotent_for_a_double_click(db_session): ...
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_applications_service.py -v`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement the module**

Module docstring records two rulings: why `save` never moves a row backwards, and why `pipeline` does not filter `is_active` (with the contrast to `match_sweep.prefilter_query`, which always does). Advancement is monotonic along `GROUP_ORDER`'s reverse — write it as one helper the transition functions share rather than four separate comparisons.

- [ ] **Step 4: Run the tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/applications.py apps/backend/tests/test_applications_service.py
git commit -m "feat(jobs): own every application status transition in one service"
```

---

### Task 3: Exclude acted-on postings from the match count

**Files:**
- Modify: `apps/backend/app/jobs/match_sweep.py` (the `_exclusions` seam)
- Test: `apps/backend/tests/test_match_sweep.py`

**Interfaces:**
- Consumes: `applied_job_ids` (Task 2).
- Produces: `_exclusions` now excludes any posting the person has an `Application` for, any status. `count_matches` and `prefilter_query` keep their signatures.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_application_row_excludes_a_posting(db_session): ...
@pytest.mark.parametrize("status", ["saved", "tailored", "applied", "interviewing", "closed"])
def test_every_status_excludes(db_session, status): ...
def test_another_users_application_does_not_exclude(db_session): ...
def test_the_exclusion_survives_the_constrains_nothing_guard(db_session):
    # a preference that constrains nothing is still 0, not "everything minus applications"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_match_sweep.py -k exclu -v`
Expected: FAIL — `_exclusions` returns the query unchanged today.

- [ ] **Step 3: Implement `_exclusions`**

A correlated `NOT EXISTS` against `applications` on `(user_id, job_id)`, not a fetched id set — the sweep of a later slice runs this per active user on a schedule. Replace the seam's placeholder docstring with what it now does and what remains for the digest exclusion.

- [ ] **Step 4: Run the file**

Run: `.venv/bin/python -m pytest tests/test_match_sweep.py -q`
Expected: PASS, including the slice 2 tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/jobs/match_sweep.py apps/backend/tests/test_match_sweep.py
git commit -m "feat(jobs): make the match count mean not-yet-acted-on"
```

---

### Task 4: Resume health

**Files:**
- Create: `apps/backend/app/services/resume_health.py`
- Test: `apps/backend/tests/test_resume_health.py`

**Interfaces:**
- Consumes: `facts_for_user`, `facts_are_stale` (`app/services/resume_facts.py`), `Resume.published_revision_id`, `source_resume`.
- Produces: `health(db, *, user) -> ResumeHealth`, a frozen dataclass with `has_master: bool`, `published: bool`, `facts_state: Literal["missing", "stale", "fresh"]`, `matching_revision_current: bool`, `needs_rebuild: bool`. `facts_state` reuses `facts_are_stale` — do not restate the rule. `matching_revision_current` compares `resume_facts.source_revision_id` with the master's `published_revision_id`. `needs_rebuild` is `facts_state != "fresh" or not matching_revision_current`.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_master_resume_is_not_an_error(db_session): ...             # has_master False, needs_rebuild False
def test_master_without_a_published_revision(db_session): ...
def test_missing_facts_need_a_rebuild(db_session): ...
def test_facts_from_an_older_revision_are_not_current(db_session):
    assert h.matching_revision_current is False and h.needs_rebuild is True
def test_fresh_facts_on_the_published_revision_are_healthy(db_session): ...
def test_facts_state_matches_facts_are_stale(db_session):
    # one rule, not two: assert the service agrees with the function it wraps
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_resume_health.py -v`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement the module**

Pure composition over existing readers; no new queries against `resume_facts` beyond `facts_for_user`.

- [ ] **Step 4: Run the tests** — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/resume_health.py apps/backend/tests/test_resume_health.py
git commit -m "feat(resumes): report whether matching runs on the current resume"
```

---

### Task 5: Application endpoints

**Files:**
- Create: `apps/backend/app/routers/applications.py`
- Modify: `apps/backend/app/schemas_jobs.py`, `apps/backend/app/main.py`
- Test: `apps/backend/tests/test_applications_api.py`

**Interfaces:**
- Consumes: Task 2's service.
- Produces:
  - `POST /v1/me/applications` — body `ApplicationCreate(job_id: str, source: Literal["digest","search"] = "search")`, 201, `ApplicationOut`. Idempotent per Task 2's `save`.
  - `DELETE /v1/me/applications/{job_id}` — 204 when it removed a `saved` row, 409 with a reason when the row has moved past `saved`, 404 when there is none.
  - `POST /v1/me/applications/{job_id}/applied` — `mark_applied`, returns `ApplicationOut`.
  - `POST /v1/me/applications/{job_id}/confirm` — body `ApplicationConfirm(applied: bool)`; true confirms, false reverts.
  - `PATCH /v1/me/applications/{job_id}` — body `ApplicationPatch(status: str | None, notes: str | None)`; 422 on a status outside `STATUSES`, and `interviewing`/`closed` are the only values a client has reason to send.
  - `ApplicationOut`: `job_id`, `job_title`, `company`, `apply_url`, `job_is_active`, `status`, `source`, `tailor_resume_id`, `applied_at`, `applied_confirmed_at`, `last_status_at`, `notes`.
  - All four routes take `user: User = Depends(require_user)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_route_requires_a_user(client): ...                     # 401 each
def test_save_is_idempotent_over_two_posts(client, db_session): ...   # one row, 201 then 201
def test_delete_refuses_once_the_row_has_moved_on(client, db_session): ...   # 409
def test_delete_of_nothing_is_404(client, db_session): ...
def test_confirm_true_stamps_and_confirm_false_reverts(client, db_session): ...
def test_patch_rejects_a_status_outside_the_vocabulary(client, db_session): ...  # 422
def test_another_users_application_is_404_not_403(client, db_session):
    # Review Focus 5: and assert the row is unchanged afterwards
def test_out_reports_an_inactive_posting_rather_than_hiding_it(client, db_session): ...
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, 404 on every route.

- [ ] **Step 3: Implement the router and schemas, and register it in `app/main.py`**

Follow `app/routers/preferences.py` for shape: thin handlers, `require_user`, service calls, one commit per request. Look up by `(user_id, job_id)` so a wrong owner is indistinguishable from a missing row.

- [ ] **Step 4: Run the file** — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/routers/applications.py apps/backend/app/schemas_jobs.py apps/backend/app/main.py apps/backend/tests/test_applications_api.py
git commit -m "feat(jobs): expose the application pipeline over the API"
```

---

### Task 6: `job_id` on tailor creation

**Files:**
- Modify: `apps/backend/app/schemas.py:52` (`TailorCreate`), `apps/backend/app/routers/resumes.py:313` (`create_tailor_route`)
- Test: `apps/backend/tests/test_resume_tailors.py` (add to the existing file; create it only if absent)

**Interfaces:**
- Consumes: `link_tailor` (Task 2).
- Produces: `TailorCreate.job_id: str | None = None`. When present *and* the request has a signed-in user, the route calls `link_tailor` after `create_tailor` succeeds, inside the same transaction, so a tailor is never created without its linkage or vice versa.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_tailor_with_a_job_id_writes_the_application(client, db_session): ...  # status "tailored"
def test_a_tailor_without_a_job_id_writes_no_application(client, db_session): ...
def test_a_guest_tailor_with_a_job_id_still_creates_the_tailor(client, db_session):
    # Review Focus 3: applications.user_id is NOT NULL. The tailor is the
    # point of the request; the linkage is not. 201, no application row.
def test_an_unknown_job_id_is_422_and_creates_no_tailor(client, db_session): ...
def test_tailoring_a_posting_already_applied_to_does_not_move_it_backwards(client, db_session): ...
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, `TailorCreate` has no `job_id`.

- [ ] **Step 3: Implement**

Add the field; in the route, after `create_tailor`, link when `user is not None and body.job_id`. Validate the job exists before writing, and raise 422 so the failure is the client's bad id rather than a 500. Comment the guest case: dropping the linkage is deliberate, not an oversight.

- [ ] **Step 4: Run the file** — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/schemas.py apps/backend/app/routers/resumes.py apps/backend/tests/test_resume_tailors.py
git commit -m "feat(resumes): link a tailor to the posting it was made for"
```

---

### Task 7: `GET /v1/dashboard`

**Files:**
- Create: `apps/backend/app/routers/dashboard.py`
- Modify: `apps/backend/app/schemas_jobs.py`, `apps/backend/app/main.py`
- Test: `apps/backend/tests/test_dashboard_api.py`

**Interfaces:**
- Consumes: `current_or_seeded` (`app/services/jobs/preferences.py`), `count_matches` (Task 3's behaviour), `pipeline` (Task 2), `health` (Task 4), `disclosure_count`/`last_disclosed_at` (`app/services/disclosure.py`), `ApplicationOut` (Task 5).
- Produces: `GET /v1/dashboard` → `DashboardOut` with `preference: JobPreferenceOut`, `preference_summary_parts` omitted (the frontend owns copy via the existing `preferenceSummary`), `match_count: int`, `applications: list[ApplicationOut]`, `resume_health: ResumeHealthOut` (the five fields of Task 4's `ResumeHealth`, unchanged in name and type), `employer_views: EmployerViewsOut(disclosure_count: int, last_disclosed_at: str | None)`. `user: User = Depends(require_user)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_requires_a_user(client): ...                                  # 401
def test_the_brand_new_person_gets_every_section_empty(client, db_session):
    # Review Focus 1: no preference row, no facts, no applications, no tailors.
    # 200; preference is the transient seed; match_count 0; applications [];
    # resume_health.has_master False; employer_views.disclosure_count 0.
def test_it_returns_the_whole_view_in_one_request(client, db_session):
    # assert every top-level key is present, so the page never needs a second call
def test_applications_come_back_in_group_order(client, db_session): ...
def test_it_never_persists_a_preference_row(client, db_session):
    # the transient seed of slice 2 must stay transient on a GET
def test_applications_are_unreachable_from_the_employer_path(client, db_session):
    # Global Constraints: assert readable_profiles_query and the talent
    # endpoints expose no application data for a consenting user
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, 404.

- [ ] **Step 3: Implement the router and schemas, and register it**

Composition only — every number comes from a function above, none is recomputed here. Reuse `preferences.py`'s `_out` rather than building a second `JobPreferenceOut` projection; extract it to a shared helper if that needs an import cycle broken.

- [ ] **Step 4: Run the file, then the suite** — Expected: PASS; only the 2 known failures.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/routers/dashboard.py apps/backend/app/schemas_jobs.py apps/backend/app/main.py apps/backend/tests/test_dashboard_api.py
git commit -m "feat(web): serve the whole dashboard in one request"
```

---

### Task 8: Frontend client layer and the job actions

**Files:**
- Create: `apps/frontend/lib/applications.ts`, `apps/frontend/lib/dashboard.ts`, `apps/frontend/components/jobs/JobActions.tsx`
- Modify: `apps/frontend/components/jobs/JobView.tsx`, `apps/frontend/messages/{en,zh-TW,zh-CN}.json`
- Test: `apps/frontend/lib/applications.test.ts`

**Interfaces:**
- Consumes: Task 5 and Task 7's endpoints; `apiJson` (`lib/api.ts`), `ApiError`.
- Produces:
  - `lib/applications.ts`: `ApplicationStatus`, `Application`, `GROUP_ORDER` (the same four values in the same order as Task 2's backend constant, which is authoritative — if they ever disagree the groups render out of the spec's heat order), `saveJob(jobId)`, `unsaveJob(jobId)`, `markApplied(jobId)`, `confirmApplied(jobId, applied: boolean)`, `patchApplication(jobId, input)`, and `groupApplications(items): {status, items}[]` in `GROUP_ORDER`, skipping empty groups.
  - `lib/dashboard.ts`: `Dashboard` type and `fetchDashboard(): Promise<Dashboard>`.
  - `JobActions.tsx` *(client)*: props `{ jobId, applyUrl, initial: Application | null, masterResumeId: string | null }`. Save/Saved toggle, *Tailor for this role* (calls `createTailor` with `job_id`, then routes to the editor), and the outbound apply link that calls `markApplied` before opening. Signed-out: the actions render as links to login rather than failing — a guest clicking Save gets the login page, not a 401 toast.

- [ ] **Step 1: Write the failing tests**

```ts
test("groupApplications returns groups in GROUP_ORDER and omits empty ones", ...)
test("groupApplications keeps recency order inside a group", ...)
test("an unknown status does not vanish from the grouping", ...)  // forward compatibility
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd apps/frontend && npx tsx --test lib/applications.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement the two lib modules and `JobActions.tsx`**

`JobView` is an async server component — mount `JobActions` as a client child and pass the server-fetched `initial` down; do not make `JobView` a client component.

- [ ] **Step 4: Add the i18n keys**

New keys under `jobs` and a new `dashboard` section: save/saved/unsave, tailorForThisRole, apply, didYouApply/yes/no, the five section headings, the group labels, the quiet-time and ready-ago lines, the health states, employer-views copy, and every empty state from Review Focus 1. **Message the peer session before editing the locale files** and stage them as HEAD-plus-your-lines blobs (Global Constraints).

- [ ] **Step 5: Verify the tests, types and parity**

Run: `npx tsx --test lib/applications.test.ts` → PASS; `npx tsc --noEmit` → only the known `lib/admin-ingest.test.ts` error; a parity check over the three locale files → equal key sets.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/lib/applications.ts apps/frontend/lib/dashboard.ts apps/frontend/components/jobs/JobActions.tsx apps/frontend/components/jobs/JobView.tsx apps/frontend/lib/applications.test.ts
git commit -m "feat(web): save a posting and tailor for it from the job page"
```

---

### Task 9: The dashboard page

**Files:**
- Modify: `apps/frontend/app/[locale]/dashboard/page.tsx`
- Create: `apps/frontend/components/dashboard/ApplicationList.tsx`, `ResumeHealthCard.tsx`, `MatchCountCard.tsx`, `EmployerViews.tsx`
- Test: `apps/frontend/components/dashboard/applications.test.ts`

**Interfaces:**
- Consumes: Task 8's lib modules; `preferenceSummary` (`lib/jobs.ts`, slice 2 — reuse, do not rewrite); `refreshResumeFacts` (`lib/jobs.ts`) for *Rebuild now*.
- Produces: the page in the spec's order — header (profile summary + edit link), slot 2 (`MatchCountCard`), `ApplicationList`, `ResumeHealthCard`, `EmployerViews` — from one `fetchDashboard()`. The existing resume file manager moves to the bottom, below `EmployerViews`.

- [ ] **Step 1: Write the failing test**

```ts
test("nextAction maps each status to exactly one action", ...)
// saved -> "tailor", tailored -> "apply", applied|interviewing -> "logResponse",
// closed -> none; and an applied row with applied_confirmed_at null -> "confirm"
test("quietDays counts from last_status_at, not created_at", ...)
test("readyAgeDays is reported for tailored rows only", ...)
```

- [ ] **Step 2: Run it and watch it fail** — Expected: FAIL, module not found.

- [ ] **Step 3: Implement the four components**

`nextAction` and the age helpers live in `components/dashboard/applications.ts` so the test can reach them without rendering. `ApplicationList` renders group headings from `groupApplications`, one action per row, the tailor as a link inside its own row when `tailor_resume_id` is set (the spec demotes tailors from top-level objects to exactly this), the "ready N days ago" line on `tailored` (the spec names this as where the loop leaks — it is deliberately prominent), "N days quiet" on `applied`, and the inline "did you apply?" on an `applied` row whose `applied_confirmed_at` is null.

- [ ] **Step 4: Rewrite the page's data loading**

One `fetchDashboard()` replacing the `getMe` → `claimResumes` → `listResumes` waterfall at `page.tsx:107-142`. `claimResumes` still has to run for a just-signed-in guest — keep it, and keep it off the critical path as the current comment at line 109 describes. Every section renders its empty state rather than null (Review Focus 1).

- [ ] **Step 5: Verify**

Run: `npx tsx --test components/dashboard/applications.test.ts` → PASS; `npx tsc --noEmit` → only the known error; then the headless-Chrome check in the project's frontend-verification notes against a stub API, confirming all five sections render for a brand-new user and for a user with one row in each group.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/app/\[locale\]/dashboard/page.tsx apps/frontend/components/dashboard/
git commit -m "feat(web): make the dashboard read in loop order"
```
