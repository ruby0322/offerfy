# Onboarding First Run and Daily Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new account sees scored, explained matches inside its first session, and every posting that enters the catalogue is scored within about 30 minutes instead of by the following Monday.

**Architecture:** Scores move off the weekly digest run and onto the `(user, posting)` pair in a new `job_scores` table. A sweep on its own timer scores only what it has not scored, four times a day; the weekly digest reads that cache instead of calling the provider, and a user-initiated first run is the same work pointed at one person. The dashboard grows a scored-arrivals section above the surviving weekly digest card.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (`Mapped`/`mapped_column`) + Alembic, Postgres in production and SQLite in tests; Next.js 15 App Router + next-intl; `pytest` for the backend, `tsx --test` (node:test) for the frontend.

**Spec:** `docs/superpowers/specs/2026-11-19-onboarding-first-run-design.md` — read it first. Every ruling below argues from it, and two of its rulings reverse earlier decisions still written in the code.

## Global Constraints

- **Alembic head is `041_chat_messages_resume_index`.** Confirm it again before writing `042`; revisions collide across branches in this repo.
- **No test runs alembic.** `conftest` builds the schema from the models with `create_all`, so a migration is checked by eye against the models and against a throwaway Postgres. Never against the live database — the only live Postgres is production.
- **The suite runs `APP_ENV=development`, where `usage.limits_enabled()` is False.** Any test that needs enforcement sets `monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")` then `get_settings.cache_clear()`.
- **`status`, `track`, `tier` and `outcome` carry no CHECK constraint**, matching `031_job_preferences.py`, `032_applications.py` and `033_digests.py`. The vocabulary lives with its one writer.
- **`llm_calls.purpose` is CHECK-constrained to eight values.** Reuse `profile`, `match` and `digest`; do not add a purpose.
- **Never run prettier on this repo.** No config, hand-wrapped at ~90 columns; `--write` rewraps whole files and buries the change.
- **Frontend copy ships in all three locales** (`en`, `zh-TW`, `zh-CN`) in the same change. `lib/i18n-parity.test.ts` enforces key parity.
- **Design and verify the UI in zh-TW, not English.** CJK labels run ~1.5× wider and read heavier; fixed widths wrap and filled buttons crowd.
- **One shared spend ceiling, no reserve** (`global_weekly_ceiling_uusd`, $150/week). The sweep, the digest and first runs all draw on it, first-come-first-served.
- **Stage by file, never by directory.** This working tree carries unrelated modifications (`AGENTS.md`, `apps/backend/app/db.py`, `docker-compose.yml`, `k8s/base/configmap.yaml`, `ops/systemd/offerfy-api.service`, `apps/frontend/next-env.d.ts`) that are not part of this work. A directory pathspec sweeps a neighbour's half-finished change into your commit, and the later merge then deletes it with no conflict marker. Expand the `git add` lines below to the exact files you touched.

## Review Focus

Five things the spec implies that no task's own happy-path tests would exercise, most likely to bite first. Each has a test added to the task that owns the code.

1. **A posting goes inactive between being scored and being shown.** A dead role rendered as a live match wastes the one click the onboarding moment is for. `DigestItemOut` already carries `job_is_active`; arrivals must too. → Task 7.
2. **The first run for someone with no publishable resume.** `build_facts` returns an error rather than facts, and the endpoint must say so instead of 500ing or reporting an empty success. → Task 6.
3. **A sweep pass and a first run scoring the same user at once.** Both see zero rows and both buy; the write-back must upsert so the loser writes the same answer rather than raising on the unique constraint. → Task 2.
4. **A resume edited between the sweep's facts rebuild and the score write.** The hash recorded must be the rebuilt facts' hash, not the one read at the top of the pass, or the next pass sees its own work as stale and rescores forever. → Task 5.
5. **`matches_seen_at` ahead of now, from clock skew or a replayed request.** The arrivals window must clamp rather than return a negative or catalogue-wide count. → Task 7.

---

## File Structure

