# Scoring and the Weekly Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the deterministic filter into a reason to come back — a weekly digest of scored postings in two tracks, *Strong fit* and *New this week*, built by a scheduled job and shown in the dashboard's slot 2.

**Architecture:** One scoring pass per (resume, posting) pair on `/v1/decisions` produces a tier, a numeric score and four component probabilities; a second Responses call writes one reason and one optional gap per survivor, validated against `resume_facts` so no sentence can claim experience the resume does not have. `build_digests.py` composes those into frozen `digests`/`digest_items` rows, classifying each scored candidate into either or both tracks. The dashboard reads the latest digest through the existing `GET /v1/dashboard` aggregate and falls back to slice 3's match-count card when both tracks are empty.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, `httpx`, pytest (SQLite in-memory via `tests/conftest.py`), systemd **user** units, Next.js App Router, next-intl, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-11-12-job-discovery-loop-design.md` — read *Scoring on the Decisions API*, *Reason and gap prose*, *Digest assembly and scheduling*, *Metering*, *Failure modes*, *Surfaces/Dashboard* item 2, the `digests`/`digest_items` entry under *Data model*, and above all **`## Amended 2026-11-13, before slice 4`**, which supersedes parts of the older sections and holds every decision. Slices 1–3 are shipped and deployed.

## Global Constraints

- **Two tracks, one scoring pass.** *Strong fit* scores the whole eligible pool and admits **only** the rubric levels `Strong fit` and `Good fit`; it is a standing shortlist with **no digest-based exclusion** — it repeats until the person acts, relying solely on the applications exclusion. *New this week* is bounded by `first_seen_at` inside the digest period and admits anything above `Not a fit`. A posting may qualify for both and still costs **one** Decisions call.
- **`match_sweep._exclusions`' recent-digest seam is needed by neither track.** Close it as unnecessary; do not fill it.
- **Up to five per track, never padded.** Fewer is correct. The prose step is explicitly permitted to return fewer than five and none.
- **A thin or empty week falls back to slice 3's `MatchCountCard`** in slot 2 — never a shortfall message.
- **The numeric `fit_score` IS displayed**, beside its four-level tier, not instead of it.
- **A refusal is its own answer type, never a low probability.** On a refusal for one pair, drop that candidate; never score it zero.
- **Alembic revision number comes from `alembic heads` at implementation time**, not from this plan. Head was `032_applications` when this was written; concurrent branches collide.
- **Migrations have no test coverage and the only live Postgres is PRODUCTION.** Verify each revision up *and* down against a throwaway container before it goes near the host. `Context impl SQLiteImpl` in alembic output means the env did not load — stop there.
- **Two new metering purposes under `system_guard`**: `match` (Decisions) and `digest` (prose), following `app/jobs/refresh_profiles.py` verbatim. `purpose="match"` records `input_tokens` only, every other usage field `None` — the shape `turn.py` emits for `purpose="vision"`. **A digest build must never consume the person's edit allowance.**
- **`tests/test_metering.py::test_billable_call_sites_are_wrapped` is an inventory that fails when a new call site appears.** Both new sites go in it. Editing that test to pass without wrapping the call is the one shortcut that defeats the whole design.
- **Digest text is frozen once written** — same discipline as `ResumeRevision` and `profile_json`. Derived, but immutable after display.
- **`digests` and `digest_items` must stay unreachable from `readable_profiles_query` and every employer-facing read** (`app/routers/talent.py`, `talent_me.py`).
- **i18n parity is exact** across `messages/{en,zh-TW,zh-CN}.json`, and a missing key **throws at render** rather than falling back to English — so a passing build proves nothing. The dashboard route needs an actual render in all three locales.
- Never commit `apps/frontend/next-env.d.ts`. Never run `npm run build:public` from `/home/ruby0322/offerfy` — that `.next` is what the live site serves.
- **Another session owns the landing page and all marketing copy.** The twelve-string copy gate is enumerated in Task 9 and is **theirs to word**; this plan does not draft it.
- Two pre-existing backend failures are not this slice's: `tests/test_grants.py::test_an_open_ended_grant_keeps_counting` and `::test_revoking_bites_immediately_not_at_the_boundary`. One pre-existing `tsc` error in `lib/admin-ingest.test.ts`.

## Review Focus

Five conditions the spec implies that no happy path would exercise. Each has a test in the task that owns the code.