**Backend, created:**
- `app/services/jobs/score_cache.py` — read-through over `score.score_candidates`. The only module that knows the cache exists.
- `app/jobs/score_sweep.py` — the scheduled job. Audience, cap, ceiling, prune, `--dry-run`.
- `app/routers/matches.py` — the first run and its progress read.
- `alembic/versions/042_job_scores.py`
- `ops/systemd/offerfy-score-sweep.{service,timer}`, `ops/systemd/offerfy-score-sweep-failure.service`

**Backend, modified:**
- `app/models.py` — `JobScore`; `User.matches_seen_at`.
- `app/jobs/build_digests.py` — `_judge` reads the cache; `degraded` narrows.
- `app/jobs/match_sweep.py` — `_any_token` LIKE → `IN`.
- `app/routers/preferences.py` — `first_save` on the PUT response.
- `app/routers/dashboard.py` — server-side arrivals window; arrivals payload.
- `app/schemas_jobs.py` — `MatchArrivalOut`, `FirstRunOut`, `MatchProgressOut`, `first_save`.
- `app/main.py` — register the matches router.

**Frontend, created:**
- `components/dashboard/ArrivalsBlock.tsx`, `components/dashboard/arrivals.ts` (+ test)
- `components/dashboard/OnboardingCard.tsx`

**Frontend, modified:**
- `lib/dashboard.ts`, `lib/jobs.ts` — new types and calls.
- `lib/last-visit.ts` — deleted; its docstring asserts a decision this work reverses.
- `components/dashboard/MatchesBlock.tsx` — the two-column split is rebuilt.
- `app/[locale]/dashboard/page.tsx`, `components/dashboard/nextSteps.ts`
- `messages/{en,zh-TW,zh-CN}.json`

---

### Task 1: `job_scores` and the arrivals marker

**Files:**
- Modify: `app/models.py` (add `JobScore` after `DigestItem`; add one column to `User`)
- Create: `alembic/versions/042_job_scores.py`
- Test: `tests/test_job_scores_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `models.JobScore` with columns `id, user_id, job_id, facts_input_hash, description_hash, model, tier, fit_score, requirements_met, seniority_fit, location_fit, direction_fit, reason, gap, prose_model, outcome, scored_at`; unique `(user_id, job_id)` named `job_scores_user_id_job_id_key`; both FKs `ondelete="CASCADE"`; `tier`, `fit_score`, the four components, `reason`, `gap` and `prose_model` all nullable. `models.JobScore.OUTCOMES = ("scored", "refused", "prose_rejected")`. `models.User.matches_seen_at: Mapped[datetime | None]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_one_row_per_user_and_posting(db_session, ...):
    # second insert for the same pair raises IntegrityError
def test_a_refused_pair_stores_no_score(db_session, ...):
    # outcome="refused", tier is None, fit_score is None, and it commits
def test_deleting_the_user_removes_their_scores(db_session, ...):
def test_deleting_the_posting_removes_its_scores(db_session, ...):
def test_matches_seen_at_defaults_to_null(db_session, ...):
    # a fresh user has never looked; None is "first visit", not "no window"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_job_scores_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'JobScore'`

- [ ] **Step 3: Add `JobScore` to `app/models.py` and `matches_seen_at` to `User`**

Follow `Digest`'s declaration style exactly, including a class docstring that says why `tier` is nullable (a refusal is an answer, not a zero) and why the hashes are recorded rather than keyed on (§1 of the spec). Put `matches_seen_at` in `User`'s account-profile block with a comment that it replaces `lib/last-visit.ts`.

- [ ] **Step 4: Write `042_job_scores.py`**

`revision = "042_job_scores"`, `down_revision = "041_chat_messages_resume_index"`. Plain `create_table` plus `op.add_column("users", ...)`, no dialect guard, matching `033_digests.py`. Module docstring states that no test runs alembic and that the pair ships in one revision because a half-applied pair has no useful meaning.