1. **A refusal on one pair mid-batch.** `asyncio.gather` over ~30 candidates; one comes back `type: "refusal"`. That candidate must be dropped from the digest entirely, never recorded with `fit_score` 0, and the other 29 must still produce a digest. Reading a refusal as a low probability is the specific failure the spec names. *(Task 2)*
2. **A reason citing experience the resume does not have.** The only free text shown to a person as a claim about their own history. The item must keep its tier and score and lose its sentence; it must not be dropped, and the sentence must not survive. *(Task 3)*
3. **The spend ceiling hit partway through a run.** The user in flight gets a complete digest; everyone after gets none and is logged by id. Nobody gets a half-scored digest, and the job never 429s itself. *(Task 5)*
4. **Decisions absent or 4xx for the whole run.** `status=degraded`: prefilter order, no tiers, no scores, no reasons — and the card *says so*. Silently reordering by a worse signal while showing confident tiers is the thing the spec forbids. *(Task 5, Task 8)*
5. **Both tracks empty for a person with a preference.** Slot 2 renders slice 3's match-count card, not an empty digest and not a shortfall. This is the common case for a thin market, not an edge case — a Taiwan backend preference has 15 eligible postings total. *(Task 8, Task 9)*

All five rows of the spec's *Failure modes* table have a test: Decisions absent (Task 5), a refusal on one pair (Task 2), prose failing after scoring (Task 3), an empty prefilter (Task 5), and unbuildable resume facts (Task 5).

## File Structure

**Backend**
- `app/models.py` — add `Digest` and `DigestItem`. Read-only elsewhere.
- `alembic/versions/0NN_digests.py` — two tables.
- `app/services/jobs/score.py` *(new)* — the Decisions call and its answer parsing. The only module that talks to `/v1/decisions` for matching.
- `app/services/jobs/reasons.py` *(new)* — the prose call and the citation validator. The only module that produces user-visible match prose.
- `app/services/jobs/digest.py` *(new)* — track classification and digest reads. Pure except for the reads.
- `app/jobs/build_digests.py` *(new)* — the scheduled build: eligibility, orchestration, spend ceiling, degraded path, CLI entry.
- `app/jobs/match_sweep.py` — close the `_exclusions` recent-digest seam.
- `app/schemas_jobs.py` — digest schemas.
- `app/routers/dashboard.py` — add the digest to the aggregate.
- `~/.config/systemd/user/offerfy-digest.{timer,service}` + `offerfy-digest-failure.service` — not in git; recorded in `docs/deploy.md`.

**Frontend**
- `lib/digest.ts` *(new)* — the digest types and the two-track grouping helper.
- `components/dashboard/DigestCard.tsx` *(new, client)* — slot 2: two labelled groups, or the degraded notice, or nothing so the page falls back.
- `app/[locale]/dashboard/page.tsx` — render `DigestCard` in slot 2, falling back to `MatchCountCard`.
- `messages/{en,zh-TW,zh-CN}.json` — new keys.

---

### Task 1: The `digests` and `digest_items` tables

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/0NN_digests.py`
- Test: `apps/backend/tests/test_digest_model.py`

**Interfaces:**
- Produces: `Digest` with `id` (String(36), uuid default, as `Application` does), `user_id` (FK `users.id`, `ON DELETE CASCADE`, NOT NULL), `period_start` (DateTime(timezone=True), NOT NULL), `built_at` (DateTime(timezone=True), NOT NULL), `model` (String(64), NOT NULL), `input_hash` (String(64), NOT NULL), `status` (String(16), NOT NULL), `candidate_count` (Integer, NOT NULL), `opened_at` (DateTime(timezone=True), nullable). `UniqueConstraint("user_id", "period_start", name="digests_user_id_period_start_key")`, index on `user_id`.
- `DigestItem` with `id`, `digest_id` (FK `digests.id`, `ON DELETE CASCADE`, NOT NULL), `job_id` (FK `jobs.id`, `ON DELETE CASCADE`, NOT NULL), `rank` (Integer, NOT NULL), `track` (String(16), NOT NULL), `tier` (String(16), nullable), `fit_score` (Float, nullable), `reason` (Text, nullable), `gap` (Text, nullable), `requirements_met`/`seniority_fit`/`location_fit`/`direction_fit` (Float, nullable). `UniqueConstraint("digest_id", "job_id", "track", name="digest_items_digest_id_job_id_track_key")`, index on `digest_id`.
- `STATUSES = ("full", "degraded", "empty")` and `TRACKS = ("strong_fit", "new_this_week")` live in `app/services/jobs/digest.py` (Task 4), not in the model — no CHECK constraint, matching `JobPreference.activity` and `Application.status`.

The unique constraint carries `track` because one posting can legitimately appear in both tracks of one digest; `(digest_id, job_id)` alone would forbid the thing the spec requires.

- [ ] **Step 1: Write the failing tests**

```python
def test_digest_round_trips(db_session): ...                  # every column survives flush/expire/reload
def test_one_digest_per_user_and_period(db_session): ...       # IntegrityError on the second insert
def test_a_posting_can_appear_in_both_tracks(db_session):
    # the reason `track` is in the unique constraint
def test_the_same_posting_twice_in_one_track_is_refused(db_session): ...
def test_deleting_the_digest_removes_its_items(db_session): ...        # CASCADE
def test_deleting_the_user_removes_the_digest(db_session): ...         # CASCADE
def test_tier_and_score_are_nullable_for_a_degraded_digest(db_session):
    # a degraded digest has items with no tier, no score and no prose
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_digest_model.py -v`
Expected: FAIL, `ImportError: cannot import name 'Digest'`.

- [ ] **Step 3: Add `Digest` and `DigestItem` to `app/models.py`**

Place them after `Application`. Follow that class for column style and the `_utcnow` default. **`last_status_at`'s lesson applies: do not add `onupdate` to anything here** — a digest is frozen after it is written, so a column that moves on any UPDATE would be a lie. Each class's docstring says what the table is for; `DigestItem`'s says why `track` is in the unique constraint and why the four component probabilities are stored rather than derived.

- [ ] **Step 4: Write the migration**

`alembic heads` first; name the revision from its output, `down_revision` = that head. `op.create_table` twice plus both unique constraints and both indexes; `downgrade` drops `digest_items` then `digests`.

- [ ] **Step 5: Verify both directions against a throwaway Postgres**

Start a disposable container (never production — see Global Constraints), `alembic upgrade head`, confirm both tables, both constraints, all four FK delete rules and both indexes by querying `pg_constraint` and `pg_indexes` directly rather than trusting the migration's output. Then `alembic downgrade -1` and confirm both tables are gone. Record the commands and output in the task report.

- [ ] **Step 6: Run the suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: the new tests pass; only the 2 known `test_grants.py` failures remain.

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/0NN_digests.py apps/backend/tests/test_digest_model.py
git commit -m "feat(jobs): add the digests and digest_items tables"
```

---

### Task 2: Scoring on the Decisions API

**Files:**
- Create: `apps/backend/app/services/jobs/score.py`
- Modify: `apps/backend/app/services/layout.py` *(add the `fit` rubric to `SCORE_RUBRICS`)*, `apps/backend/tests/test_metering.py` *(the inventory)*
- Test: `apps/backend/tests/test_jobs_score.py`

**Interfaces:**
- Consumes: `decisions_url()`, `SCORE_RUBRICS`, `_error_detail()` and the refusal handling of `_vision_answers()` from `app/services/layout.py`; `settings.openai_decisions_model`; `system_guard` from `app/services/metering.py`.
- Produces:
  - `FIT_RUBRIC = "fit"` with levels `("Strong fit", "Good fit", "Worth a look", "Not a fit")`, and `PREDICATES = ("requirements_met", "seniority_fit", "location_fit", "direction_fit")`.
  - `@dataclass(frozen=True) class Scored` — `job_id: str`, `tier: str`, `fit_score: float`, `components: dict[str, float]`.
  - `@dataclass class ScoreRun` — `scored: list[Scored]`, `refused: list[str]`, `error: str | None`, `usage: dict[str, Any]`, `model: str`. A provider failure sets `error` and leaves `scored` empty; it never raises.
  - `async def score_candidates(db, *, user, facts, jobs, concurrency: int = 4) -> ScoreRun` — one Decisions call per (facts, job) pair, `asyncio.gather` behind an `asyncio.Semaphore(concurrency)`.
- The `fit` rubric must be added to `SCORE_RUBRICS` in `layout.py` with the four levels above and its instructions, so `_score_questions` can build it; that is the only edit to `layout.py`.

- [ ] **Step 1: Write the failing tests**

All against recorded payloads; **no live provider in tests** (spec, Testing).