- [ ] **Step 5: Run to verify they pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_job_scores_model.py -v && .venv/bin/python -m pytest -q`
Expected: PASS; suite green.

- [ ] **Step 6: Check the migration against the models by eye and against a throwaway Postgres**

Run `alembic upgrade head` on a scratch database only. Confirm column types, nullability, the unique constraint name and both cascades match the model.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/042_job_scores.py apps/backend/tests/test_job_scores_model.py
git commit -m "feat(matching): job_scores, one row per person and posting"
```

---

### Task 2: the read-through cache

**Files:**
- Create: `app/services/jobs/score_cache.py`
- Test: `tests/test_score_cache.py`

**Interfaces:**
- Consumes: `models.JobScore` (Task 1); `score.score_candidates(db, *, user, facts, jobs, concurrency) -> ScoreRun`; `score.Scored(job_id, tier, fit_score, components)`; `ScoreRun(scored, refused, error, usage, model)`.
- Produces:
  - `async cached_scores(db, *, user, facts, jobs, cap) -> ScoreRun` — fresh cache rows plus whatever it had to buy, in one `ScoreRun`.
  - `stale_pairs(db, *, user, facts, jobs) -> list[Job]` — the subset needing purchase; what the sweep and the progress read both count with.
  - `write_back(db, *, user, facts, job, scored=None, refused=False) -> JobScore` — upsert on `(user_id, job_id)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_fresh_cached_row_is_not_bought_again():
    # stub score_candidates to raise if called; the ScoreRun still carries the row
async def test_a_miss_is_bought_and_written_back():
async def test_a_refusal_is_recorded_and_not_retried():
    # outcome="refused" after pass one; pass two makes no provider call
async def test_a_changed_resume_hash_rescores_that_pair():
async def test_a_rewritten_posting_rescores_that_pair():
    # JobFacets.description_hash differs from the stored one
async def test_a_refusal_does_not_set_error():
    # score.py's own semantics: refused is an answer, error is a fault
async def test_a_provider_fault_sets_error_and_still_returns_cached_rows():
async def test_cap_bounds_purchases_not_the_returned_pool():
    # 40 candidates, 10 already cached, cap=5 -> 15 returned, 5 bought
async def test_a_concurrent_double_run_writes_one_row():      # Review Focus 3
    # write_back twice for the same pair: no IntegrityError, one row, same answer
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_score_cache.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `score_cache.py`**

Staleness compares `JobScore.facts_input_hash` against the **stored** `ResumeFacts.input_hash` and `JobScore.description_hash` against the posting's current `JobFacets.description_hash` — never against `resume_facts.input_hash(...)` recomputed from the live resume. The spec's §1 explains why the other reading is an infinite rescore loop; put that reason in the module docstring, because it is the one thing a reader will be tempted to "fix".

`error` is set only when a miss could not be bought. A refusal is not a fault.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_score_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/jobs/score_cache.py apps/backend/tests/test_score_cache.py
git commit -m "feat(matching): read-through score cache over the Decisions call"
```

---

### Task 3: the digest reads the cache, and `degraded` narrows

**Files:**
- Modify: `app/jobs/build_digests.py` (`_judge`, around the `score_candidates` call and the `run.error` branch)
- Test: `tests/test_build_digests.py` (extend)

**Interfaces:**
- Consumes: `score_cache.cached_scores` (Task 2).
- Produces: no new names. `Digest.model` may now hold the literal `"mixed"`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_digest_built_from_cached_scores_calls_no_provider():
async def test_one_unscored_miss_among_cached_scores_still_ranks():
    # status "full", tiers present -- NOT degraded
async def test_a_digest_with_no_scores_at_all_is_degraded():
    # the pre-existing meaning, preserved
async def test_model_is_mixed_when_cached_scores_disagree():
    # two rows, two models -> Digest.model == "mixed"