```python
async def test_one_call_per_pair_not_one_call_for_many(monkeypatch):
    # 3 jobs -> 3 request bodies, each carrying exactly one posting.
    # The spec's reason: "one request answers one question about its whole
    # input" -- _chosen_pages already paid for this lesson.
async def test_the_four_levels_map_to_a_tier_and_a_score(monkeypatch): ...
async def test_a_refusal_drops_that_candidate_and_never_scores_it_zero(monkeypatch):
    # Review Focus 1: 3 jobs, the middle one refuses.
    assert [s.job_id for s in run.scored] == ["a", "c"]
    assert run.refused == ["b"]
    assert all(s.fit_score > 0 for s in run.scored)
async def test_a_missing_probabilities_list_is_an_error_not_a_zero(monkeypatch): ...
async def test_a_job_id_absent_from_the_candidate_set_is_dropped(monkeypatch): ...
async def test_a_provider_4xx_sets_error_and_scores_nothing(monkeypatch):
    assert run.error and run.scored == []
async def test_usage_records_input_tokens_only(monkeypatch):
    # purpose="match" shape: input_tokens set, every other field None,
    # because the endpoint has no cache line and no output charge.
async def test_the_posting_text_is_trimmed_to_the_spec_budget(monkeypatch):
    # title, company, location and ~1500 chars of requirements
async def test_concurrency_is_bounded(monkeypatch): ...   # never more than `concurrency` in flight
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, module not found.

- [ ] **Step 3: Implement the module**

Module docstring records why one call per pair rather than one call for thirty, quoting the spec's `_chosen_pages` precedent, and why a refusal is not a zero. Wrap the provider call in `system_guard(user=..., purpose="match", reason=...)` — `reason` is required free text so the exemption is a named thing a reviewer sees.

- [ ] **Step 4: Add the call site to the metering inventory**

Add it to `tests/test_metering.py::test_billable_call_sites_are_wrapped`'s expected set. **Do not make that test pass by editing the expected set alone** — the wrap in Step 3 is what the test exists to check.

- [ ] **Step 5: Run the tests, then the suite** — Expected: PASS; only the 2 known failures.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/services/jobs/score.py apps/backend/app/services/layout.py apps/backend/tests/test_jobs_score.py apps/backend/tests/test_metering.py
git commit -m "feat(jobs): score a resume against one posting on the Decisions API"
```

---

### Task 3: Reason and gap prose, and the citation validator

**Files:**
- Create: `apps/backend/app/services/jobs/reasons.py`
- Modify: `apps/backend/tests/test_metering.py`
- Test: `apps/backend/tests/test_jobs_reasons.py`

**Interfaces:**
- Consumes: `Scored` (Task 2); `resume_facts.skills_text` / `search_text`; `app/services/profile.py` as the structural precedent — strict structured output, hand-written JSON schema, fixed cheap model, pydantic validation after, `clamp_payload`'s length discipline.
- Produces:
  - `@dataclass(frozen=True) class Prose` — `job_id: str`, `reason: str`, `gap: str | None`.
  - `@dataclass class ProseRun` — `prose: list[Prose]`, `dropped: list[tuple[str, str]]` (job_id, why), `error: str | None`, `usage`, `model`.
  - `async def write_reasons(db, *, user, facts, scored, jobs) -> ProseRun` — **one call per digest**, over the survivors only.
  - `def validate(items, *, facts, scored_ids) -> tuple[list[Prose], list[tuple[str, str]]]` — pure, and the heart of the task.

- [ ] **Step 1: Write the failing tests**

The validator is pure, so most of these need no provider at all.

```python
def test_an_unknown_job_id_is_dropped(): ...
def test_a_reason_citing_an_absent_skill_loses_its_sentence_not_its_item():
    # Review Focus 2. The item keeps tier and score; reason becomes None.
    kept, dropped = validate([...], facts=facts, scored_ids={"a"})
    assert kept == [] and dropped == [("a", "uncited")]
def test_a_reason_citing_a_real_skill_survives(): ...
def test_a_reason_citing_a_real_employer_survives(): ...
def test_a_manufactured_gap_is_rejected_but_a_null_gap_is_fine(): ...
def test_a_long_reason_is_clamped_not_rejected():
    # clamp_payload's reason: truncating loses a clause, rejecting loses the item
def test_fewer_than_five_is_allowed_and_so_is_none():
    # the honest empty state is only honest if the model may produce it
async def test_a_provider_failure_keeps_the_scores_and_drops_the_prose(monkeypatch):
    # Failure modes table: real tiers, scores and order; rows render bare
async def test_one_call_per_digest_not_one_per_item(monkeypatch): ...
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, module not found.

- [ ] **Step 3: Implement the module**

The prompt carries the spec's four prohibitions verbatim in `profile.py`'s voice: it may return fewer than five and may return none; every reason cites something from the resume facts **and** something from the posting; a gap is `null` when there is none; no invented experience. The module docstring says why the validator is stricter than `profile.py`'s — this is the only free text shown to a person as a claim about their own history, and an unverifiable sentence is worse than no sentence. Wrap the call in `system_guard(user=..., purpose="digest", reason=...)`.

- [ ] **Step 4: Add the call site to the metering inventory** — same discipline as Task 2 Step 4.

- [ ] **Step 5: Run the tests, then the suite** — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/services/jobs/reasons.py apps/backend/tests/test_jobs_reasons.py apps/backend/tests/test_metering.py
git commit -m "feat(jobs): write match prose that cannot claim what the resume does not say"
```

---

### Task 4: Track classification and digest reads

**Files:**
- Create: `apps/backend/app/services/jobs/digest.py`
- Test: `apps/backend/tests/test_jobs_digest.py`

**Interfaces:**
- Consumes: `Digest`, `DigestItem` (Task 1); `Scored` (Task 2); `Prose` (Task 3).
- Produces:
  - `STATUSES = ("full", "degraded", "empty")`, `TRACKS = ("strong_fit", "new_this_week")`, `STRONG_TIERS = ("Strong fit", "Good fit")`, `ITEMS_PER_TRACK = 5`.
  - `def classify(scored, *, new_job_ids) -> dict[str, list[Scored]]` — **pure.** Returns both tracks, each ordered by `fit_score` descending and truncated to `ITEMS_PER_TRACK`. `strong_fit` admits only `STRONG_TIERS`; `new_this_week` admits anything whose tier is not `"Not a fit"` **and** whose `job_id` is in `new_job_ids`. A posting may appear in both.
  - `def latest_for_user(db, *, user) -> Digest | None` — the most recent by `period_start`, items eager-loaded with their `Job`.
  - `def mark_opened(db, *, digest) -> Digest` — stamps `opened_at` once; idempotent. This is what the eligibility gate in Task 5 reads.

- [ ] **Step 1: Write the failing tests**

```python
def test_strong_fit_admits_only_the_top_two_levels():
    # "Worth a look" and "Not a fit" never appear in strong_fit
def test_new_this_week_admits_worth_a_look_but_not_not_a_fit(): ...
def test_new_this_week_is_bounded_by_the_new_job_ids(): ...
def test_a_posting_can_land_in_both_tracks(): ...
def test_each_track_is_ordered_by_score_and_capped_at_five(): ...
def test_neither_track_is_padded_when_there_are_fewer_than_five(): ...
def test_latest_for_user_returns_the_newest_period(db_session): ...
def test_latest_for_user_ignores_another_users_digest(db_session): ...
def test_mark_opened_stamps_once_and_is_idempotent(db_session): ...
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, module not found.

- [ ] **Step 3: Implement the module**

Module docstring records the two-track decision and its reason from the spec: *"we found 1 role worth your time" reads as failure; "1 new role opened this week" reads as a fact* — and that `strong_fit` deliberately has no digest-based exclusion, so it repeats until the person acts, with the applications exclusion doing the dropping.

- [ ] **Step 4: Run the tests** — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/digest.py apps/backend/tests/test_jobs_digest.py
git commit -m "feat(jobs): classify scored candidates into the two digest tracks"
```

---

### Task 5: The build job

**Files:**
- Create: `apps/backend/app/jobs/build_digests.py`
- Modify: `apps/backend/app/jobs/match_sweep.py` *(close the `_exclusions` seam)*
- Test: `apps/backend/tests/test_build_digests.py`

**Interfaces:**
- Consumes: `prefilter_query` (`app/jobs/match_sweep.py`), `current_or_seeded` (`app/services/jobs/preferences.py`), `facts_for_user` / `facts_are_stale` (`app/services/resume_facts.py`), `score_candidates` (Task 2), `write_reasons` (Task 3), `classify` (Task 4), `system_guard`.
- Produces:
  - `CANDIDATE_CAP = 30` — how many prefilter survivors get scored per user.
  - `async def build_for_user(db, *, user, period_start, now=None) -> Digest | None` — returns `None` when the person is not eligible or has no preference row.
  - `async def build_all(db, *, period_start, now=None) -> BuildReport` — walks eligible users, stops cleanly on the ceiling.
  - `@dataclass class BuildReport` — `built: list[str]`, `skipped_no_preference: list[str]`, `skipped_not_engaged: list[str]`, `skipped_over_ceiling: list[str]`, `degraded: list[str]`.
  - `def main(argv=None) -> int` — `argparse`, following `app/jobs/refresh_profiles.py`'s CLI shape exactly, with `--period-start` and `--user-id` for a single-user run.
- Eligibility, from the spec: `job_preferences.activity` is not `paused` **and** the person opened one of their last two digests; **first-time users with a `job_preferences` row are always built for once**. No preference row → no digest. Stale facts → rebuild `resume_facts` **before** scoring, never after.

- [ ] **Step 1: Write the failing tests**