async def test_model_is_the_single_model_when_they_agree():
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build_digests.py -k "cached or mixed or unscored" -v`
Expected: FAIL — the second test degrades, the model tests see the first row's model.

- [ ] **Step 3: Swap the call and narrow the branch**

`score_candidates` → `cached_scores`. The degraded branch fires only when `not run.scored`; otherwise rank what is in hand. `Digest.model` is the single model when every shown item agrees and `"mixed"` when they do not — silently picking one would make the column a lie, which is the column's whole job not to be.

Keep the existing comment's intent but correct it: the old text says a half-scored pool ranks survivors against postings nothing judged, which was true when a fault meant nothing had been judged and is not true with a cache.

- [ ] **Step 4: Run to verify they pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_build_digests.py -v && .venv/bin/python -m pytest -q`
Expected: PASS; suite green. Any pre-existing digest test that asserted "degraded on any error" is expected to need updating — update it and say so in the commit body.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/jobs/build_digests.py apps/backend/tests/test_build_digests.py
git commit -m "feat(digest): rank cached scores; degraded now means nothing was scorable"
```

---

### Task 4: `_any_token` LIKE → `IN`

Its own comment names this slice as the trigger: *"revisit when a later slice runs the prefilter once per active user on a schedule."* The sweep runs it per eligible user, 28 times a week, against ~20k postings. Behaviour-preserving; do it before the sweep lands on it.

**Files:**
- Modify: `app/jobs/match_sweep.py` (`_any_token`, `_escape`)
- Test: `tests/test_match_sweep.py` (extend)

**Interfaces:** no change to `_any_token`'s signature or to `prefilter_query`.

- [ ] **Step 1: Write the failing tests**

Pin the equivalence on exactly the cases the LIKE escaping existed for:

```python
def test_a_token_does_not_match_a_longer_value():      # |tw| must not match "TWX"
def test_a_token_does_not_match_a_substring():          # |ml| must not match "html"
def test_an_underscore_in_a_region_is_not_a_wildcard(): # north_america vs northXamerica
def test_a_percent_in_a_token_is_not_a_wildcard():
def test_matching_is_case_insensitive():                # a stored "TW" matches "tw"
```

- [ ] **Step 2: Run to verify they pass on the current LIKE implementation**

Run: `.venv/bin/python -m pytest tests/test_match_sweep.py -k "token or wildcard or insensitive" -v`
Expected: PASS. This is the one place in this plan where the tests go in green on purpose — they are the equivalence harness for a refactor, so they must hold before and after. Do not proceed until they pass here.

- [ ] **Step 3: Replace the LIKE with `lower(column).in_(tokens)`**

Every column `_any_token` is called with (`JobFacets.function`, `.country`, `.region`, `.city`) holds a single token rather than a pipe list, which is what makes this equivalent. Drop `_escape` if nothing else uses it. Replace the deferred-optimisation comment with one sentence recording that it was taken and why it is equivalent.

- [ ] **Step 4: Run to verify they still pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_match_sweep.py -v && .venv/bin/python -m pytest -q`
Expected: PASS; suite green.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/jobs/match_sweep.py apps/backend/tests/test_match_sweep.py
git commit -m "perf(matching): the prefilter's token match is an IN, not a LIKE"
```

---

### Task 5: the sweep

**Files:**
- Create: `app/jobs/score_sweep.py`
- Create: `ops/systemd/offerfy-score-sweep.service`, `offerfy-score-sweep.timer`, `offerfy-score-sweep-failure.service`
- Test: `tests/test_score_sweep.py`

**Interfaces:**
- Consumes: `score_cache.cached_scores`, `stale_pairs` (Task 2); `match_sweep.prefilter_query`; `build_digests.is_eligible`, `saved_preference`; `resume_facts.facts_for_user`, `facts_are_stale`, `build_facts`; `reasons.write_reasons`; `digest.STRONG_TIERS`; `metering.system_guard`, `metered`; `usage.window_start`; `store.freshness_cutoff`.
- Produces: `SWEEP_CAP = 30`; `SweepReport(swept, skipped_not_engaged, skipped_no_preference, skipped_over_ceiling, pruned, bought)`; `async sweep_user(db, *, user, cap) -> int`; `async sweep_all(db, *, now=None, cap=None, dry_run=False) -> SweepReport`; `prune(db) -> int`; `main(argv)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_only_eligible_users_are_swept():            # reuses is_eligible unchanged
async def test_a_paused_preference_is_skipped():
async def test_a_user_with_no_preference_row_is_skipped():
async def test_the_cap_bounds_purchases_per_user_per_pass():
async def test_a_broad_preference_fills_in_over_several_passes():
async def test_stale_facts_are_rebuilt_before_scoring():
async def test_the_rebuilt_facts_hash_is_what_gets_recorded():   # Review Focus 4
    # edit the resume mid-pass; the row must carry the REBUILT hash, so the
    # next pass sees its own work as fresh and buys nothing
async def test_facts_are_not_rebuilt_when_there_is_nothing_to_judge():
async def test_prose_is_bought_for_strong_tiers_only():
async def test_prose_rejected_by_the_validator_is_recorded_and_not_rebought():
async def test_the_ceiling_stops_cleanly_and_lists_who_got_nothing():
async def test_a_user_who_starts_always_finishes():
async def test_longest_unswept_goes_first():
async def test_pruning_runs_outside_the_user_loop():
    # a user who has fallen out of is_eligible still has dead rows pruned
async def test_pruning_runs_on_a_pass_that_scores_nothing():
async def test_pruning_runs_when_the_ceiling_has_stopped_scoring():
async def test_dry_run_reports_the_pass_size_and_buys_nothing():
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_score_sweep.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `score_sweep.py`**

Lift `build_digests`' structure deliberately: `build_order`'s self-correcting sort (here, longest-unswept-first), the once-per-user ceiling check that never runs inside a user, the per-user `try/except` with a rollback so one person's database fault does not cost everyone else the pass, and `BuildReport`'s per-id lists including its deliberate absence of a `failed` list.

Purposes are `profile` (the facts rebuild), `match` (scoring) and `digest` (prose), all under `system_guard` — nobody is waiting on an HTTP response, so `preflight` would only make the job 429 itself.

`prune` is one global statement outside the loop and outside the ceiling check, and runs even on a pass that scores nothing. The spec's §1 says why: inside the loop it inherits an audience that abandons exactly the rows it exists to delete.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_score_sweep.py -v`
Expected: PASS

- [ ] **Step 5: Write the systemd units**

Copy `offerfy-digest.{service,timer}` and `offerfy-ingest-failure.service`. `OnCalendar=*-*-* 00,06,12,18:40:00 Asia/Taipei` — the ingest runs at `:10` with up to 10 minutes of randomised delay, so `:40` reads a catalogue that just settled. `Persistent=true`, `RandomizedDelaySec=5min`, `OnFailure=offerfy-score-sweep-failure.service`.

The service file must say, as `offerfy-digest.service` does, that **this job spends money against the production database** and that it ships disabled. It sends nothing; nothing is emailed.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/jobs/score_sweep.py apps/backend/tests/test_score_sweep.py \
        ops/systemd/offerfy-score-sweep.service \
        ops/systemd/offerfy-score-sweep.timer \
        ops/systemd/offerfy-score-sweep-failure.service
git commit -m "feat(matching): score new postings four times a day"
```

---

### Task 6: the first run

**Files:**
- Create: `app/routers/matches.py`
- Modify: `app/routers/preferences.py` (`put_job_preferences`), `app/schemas_jobs.py`, `app/main.py`
- Test: `tests/test_first_run.py`

**Interfaces:**
- Consumes: `score_cache`, `score_sweep.sweep_user` (Tasks 2, 5); `match_sweep.count_matches`; `preferences.current_or_seeded`.
- Produces: `POST /v1/me/matches/first-run` → `FirstRunOut(started: bool, reason: str | None)`; `GET /v1/me/matches/progress` → `MatchProgressOut(scored: int, expected: int, running: bool)`; `JobPreferenceOut.first_save: bool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_first_put_reports_first_save_and_later_ones_do_not():
async def test_the_first_run_scores_the_whole_eligible_pool():
async def test_the_first_run_is_refused_once_scores_exist():
    # started False, reason "already_scored"