```python
async def test_no_preference_row_means_no_digest(db_session): ...
async def test_a_paused_preference_is_skipped(db_session): ...
async def test_a_first_time_user_is_always_built_for_once(db_session): ...
async def test_a_user_who_opened_neither_of_their_last_two_is_skipped(db_session): ...
async def test_a_user_who_opened_one_of_the_last_two_is_built_for(db_session): ...
async def test_stale_facts_are_rebuilt_before_scoring_not_after(db_session): ...
async def test_the_ceiling_finishes_the_user_in_flight_then_stops(db_session):
    # Review Focus 3: the in-flight user's digest is complete; later users
    # appear in skipped_over_ceiling by id; nobody is half-scored.
async def test_decisions_absent_writes_a_degraded_digest(db_session):
    # Review Focus 4: prefilter order, status "degraded", no tiers, no scores,
    # no reasons -- and the items are still there.
async def test_a_prose_failure_keeps_tiers_and_scores(db_session): ...
async def test_an_empty_prefilter_writes_status_empty(db_session): ...
async def test_a_digest_is_never_partially_written(db_session):
    # a failure mid-build leaves no digest row at all
async def test_rebuilding_the_same_period_does_not_duplicate(db_session): ...
async def test_the_build_never_consumes_the_users_edit_allowance(db_session): ...
async def test_unbuildable_resume_facts_means_no_digest(db_session):
    # The fifth row of the spec's Failure modes table, and the only one with
    # no other home: facts that cannot be built produce NO digest at all,
    # and the person sees slice 3's resume-health card reason instead. A
    # digest built on absent facts would be scored against nothing.
    assert await build_for_user(db_session, user=u, period_start=p) is None
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, module not found.

- [ ] **Step 3: Implement the job**

One transaction per user so a failure mid-build writes nothing. `new_job_ids` for `classify` comes from the candidates whose `first_seen_at >= period_start`. Module docstring records the eligibility rule and why it exists: *a churned user stops costing money after two weeks with no separate churn model, and weekly spend tracks engagement rather than signups.*

- [ ] **Step 4: Close the `_exclusions` recent-digest seam**

`match_sweep._exclusions` was left open for "the applications and recent-digest exclusions". Neither track needs the digest half — `strong_fit` repeats deliberately and `new_this_week` is bounded by its window. Replace the seam's remaining promise with a comment saying the digest exclusion was considered and is not wanted, and why. **Slice 2 and 3's tests on that function must keep passing unchanged.**

- [ ] **Step 5: Run the tests, then the suite** — Expected: PASS; only the 2 known failures.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/jobs/build_digests.py apps/backend/app/jobs/match_sweep.py apps/backend/tests/test_build_digests.py apps/backend/tests/test_match_sweep.py
git commit -m "feat(jobs): build the weekly digest, and stop before the ceiling"
```

---

### Task 6: The timer

**Files:**
- Create: `~/.config/systemd/user/offerfy-digest.service`, `offerfy-digest.timer`, `offerfy-digest-failure.service` *(not in git)*
- Modify: `docs/deploy.md` *(gitignored — the authoritative runbook)*, `AGENTS.md`

**Interfaces:**
- Consumes: `app.jobs.build_digests:main` (Task 5).

A **user** unit, as `offerfy-ingest.timer` is, because agent sessions on this host have no passwordless sudo and a user timer plus `loginctl enable-linger` needs none. `Persistent=true`, `RandomizedDelaySec`, and an `OnFailure=` sibling writing to the journal, mirroring `offerfy-ingest-failure.service`. **Monday ~05:00 Asia/Taipei**, scheduled clear of the ingest slots so it reads a settled catalogue.

- [ ] **Step 1: Read the units this mirrors**

`systemctl --user cat offerfy-ingest.timer offerfy-ingest.service offerfy-ingest-failure.service`. Copy their structure rather than inventing one.

- [ ] **Step 2: Write the three units**

- [ ] **Step 3: Verify without waiting a week**

```bash
systemctl --user daemon-reload
systemctl --user enable --now offerfy-digest.timer
systemctl --user list-timers offerfy-digest.timer   # NEXT must be a Monday ~05:00
systemctl --user start offerfy-digest.service        # one run, now
journalctl --user -u offerfy-digest.service -n 40 --no-pager
```

Expected: the timer's next elapse is a Monday morning; the one-off run exits 0 and logs a `BuildReport`. Confirm `loginctl show-user ruby0322 -p Linger` is `yes`, or the timer dies with the session.

- [ ] **Step 4: Record it in both runbooks**