async def test_progress_counts_rows_against_the_expected_pool():
async def test_progress_survives_a_reload():
    # a second GET from a fresh client sees the same count; no in-process state
async def test_a_user_with_no_publishable_resume_is_told_why():   # Review Focus 2
    # started False, reason "no_resume"; not a 500 and not an empty success
async def test_over_the_ceiling_it_says_paused_and_promises_no_time():
    # started False, reason "budget_paused" -- and the response does NOT claim
    # the sweep will arrive, because the sweep shares the ceiling that stopped it
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_first_run.py -v`
Expected: FAIL — route does not exist (404) and `first_save` is absent.

- [ ] **Step 3: Implement the router and the `first_save` flag**

`put_job_preferences` learns whether a row existed before it wrote and reports it; the write stays a pure write and starts nothing. The run is a detached task that writes `job_scores` rows incrementally; progress is `count(job_scores for user)` against `min(count_matches(pref), cap)`. No progress table and no stream — the rows are already durable and already the thing being produced, so a reload or a second tab reads the same count.

`system_guard`, not `preflight`: matching spend is the platform's, and billing a signup's scoring calls to a registered account's 20 weekly edit credits would spend their allowance on something they cannot see being spent.

- [ ] **Step 4: Run to verify they pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_first_run.py -v && .venv/bin/python -m pytest -q`
Expected: PASS; suite green.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/routers/matches.py apps/backend/app/routers/preferences.py apps/backend/app/schemas_jobs.py apps/backend/app/main.py apps/backend/tests/test_first_run.py
git commit -m "feat(matching): score a new account's matches while they are still here"
```

---

### Task 7: the dashboard API

**Files:**
- Modify: `app/routers/dashboard.py`, `app/schemas_jobs.py`
- Test: `tests/test_dashboard_arrivals.py`

**Interfaces:**
- Consumes: `models.User.matches_seen_at` (Task 1); `job_scores` rows.
- Produces: `MatchArrivalOut(job_id, job_title, company, apply_url, job_is_active, location, tier, fit_score, reason, gap, scored_at)`; `DashboardOut.arrivals: list[MatchArrivalOut]`, `DashboardOut.first_visit: bool`; `since` query parameter removed.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_dashboard_stamps_when_you_last_looked():
def test_arrivals_are_what_was_scored_since_that_stamp():
def test_a_first_visit_is_distinguishable_from_an_empty_window():
    # matches_seen_at NULL -> first_visit True, arrivals is everything scored
    # (a brand new account has 15 fresh matches and must not be shown nothing)
def test_a_marker_in_the_future_clamps_instead_of_going_negative():   # Review Focus 5
def test_an_inactive_posting_is_marked_not_dropped():                 # Review Focus 1
    # job_is_active False travels, as DigestItemOut already does it
def test_a_matching_posting_with_no_score_yet_is_present_without_one():
    # LEFT JOIN: tier and fit_score None, still listed
def test_the_client_since_parameter_is_gone():
    # two sources for one number is how they disagree
def test_the_digest_still_travels_and_is_still_stamped_opened():
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_arrivals.py -v`
Expected: FAIL — `arrivals` absent from the response.

- [ ] **Step 3: Implement the arrivals read and the stamp**

The GET already writes on read (`digest.opened_at`), so the precedent and its failure handling exist — follow it, including the "a bookkeeping stamp must not take down the page" rollback. Read the previous value *before* stamping the new one, the way the page's own `since.current` ref does, or the window is always empty.

- [ ] **Step 4: Run to verify they pass, then the whole suite**