`docs/deploy.md` is gitignored and authoritative; `AGENTS.md` is in git and is what both sessions read. The units' contents go in `deploy.md` because they exist nowhere else — losing them means reconstructing them from memory.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs(ops): the weekly digest timer"
```

---

### Task 7: The digest on `GET /v1/dashboard`

**Files:**
- Create: `apps/backend/tests/test_dashboard_digest.py`
- Modify: `apps/backend/app/routers/dashboard.py`, `apps/backend/app/schemas_jobs.py`

**Interfaces:**
- Consumes: `latest_for_user`, `mark_opened`, `TRACKS` (Task 4).
- Produces: `DashboardOut` gains `digest: DigestOut | None`. `DigestOut` carries `status: str`, `built_at: str`, `opened_at: str | None`, `candidate_count: int`, and `items: list[DigestItemOut]`. `DigestItemOut` carries `job_id`, `job_title`, `company`, `apply_url`, `job_is_active`, `track`, `rank`, `tier: str | None`, `fit_score: float | None`, `reason: str | None`, `gap: str | None`.
- `digest` is `None` when the person has no digest at all — which is what makes the frontend's fallback to `MatchCountCard` a simple null check rather than a count of items.
- **Reuse `application_out`'s shape discipline**: the projection takes the row and reads `item.job` through an eager-loaded relationship, not a `db.get(Job)` per item. Task 4's `latest_for_user` eager-loads; a per-item query here would be an N+1 on the most-visited signed-in page.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_person_with_no_digest_gets_null_not_an_empty_object(client, db_session): ...
def test_the_digest_comes_back_with_both_tracks(client, db_session): ...
def test_a_degraded_digest_reports_its_status_and_null_tiers(client, db_session): ...
def test_items_are_ordered_by_track_then_rank(client, db_session): ...
def test_reading_the_dashboard_marks_the_digest_opened(client, db_session):
    # this is what the eligibility gate in Task 5 reads; without it nobody is
    # ever "engaged" and every user is skipped from week three onward
def test_marking_opened_is_the_only_write_a_dashboard_GET_makes(client, db_session):
    # slice 3 pinned "a GET writes nothing" with a before_cursor_execute
    # listener; this is the one deliberate exception, so narrow that test
    # rather than deleting it
def test_the_query_count_stays_flat_as_items_grow(client, db_session): ...
def test_another_users_digest_is_never_returned(client, db_session): ...
def test_digests_are_unreachable_from_the_employer_path(client, db_session):
    # sentinel reason/gap strings planted, then the talent endpoints walked
```

- [ ] **Step 2: Run them and watch them fail** — Expected: FAIL, `digest` absent from the response.

- [ ] **Step 3: Implement**

Composition only. **`mark_opened` is the one write a dashboard GET is allowed to make**, and slice 3's `test_a_get_writes_nothing` must be narrowed to say so explicitly rather than removed — it is what pins "a page visit spends nobody's credits".

- [ ] **Step 4: Run the file, then the suite** — Expected: PASS; only the 2 known failures.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/routers/dashboard.py apps/backend/app/schemas_jobs.py apps/backend/tests/test_dashboard_digest.py apps/backend/tests/test_dashboard_api.py
git commit -m "feat(web): serve the latest digest with the dashboard"
```

---

### Task 8: The digest card

**Files:**
- Create: `apps/frontend/lib/digest.ts`, `apps/frontend/components/dashboard/DigestCard.tsx`, `apps/frontend/lib/digest.test.ts`
- Modify: `apps/frontend/lib/dashboard.ts`, `apps/frontend/messages/{en,zh-TW,zh-CN}.json`

**Interfaces:**
- Consumes: Task 7's `DigestOut`; `Dashboard` (`lib/dashboard.ts`); `createTailor` with `job_id` (slice 3) for *Tailor it*.
- Produces:
  - `lib/digest.ts`: `DigestStatus`, `DigestTrack`, `Digest`, `DigestItem`, `TRACK_ORDER = ["strong_fit", "new_this_week"]` (the same two values in the same order as Task 4's backend constant, which is authoritative), and `groupByTrack(items): {track, items}[]` in `TRACK_ORDER`, **skipping empty tracks**.
  - `DigestCard.tsx` *(client)*: props `{ digest: Digest }`. Renders one labelled group per non-empty track, each row carrying tier **and** `fit_score`, the reason, the gap when present, and one action — *Tailor it*. A `degraded` digest renders its items with no tiers or scores **and a line saying it could not be ranked this week**.
- The card renders **nothing at all** when both tracks are empty, so the page's fallback is a null check rather than a second emptiness rule.

- [ ] **Step 1: Write the failing tests**

```ts
test("groupByTrack returns tracks in TRACK_ORDER and omits empty ones", ...)
test("a posting appearing in both tracks appears in both groups", ...)
test("an unknown track does not vanish from the grouping", ...)     // forward compatibility
test("groupByTrack keeps rank order inside a track", ...)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd apps/frontend && npx tsx --test lib/digest.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement the lib module and the card**