Run: `.venv/bin/python -m pytest tests/test_dashboard_arrivals.py -v && .venv/bin/python -m pytest -q`
Expected: PASS; suite green.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/routers/dashboard.py apps/backend/app/schemas_jobs.py apps/backend/tests/test_dashboard_arrivals.py
git commit -m "feat(dashboard): scored arrivals, measured server-side"
```

---

### Task 8: arrivals on the page

**Files:**
- Create: `components/dashboard/arrivals.ts`, `components/dashboard/arrivals.test.ts`, `components/dashboard/ArrivalsBlock.tsx`
- Modify: `lib/dashboard.ts`, `components/dashboard/MatchesBlock.tsx`, `app/[locale]/dashboard/page.tsx`, `messages/{en,zh-TW,zh-CN}.json`
- Delete: `lib/last-visit.ts`

**Interfaces:**
- Consumes: `DashboardOut.arrivals`, `.first_visit` (Task 7).
- Produces: `arrivals.ts` exporting `groupArrivals(items)` and `arrivalHeadline(count, firstVisit)`.

- [ ] **Step 1: Write the failing tests** (`tsx --test`, node:test, as `nextSteps.test.ts` does)

```ts
test("a first visit is not greeted as a return")        // the "Welcome back." bug
test("no arrivals and not a first visit says nothing new")
test("an unscored arrival renders without a score, not as a poor match")
test("an inactive posting is marked rather than hidden")
test("arrivals group by the day they were scored")
test("the headline counts arrivals, not the removed new_match_count")
```

**The page header line changes with it, and will not typecheck otherwise.**
`app/[locale]/dashboard/page.tsx` builds its "since you last looked" sentence
from two clauses — `sinceNewMatches`, fed by `dash.new_match_count`, and
`sinceStrongFits`, fed by `strongFitCount(dash.digest?.items)`. Task 7 removes
`new_match_count` along with the client `since` parameter, so the first clause
loses its source and must read the arrivals count instead. The second clause
still works but now duplicates what the arrivals block says a few lines below
it; fold it in rather than printing the same fact twice.

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/frontend && node_modules/.bin/tsx --test components/dashboard/arrivals.test.ts`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `arrivals.ts`, then `ArrivalsBlock.tsx`, then wire the page**

`ArrivalsBlock` sits above `MatchesBlock`. `MatchesBlock`'s two-column split goes — the sweep erases the "scored on the left, not scored on the right" distinction those columns drew — and the weekly digest card stays below arrivals as its own surface, which is Ruby's call in §5. Delete `lib/last-visit.ts` and its callers; its docstring argues for a decision this work reverses, so leaving it is worse than leaving nothing.

New copy goes into all three locales in this commit. A **"still scoring"** string is needed and does not exist: today's "not scored" means "nothing will ever score these", and the new state means "not yet".

- [ ] **Step 4: Run to verify they pass, plus typecheck, lint and every frontend test**

```bash
cd apps/frontend && node_modules/.bin/tsc --noEmit -p . && node_modules/.bin/eslint components/dashboard app/\[locale\]/dashboard lib
for f in $(find components lib -name "*.test.ts"); do node_modules/.bin/tsx --test "$f" || break; done
```
Expected: PASS throughout, `i18n-parity` included. Lint only the touched paths; a bare `npm run lint` reports ~98k pre-existing problems from the `.next.bak-*` directories.

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/components/dashboard apps/frontend/lib/dashboard.ts apps/frontend/app apps/frontend/messages
git rm apps/frontend/lib/last-visit.ts
git commit -m "feat(dashboard): scored arrivals lead, the weekly picks sit below"
```

---

### Task 9: the onboarding card

**Files:**
- Create: `components/dashboard/OnboardingCard.tsx`
- Modify: `components/dashboard/nextSteps.ts` (+ its test), `app/[locale]/dashboard/page.tsx`, `messages/{en,zh-TW,zh-CN}.json`

**Interfaces:**
- Consumes: `JobPreferenceOut` (the seed), `first_save`, `POST /v1/me/matches/first-run`, `GET /v1/me/matches/progress`.
- Produces: no exported helpers beyond the component.

- [ ] **Step 1: Write the failing test**

```ts
test("the preferences step yields to the card once a seed exists")
// nextSteps currently pushes kind "preferences" whenever hasPreference is
// false; with a seeded card on the page that step is the card, not a link
```

- [ ] **Step 2: Run to verify it fails**

Run: `node_modules/.bin/tsx --test components/dashboard/nextSteps.test.ts`
Expected: FAIL

- [ ] **Step 3: Build the card**

Three questions, **pre-ticked from the seed**, Save then first run with progress read from `GET /v1/me/matches/progress`. The full editor stays at `/preferences`. The empty state shows one greyed sample pick — tier, score, reason, labelled an example — so a new account can see the shape before it has anything.

**Which three questions is yours to choose**, and the spec says so deliberately
(`What is left to the builder`). The constraint that decides it: the three must
be the axes `prefilter_query` actually filters on — function, location,
seniority — because a question that changes no filter is a question that
changes no result. `remote_required` is a fourth axis and is deliberately not
one of the three: it makes `target_locations` inert, which is a surprise worth
explaining in the full editor rather than a radio button in an onboarding card.

- [ ] **Step 4: Run to verify it passes, plus typecheck and lint**

Run: the Task 8 Step 4 block.
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/components/dashboard apps/frontend/app apps/frontend/messages
git commit -m "feat(dashboard): a seeded onboarding card, not an empty checkbox wall"
```