Follow `components/dashboard/ApplicationList.tsx` for the client-component shape and `components/jobs/JobActions.tsx` for its guest/loading/error discipline.

- [ ] **Step 4: Add the i18n keys**

Two track headings, the degraded line, the tier labels, the score label, *Tailor it*, and the gap label. **Re-check `git status -- apps/frontend/messages/` immediately before writing those files** — another session shares this tree. If any of the three is dirty, STOP and report rather than committing over uncommitted work. Keep the three at exact leaf-key parity and write real Traditional and Simplified Chinese in the register of the surrounding `dashboard` keys.

- [ ] **Step 5: Verify tests, types and parity**

`npx tsx --test lib/digest.test.ts` → PASS; `npx tsc --noEmit` → only the known `lib/admin-ingest.test.ts` error; a parity check over the three locale files → equal key sets.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/lib/digest.ts apps/frontend/lib/digest.test.ts apps/frontend/components/dashboard/DigestCard.tsx apps/frontend/lib/dashboard.ts apps/frontend/messages/en.json apps/frontend/messages/zh-TW.json apps/frontend/messages/zh-CN.json
git commit -m "feat(web): the digest card, two tracks and a score beside each tier"
```

---

### Task 9: Slot 2, the fallback, and the copy gate

**Files:**
- Modify: `apps/frontend/app/[locale]/dashboard/page.tsx`
- Test: `apps/frontend/components/dashboard/slotTwo.test.ts`

**Interfaces:**
- Consumes: `DigestCard` (Task 8); `MatchCountCard` (slice 3); `Dashboard.digest` (Task 7).
- Produces: slot 2 renders `DigestCard` when `digest` is non-null **and** has at least one item in either track; otherwise `MatchCountCard`, unchanged from slice 3.

- [ ] **Step 1: Write the failing test**

```ts
test("slot 2 shows the digest when it has items", ...)
test("slot 2 falls back to the match count when the digest is null", ...)
test("slot 2 falls back to the match count when both tracks are empty", ...)
// Review Focus 5: the common case for a thin market, not an edge case
test("a degraded digest with items still shows the digest, not the fallback", ...)
```

Use the `renderToStaticMarkup` + `NextIntlClientProvider` + `AppRouterContext` pattern already proven in `components/jobs/JobActions.test.ts`.

- [ ] **Step 2: Run it and watch it fail** — Expected: FAIL, slot 2 ignores `digest`.

- [ ] **Step 3: Implement the slot**

One conditional in `page.tsx`. Do not add a second emptiness rule — Task 8's card renders nothing when both tracks are empty, so this is a null-or-empty check in one place.

- [ ] **Step 4: Verify in all three locales**

`npx tsc --noEmit`, the parity check, then **an actual render** of `/en/dashboard`, `/zh-TW/dashboard` and `/zh-CN/dashboard` against a stub API, for four states: no digest, a full digest with both tracks, a digest with one empty track, and a degraded digest. A missing i18n key throws at render and a passing build proves nothing. Build from a **clean worktree**, never `/home/ruby0322/offerfy` — ports :3410 and :8110, checked against both `ss -ltnp` and `docker ps` first, and killed by PID from `ss -ltnp` afterwards (a `next start` child's whole command line is `next-server (vX.Y.Z)`, with no port in it).

- [ ] **Step 5: Enumerate the copy gate — do not draft it**

Twelve live strings across six surfaces say Offerfy does not rank or recommend postings, and slice 4 makes every one false. They must change **in the same release that ships this slice**:

| | Surface | Locales |
|---|---|---|
| `about.paragraphs[1]` | /about | en, zh-TW, zh-CN |
| `contact.paragraphs[2]` | /contact | en, zh-TW, zh-CN |
| `legal.terms.sections[1].paragraphs[3]` | Terms | en, zh-TW, zh-CN |
| `lib/agent-markdown.ts:105` | `/llms.txt` | — |
| `lib/agent-markdown.ts:145` | home agent markdown | — |
| `lib/agent-markdown.ts:150` | jobs agent markdown | — |

Re-run the sweep rather than trusting this table — the count was wrong four times by being copied instead of measured. The replacement is **a rule, not sentences**: *postings get scored and ordered; resumes get assessed in prose, never scored; nothing is submitted on your behalf.* The wording belongs to the session that owns marketing copy; **message them with this list and the rule, and do not write the strings.** `about.paragraphs[2]` (zh only) stays true only while *Strong fit* is separate from featured — if that ever changes it is fourteen strings.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/app/\[locale\]/dashboard/page.tsx apps/frontend/components/dashboard/slotTwo.test.ts
git commit -m "feat(web): the digest takes slot 2, and falls back when it is thin"
```