---

### Task 10: the strings gate and zh-TW verification

The parent spec shipped a twelve-string audit as a release gate on the principle that strings describing the old behaviour change *in the same release*. Same gate here. This task is where the plan stops trusting its own list.

**Files:** `messages/{en,zh-TW,zh-CN}.json`, plus whatever the grep finds.

- [ ] **Step 1: Re-grep rather than trusting the list**

At minimum these are falsified, per §6 of the spec: `dashboard.sinceFirstVisit` ("Welcome back." to a first-time reader), `dashboard.picksEmpty`, `dashboard.picksScoredOn`, `dashboard.matchesNewThisWeek`, `dashboard.matchEmpty`. The parent spec found twelve where it expected fewer, and marketing negatives in particular go stale as features ship. Grep for "week", "weekly", "每週", "本週" across `messages/` and the marketing copy.

- [ ] **Step 2: Fix every string found, in all three locales**

- [ ] **Step 3: Verify the dashboard in zh-TW, in a browser**

Headless Chrome over CDP against a stub API. Check the onboarding card, the arrivals block and the empty state at phone width. CJK runs ~1.5× wider: watch for wrapped fixed-width labels and crowded filled buttons. Known traps in this repo: the `tailors` field and the sessionStorage tab.

- [ ] **Step 4: Run the whole backend suite and every frontend test once more**

- [ ] **Step 5: Commit**

```bash
git commit -m "fix(i18n): retire the weekly-cadence copy this release falsifies"
```

---

## Deploy

Not a task — it needs a person at a keyboard, and `docs/deploy.md` (gitignored, on the host) is the runbook.

Order, from `AGENTS.md` §3, for a release carrying a migration plus both halves:

```
alembic upgrade head        # hard rule 11 -- before any reload, never after
npm run build:public        # never a bare next build
kill -HUP  offerfy-api      # API first: the new frontend calls routes the old API lacks
ops/rolling-restart-web.sh
smoke
```

Then, before enabling anything:

- [ ] **The sweep ships disabled.** `systemctl --user disable --now offerfy-score-sweep.timer` until the next two boxes are ticked. With one shared ceiling and no reserve, a cold-start pass is the whole week's exposure.
- [ ] **Run `python -m app.jobs.score_sweep --dry-run` by hand and read the pass size.** This number is the reason the gate exists. Enable the timer only after it is known and judged affordable.
- [ ] **Watch `skipped_over_ceiling`** on the first few real passes; it is the signal that the sweep is eating the digest's budget.
- [ ] **Delete `.next`'s prerendered `/en` and roll web twice**, routinely — otherwise the old process revalidates from its stale in-memory entry and keeps serving a stale landing page.
- [ ] **Verify the API reload by the listener** (`ss -ltnpe`), not by worker count: SIGHUP leaves a stale worker and the count looks wrong while the listener tells the truth.
