---
name: Usage policy (three tiers, cost-metered)
overview: Three weekly usage tiers — guest 4 counted edits, signed-in 20 credits, matching-opted-in 100 credits — metered on what model calls actually bill. Usage is counted in its own usage_counters table rather than derived from LlmCall on read, because LlmCall's writes are best-effort by design and a budget cannot read from one. The work is attribution (LlmCall has no owner column, and both ways of reaching one through resume_id are quota resets), enforcement (pre-flight per turn, finish what you start, debt into the window), and four transparency surfaces plus a public page. Guests cannot already publish, so that requirement is copy only. One migration, which also adds the cache_write_tokens column production showed is missing. The Terms change, which bumps POLICY_VERSION. Calibrated against production on 2026-10-24 — reference edit $0.07, cache hit 82.6%, and two things nobody had priced, namely cache writes at 1.25x input and 63% of every round's input being unbounded chat history.
todos:
  - id: t0-calibrate
    content: "Task 0: Measure the real per-turn cost off production and confirm the price table"
    status: completed
  - id: t1-attribution
    content: "Task 1: Migration 014 — owner columns on llm_calls, backfilled and indexed"
    status: completed
  - id: t2-usage-service
    content: "Task 2: app/services/usage.py — prices, cost, window, tier, balance"
    status: completed
  - id: t3-enforce
    content: "Task 3: Enforce in the chat router: pre-flight, structured refusal, quota SSE event, burst cap for all tiers"
    status: completed
  - id: t4-usage-endpoint
    content: "Task 4: GET /v1/usage, for guests and users alike"
    status: completed
  - id: t12-metering-seam
    content: "Task 12: metering.py — the metered() context manager, usage_counters, every billable path wrapped, inventory test"
    status: completed
  - id: t13-grants
    content: "Task 13: entitlement grants, so one person can be given more without a deploy (BACKLOG — spawned as its own task 2026-10-24)"
    status: pending
  - id: t14-refusals
    content: "Task 14: record refusals, so the tiers can be tuned from data"
    status: completed
  - id: t5-meter-ui
    content: "Task 5: The live meter in the editor, the refusal card, and the settings panel"
    status: completed
  - id: t6-public-page
    content: "Task 6: The public /usage page in three locales, sitemap and footer"
    status: completed
  - id: t7-legal
    content: "Task 7: Terms and privacy policy, and the POLICY_VERSION bump that comes with them"
    status: completed
  - id: t8-guest-key
    content: "Task 8: The guest network key, its retention sweep, and the guest ceiling"
    status: pending
  - id: t9-admin
    content: "Task 9: Cost and ceilings on the admin dashboard"
    status: completed
  - id: t10-tests
    content: "Task 10: Tests — cost arithmetic, attribution holes, tier transitions, overrun, refusal shape"
    status: in_progress
  - id: t11-verify
    content: "Task 11: pytest, lint, tsc, build, a real browser walk through all three tiers, then deploy"
    status: completed
isProject: false
---

# Usage Policy Implementation Plan

> **For agentic workers:** implement task-by-task. Task 0 is **done** — its
> measured constants are in Task 2 and in the spec; do not re-derive them.
> Tasks 1–4 are backend and land together. 5–6
> are frontend. 7 is copy and legal, and bumping `LEGAL_UPDATED` without
> `POLICY_VERSION` will fail `tests/test_consent.py` by design. 10 must pass
> before 11.

**Goal:** A person can see, at any moment, how much AI editing they have left this
week, understand why there is a limit at all, and get more of it by signing in or
by opting into matching — and Offerfy's weekly model spend has a known ceiling.

**Architecture:** No new ledger. `LlmCall` already holds one row per model round
with its token counts, written on every billable path, and
`talent_me.REFRESH_LIMIT_PER_HOUR` already meters off it. This plan adds owner
columns to that table (because the only existing route to an owner is a join
through `resume_id`, which resume deletion nulls and `/v1/auth/claim`
reassigns), a pure `app/services/usage.py` that turns rows into money and money
into a tier balance, one pre-flight check in the chat router, one additive SSE
event, one read endpoint, and copy.

**Tech Stack:** FastAPI + SQLAlchemy 2 + Alembic, Next 16 / React 19 /
next-intl, SSE.

**Spec:** [docs/superpowers/specs/2026-10-24-usage-policy-design.md](../specs/2026-10-24-usage-policy-design.md)

## Global Constraints

- **`usage_counters` is the quota's source of truth; `LlmCall` is telemetry.**
  Reversed from "no new ledger" on 2026-10-24 — `_persist_usage` is
  best-effort by design, so a quota reading from it means a lost row is a free
  turn. `LlmCall` keeps its exact current shape and its try/except. Only
  `usage_counters` is committed and must not be swallowed. If a task starts
  reading the quota back off `LlmCall`, it has drifted.
- **One wrap per billable path, and an inventory test that notices new ones.**
  The context manager was chosen over a required meter parameter knowing it
  gives up the compile-time guarantee (see the spec's rejected list). The
  inventory test in Task 12 is what replaces that guarantee — if a task adds a
  provider call site, that test must fail, and editing the test to make it pass
  without wrapping the call is the one shortcut that defeats the whole design.
- **Additive transport only.** Every existing `ChatStreamEvent` shape is
  untouched. A client that ignores the new `quota` event behaves exactly as it
  does today.
- **The `quota` event is not the `usage` event.** `chat.py` relays no telemetry
  to clients, deliberately, and that stays true: `quota` carries tier, remaining,
  limit and reset time, and **never** tokens, model names or dollars.
- **A turn that starts, finishes.** No budget check between rounds, ever.
  Abandoning the repair loop leaves a resume that does not compile.
- **Integer µUSD throughout.** No floats in the ledger or the comparison. Floats
  are for the display layer only.
- **Telemetry may be lost; enforcement may not.** `_persist_usage` is wrapped in
  try/except on purpose. The *attribution* columns ride inside that same
  insert and inherit it — but the pre-flight check is not telemetry and must not
  be swallowed.
- **`anonymous stays whole` and `AI stays optional` still hold.** Guests keep the
  editor, the preview, the export and the ATS report, unmetered. The limit is on
  the model.
- **Quota writes nothing to the consent ledger.** Tier is derived from
  `has_consent` at request time.
- **No clawback.** Turning matching off changes the tier at the next window, never
  retroactively.
- Backend checks: `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q`
- Frontend checks: `cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build:public`
- Locales are `en`, `zh-TW`, `zh-CN`. Every string in Tasks 5–7 needs all three.
- Product name is Offerfy.
- Migrations have no test coverage in this repo and the only live Postgres is
  production — verify Task 1 against a throwaway database, both directions.

## Findings this plan is built on

Carried from the spec; each was read out of the code. The full table, with the
reasoning, is in [the spec](../specs/2026-10-24-usage-policy-design.md#what-already-exists-do-not-rebuild-it).

| # | Finding | Consequence for this plan |
| --- | --- | --- |
| 1 | `LlmCall` already records per-round `input_tokens`, `cached_tokens`, `output_tokens`, `reasoning_tokens`, `model`, `purpose` | No new table. Task 2 reads this one. |
| 2 | `talent_me.py:37` already meters a limit off `LlmCall` rows, with the reason written down | Task 2 follows an existing pattern rather than inventing one. |
| 3 | `publish` and `share` already take `require_user` | Requirement 1.1's publish clause is **copy only** (Task 6). No backend work. |
| 4 | `talent_introduction` is the only self-serve consent | Tier 3's predicate is `has_consent(..., purpose="talent_introduction")`. |
| 7 | A turn is up to 9 rounds and repair can add 3 more passes — but **production uses 2–3** (`round_index` histogram `0→7, 1→7, 2→4`), so the "fills its budget" reading is retracted | A turn count still cannot bound spend — measured median $0.045 against a complex-turn $0.23–$0.34 and a worst case near $1. The pre-flight must assume a turn may cost ~20x the median. |
| 11 | **Cache writes are billed at 1.25x input and `LlmCall` does not record them** — `_usage_counts` stops at four fields; the provider returns a fifth | 20–45% of the bill is invisible. Task 1 adds the column, Task 2 prices it. |
| 12 | **`_history()` re-sends every message ever written on a resume, every round, unbounded** — 63% of a measured round's input | The largest cost lever in the product, and out of scope. See Not in scope. |
| 8 | `purpose='chat' AND round_index=0` is exactly one row per chat turn | The guest turn *count* needs no new column. |
| 9 | No model call in the compile/preview/export/ATS path | "Editing and exporting are free at every tier" is literally true (Task 6). |
| 10 | The Terms publish "10 chat turns and 20 PDF exports per hour" | Task 7 is mandatory, not optional polish. |
| — | `LlmCall.resume_id` is `ON DELETE SET NULL`, and `/v1/auth/claim` rewrites `resume.user_id` | **Two quota resets.** Task 1 exists because of this. |

---

## Execution order

Task numbers are stable references, not the order of work. 12 has to land
before 5, because the editor meter should read a number nothing can bypass or
race — and 12 changes where that number comes from.

| | | why here |
| --- | --- | --- |
| 0–4, 10 | **done** | calibration, migration 014, `usage.py`, chat enforcement, `GET /v1/usage`, 28 tests |
| **12** | **next** | `usage_counters` replaces derive-on-read. Everything downstream reads it. |
| 13 | after 12 | grants need somewhere to add to, and that is the counter |
| 14 | after 12 | refusals are recorded where refusals are decided |
| 5 | after 12–14 | the meter UI reads the final shape once, not twice |
| 6, 7 | after 5 | the public page and the Terms quote numbers the UI already shows |
| 8 | any time after 12 | independent; the guest pool ceiling reads the counter |
| 9 | after 12 | admin reads counters and reconciles them against `LlmCall` |
| 10 | continuous | each task brings its own |
| 11 | last | lint, build, browser walk, deploy |

---

## Task 0: Calibrate against production — DONE 2026-10-24

Ran read-only aggregates over `llm_calls` and confirmed the price table against
the provider. Full numbers, decomposition and the five estimate errors are in
[the spec](../specs/2026-10-24-usage-policy-design.md#what-one-edit-actually-costs--measured-2026-10-24).

- [x] **Prices, from [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing).**
      terra: input $2.00/1M, cached read $0.20/1M, **cache write (30m) $2.50/1M**,
      output **$12.00/1M**. luna (gpt-6): $0.10 / $0.01 / $0.125 / $0.50. Cached
      reads are 10% of input, cache writes 1.25x. Terra's input and output both
      fell 20% on 2026-07-30 — treat any $2.50/$15.00 figure as stale.
- [x] **Measured cost.** Median turn **$0.045**, p90 $0.121, reference edit
      **$0.07** → `CREDIT_UUSD = 70_000`. Per round $0.020–$0.038.
- [x] **Cache hit rate 82.6%** overall, 88–99% in a warm session.
- [x] **Zero unmeasurable rows** — every row carried a full usage block, so no
      proxy is dropping it today. The `FALLBACK_ROUND_UUSD` path in Task 2 is
      still required (the base URL is configurable), but it is not currently
      load-bearing.
- [x] **Rounds per turn: avg 2.57, max 3**, against a ceiling of 9. The
      `round_index` histogram is `0 → 7, 1 → 7, 2 → 4` and stops.
- [x] **Backfill is fully recoverable today**: 20 of 20 rows still join to a
      resume with a `user_id`, 0 orphans. Task 1's backfill will be complete if
      it runs soon.

**Two findings that change the work:**

- [x] **Cache writes are billed and `LlmCall` does not record them.** The
      provider returns `cache_write_tokens`; `_usage_counts` does not read it.
      At 1.25x the uncached input rate this is 20–45% of the real bill. **Task 1
      adds the column and Task 2 prices it.**
- [x] **63% of every round's input is `_history()`** re-sending every message
      ever written on the resume, unbounded. The single largest cost lever in the
      product, and out of scope here — see Not in scope.

**Caveat, and it is a real one.** The sample is **one user, 7 turns, one resume,
three days** — the instance holds 3 users, 111 guest sessions, 246 resumes. The
*structure* generalises (cache hit rate, token mix, output size, where the input
comes from — all properties of the prompt architecture). The *volume* does not:
rounds per turn, the repair rate and the source size are n=7 and will move.

**Still open:**

- [ ] Re-read after a fortnight of real traffic. Expect rounds/turn, repair rate
      and source size to move; expect the credit peg not to, since it is a
      function of the token mix.
- [ ] Re-peg `CREDIT_UUSD` once `cache_write_tokens` has collected a week.

---

## Task 1: Migration 014 — owner columns on `llm_calls` — DONE 2026-10-24

Without this, two documented actions reset a cost quota: deleting a resume
(`resume_id` is `ON DELETE SET NULL`) and signing in (`/v1/auth/claim` sets
`resume.user_id`, so a guest's spent rounds retroactively bill the new account).

- [ ] Add to `LlmCall` in `app/models.py`, mirroring how `DisclosureEvent`
      copies `recipient_label` in at write time rather than reading it through
      `api_key_id`:
      - `user_id: str | None` → `users.id`, `ondelete="SET NULL"`, indexed
      - `guest_session_id: str | None` → `guest_sessions.id`, `ondelete="SET NULL"`, indexed
      - Nullable, and **not** an XOR constraint: a row whose subject has been
        erased has neither, exactly as `ConsentEvent` documents for its own two
        subject columns. Only both set at once is nonsense — constrain that.
- [ ] **Add `cache_write_tokens: int | None` to `LlmCall` in the same migration**,
      and teach `_usage_counts` to read it from `input_tokens_details`
      (`cache_write_tokens`, with `cache_write_tokens_details.ttl_30m_tokens`
      available beside it — store the total, note the TTL split is the 1.25x vs
      2.5x price difference and take the 30m rate until a 12h write is observed).
      Nullable like its siblings, for the same stated reason: a proxy may not
      report it. **This is 20–45% of the bill and is the one term nobody has
      measured** — a meter shipped without it undercounts by that much.
- [ ] Docstring on each column saying *why* it is denormalised, naming both
      holes. The next reader will otherwise try to "simplify" it back into a join.
- [ ] `alembic/versions/014_llm_call_owner.py`:
      - add both columns, both FKs, both indexes
      - composite indexes `(user_id, created_at)` and `(guest_session_id, created_at)` —
        the balance query filters on both columns together on every chat turn
      - backfill from the resume join where it is still available:
        `UPDATE llm_calls SET user_id = r.user_id, guest_session_id = r.guest_session_id FROM resumes r WHERE r.id = llm_calls.resume_id`.
        Imperfect for anything already claimed or deleted — note that in the
        migration, and do not try to reconstruct it.
      - a working `downgrade()`
- [ ] **The model and the migration must produce the same object.** `ensure_schema()`
      builds from the models in production, so a named-constraint mismatch leaves
      a later `drop_constraint` finding nothing — this repo has already been bitten
      by exactly that (see `TalentProfile.__table_args__`). Name every constraint
      and index explicitly in both places.
- [ ] Verify up **and** down against a throwaway Postgres, not the model
      metadata. Migrations have no test coverage here.

**Verified 2026-10-24** against a throwaway `postgres:16-alpine` container (never
the live `offerfy-postgres-1` on :5433, and no compose volume):

- `upgrade head` → `downgrade -1` → `upgrade head` round-trips with rows intact.
- The backfill is correct on seeded pre-014 data: a user-owned round gets
  `user_id`, a guest-owned one gets `guest_session_id`, and an orphan (null
  `resume_id`) gets neither, as documented.
- `llm_call_subject_not_both` rejects a row with both subjects set.
- **`\d llm_calls` is byte-identical between a migrated database and an
  `ensure_schema()` one** — but only after a fix this check caught: the two FKs
  were unnamed in the model, so Postgres auto-named them
  `llm_calls_user_id_fkey` while the migration wrote `fk_llm_calls_user_id`.
  That is exactly the trap `TalentProfile.__table_args__` documents, and a
  later `drop_constraint` would have found nothing on any never-migrated
  database. Both are now named in both places.
- **The SQLite branch of the backfill was removed rather than tested.** The
  chain cannot run on SQLite at all: `012_talent_profiles` calls
  `drop_constraint` outside batch mode, which the dialect refuses. Production
  is Postgres and tests build from the models, so a SQLite branch would have
  been untestable code implying a path that does not exist.

---

## Task 2: `app/services/usage.py` — DONE 2026-10-24

Pure and sessionless where it can be, so the arithmetic is testable without a
database. The spec's section on the price table is the reference; its two
subtleties are the whole correctness risk in this task.

- [ ] **Prices**, µUSD per 1M tokens, integers, per model — **verified against
      the provider on 2026-10-24**, so copy them rather than re-deriving:

      ```python
      PRICES = {  # µUSD per 1M tokens; developers.openai.com/api/docs/pricing
          "gpt-5.6-terra": {"input": 2_000_000, "cached": 200_000,
                            "cache_write": 2_500_000, "output": 12_000_000},
          "gpt-6-luna":    {"input":   100_000, "cached":  10_000,
                            "cache_write":   125_000, "output":    500_000},
      }
      ```
      Unknown model → the most expensive known model, never free. A model we do
      not recognise must not be a free tier. `cache_write` is the 30-minute rate
      (1.25x input); the 12-hour rate is 2.5x, and the usage block distinguishes
      them under `cache_write_tokens_details`.
- [ ] `round_cost_uusd(row) -> tuple[int, bool]` returning the cost and whether
      it was measured. Two rules, each of which is a double-charge if it is got
      wrong:
      - `cached_tokens` is a **subset** of `input_tokens`. Bill
        `(input − cached)` at the input rate **plus** `cached` at the cached
        rate. Clamp the subtraction at zero in case a proxy reports nonsense.
      - `reasoning_tokens` is a **subset** of `output_tokens`. Bill `output`
        only. Never add reasoning on top.
      - `cache_write_tokens` is its **own** charge at 1.25x input, on top of the
        read. It is *not* a subset of anything already billed — unlike the two
        above, this one you do add. Measured at 20–45% of a turn.
- [ ] `input_tokens IS NULL` → charge `FALLBACK_ROUND_UUSD` (the measured mean
      round cost from Task 0) and return `measured=False`. **Not zero**: a proxy
      that drops the usage block would otherwise make the whole product free,
      and `LlmCall`'s own docstring already says knowing the spend is
      unmeasurable is itself the measurement.
- [ ] `window_start(now) -> datetime` — most recent Monday 00:00 Asia/Taipei, as
      UTC. Use `zoneinfo`; do not hand-roll an offset, Taipei has no DST today but
      a fixed `+08:00` is a lie waiting to become true elsewhere.
- [ ] `tier_of(db, user, guest) -> Tier` — `guest` / `registered` / `matching`,
      the last via `has_consent(db, user=user, purpose="talent_introduction")`.
      Read through `app.services.consent`, never by querying `ConsentEvent`.
- [ ] `balance(db, user, guest) -> Balance` with `tier`, `limit_uusd`,
      `used_uusd`, `remaining_uusd`, `turns_used`, `turn_limit`, `reset_at`,
      `unmeasured_rounds`. One query per subject over the window, summing by the
      new owner column. For guests, `turns_used` is
      `COUNT(*) WHERE purpose='chat' AND round_index=0` (Finding 8) — no new
      column needed.
- [ ] `TIERS` table: guest = turn count 4 (cost not enforced, still recorded);
      registered = 20 credits; matching = 100 credits.
      **`CREDIT_UUSD = 70_000`** ($0.07), measured in Task 0.
      Every number a named constant with a one-line comment on where it came
      from — these will be tuned, and an unexplained constant gets tuned blind.
- [ ] `limits_enabled()` — defaults to `app_env == "production"`, overridable by
      an explicit setting so staging can exercise it. Mirror
      `rate_limit.guest_rate_limits_enabled()` rather than replacing it.
- [ ] Global ceilings: `global_week_spend(db)` and `guest_week_spend(db)`, read
      against `GLOBAL_WEEKLY_CEILING_UUSD` and `GUEST_WEEKLY_CEILING_UUSD`.

**Done when:** every function above has a unit test that needs no HTTP, including
one that proves a cached-heavy turn costs less than an uncached one with the same
total tokens, and one that proves a null-usage row is not free.

---

## Task 3: Enforce in the chat router — DONE 2026-10-24

- [ ] **Attribution on write.** `_persist_usage` takes the owner and sets
      `user_id` / `guest_session_id` on the `LlmCall` row. The chat handler
      already holds both from `owner_context`. Thread them in; do not re-derive
      them from the resume.
- [ ] Same for `talent_me._record_spend` (`purpose='profile'`), and switch
      `_recent_builds` off its `join(Resume, ...)` onto the new column — the join
      is one of the two holes.
- [ ] **Pre-flight, once, before the turn starts**, after `load_resume_for_owner`
      and before anything is persisted. Guests check the turn count; signed-in
      tiers check `remaining_uusd > 0`. Then the global ceiling, then the guest
      ceiling. Not wrapped in the telemetry try/except — this one must be able to
      fail the request.
- [ ] **Structured refusal**, 429, body carrying `limit` (which one), `tier`,
      `used`, `limit_value`, `reset_at`, and `next_tier` with what it would give.
      A bare `detail: "Too many requests"` wastes the signup moment for a guest
      and the matching moment for a signed-in user. Guest-ceiling refusals say
      *sign in to keep editing*, not *try later* — degrade into the conversion
      path, not into an error.
- [ ] **The turn then runs to completion regardless of what it costs.** No check
      inside the round loop, none in the repair loop. The overrun becomes debt and
      the *next* pre-flight refuses.
- [ ] **New `quota` SSE event at turn end**, carrying tier, remaining, limit,
      reset time — and nothing else. Emit it from the same place `done` is
      emitted, after the last `_persist_usage` has committed, so the number the
      client receives is the number in the database.
      **Do not relay the existing `usage` event.** The comment at
      [chat.py:239](../../../apps/backend/app/routers/chat.py) is still correct
      and these are two different things with two different audiences.
- [ ] **Burst cap for every tier.** In `rate_limit.py`, drop the guest-only
      condition at the `chat` call site and raise `LIMITS["chat"]` to 15 for
      signed-in tiers, 4 for guests. `RateEvent.guest_session_id` is `nullable=False`,
      so this needs either a user column there or a separate counting path —
      prefer counting the same `LlmCall` turn rows over an hour window, which
      needs no schema change at all (Finding 8) and retires half of
      `rate_events`. Leave `export` exactly as it is: it is CPU, not tokens
      (Finding 9).

**Done when:** a guest is refused on turn 5 with a signup-shaped refusal; a
signed-in user with a negative balance is refused with a matching-shaped one;
both get a `quota` event on every successful turn; and no existing SSE event
shape changed.

---

## Task 4: `GET /v1/usage` — DONE 2026-10-24

- [ ] `/v1/usage`, **not** `/v1/me/usage`: it has to answer for guests, so it
      takes `owner_context(..., ensure=False)` rather than `require_user`. Every
      `/v1/me/*` route in this codebase means "signed in"; do not break that.
- [ ] Returns the `Balance` fields plus a per-day breakdown over the window, so
      the settings panel can show where the week went without a second endpoint.
- [ ] Credits, not µUSD, in the response — plus `approx_edits_left`, computed
      from the caller's own trailing mean turn cost over the last 30 days, falling
      back to `CREDIT_UUSD` before they have history. This is the number that
      makes the meter legible; see the spec on why it is self-calibrating.
- [ ] No owner, `ensure=False`, no cookie → answer for the guest tier with
      `used=0` rather than 401. A first-time visitor asking what they get is a
      reasonable question.
- [ ] Add to `lib/api.ts` with the type.

---

## Task 12: `metering.py` — the seam, the counter, and the inventory test — DONE 2026-10-24

**Do this before Task 5.** It changes where the number comes from, and the
editor meter should read the final shape once.

Three provider call sites exist, not one — [llm.py:626](../../../apps/backend/app/services/llm.py)
`client.stream` (chat/repair), [layout.py:784](../../../apps/backend/app/services/layout.py)
`client.post` (vision), [profile.py:430](../../../apps/backend/app/services/profile.py)
`client.post` (profile) — each building its own client and each leaving the
recording to whoever called it. One of those callers already forgets.

- [ ] **`usage_counters`**, migration 015:
      `(subject_kind, subject_id, window_start)` primary key, plus `uusd`,
      `turns`, `unmeasured`, `updated_at`. `subject_kind` is `'user'`,
      `'guest'` or **`'network'`**; no FK, because a counter has to survive an
      erased subject, is pruned on its own schedule, and `'network'` has no
      table to point at.
- [ ] **`'network'` is what Task 8 needs**, so that task gets no table of its
      own: the guest IP HMAC becomes a third subject, `balance()` is unchanged,
      the min-of-two rule reads two counter rows, and the 14-day retention
      sweep is the counter's own pruning rather than a second schedule.
- [ ] **The weekly budget comes from counters; the hourly burst guard keeps
      counting `LlmCall` rows.** A per-week counter cannot answer "turns in the
      last hour", and burst is an abuse guard rather than an allowance — one
      indexed COUNT, where best-effort is the right durability. Say so in the
      module, or someone will try to unify them.
- [ ] Incremented with one atomic
      `INSERT .. ON CONFLICT (subject_kind, subject_id, window_start) DO UPDATE
      SET uusd = usage_counters.uusd + :d .. RETURNING uusd`. The `RETURNING`
      is what makes the decision race-free; `enforce_chat_usage` today
      reads-then-decides with no lock.
- [ ] **The meter holds its own short-lived Session** and commits per record,
      immediately after the provider responds. Deliberately *not* the request's
      Session: a failed increment must not roll back the chat messages and
      draft source the router has already written on it — the existing
      `_persist_usage` already has to rollback carefully for exactly this
      reason. It is also more durable for metering, because a turn that dies
      halfway has still recorded the rounds it paid for.
- [ ] `metered(db, *, user, guest, purpose)` — async context manager. Enforces
      on entry (raising the structured 429 that `enforce_chat_usage` already
      builds), exposes `.record(usage_event)`.
- [ ] `system_metered(db, *, user, reason)` — records, does **not** enforce.
      For [refresh_profiles.py:44](../../../apps/backend/app/jobs/refresh_profiles.py),
      which must not 429 itself. `reason` is required and free text, so an
      exemption is a named thing a reviewer can see rather than a silent
      omission. **Not** an `enforce=False` flag on `metered()`: a boolean that
      turns the limit off is indistinguishable from forgetting to wrap.
- [ ] **Wrap all four call sites**, and keep `.record()` **per round** — the
      whole cost model is built on per-round token mix (26k input, 82.6% cache
      hit, 271 output), so a single record for a whole turn loses the thing
      being measured. In the chat router that means `.record()` stays inside
      the event loop where `_persist_usage` is called today, not around the
      turn.
      - `chat.py` — replaces the bare `enforce_chat_usage` call and
        `_persist_usage`
      - `talent_me.py` ×2 — replaces `_record_spend` and folds
        `REFRESH_LIMIT_PER_HOUR` into tier config, so "too much" stops having
        three definitions
      - `refresh_profiles.py` ×1 — **this is the live bug**: it spends real
        money and records nothing
- [ ] `balance()` reads `usage_counters` instead of aggregating `LlmCall`.
      Drops one pre-flight from 9–11 queries to 2–3, and the global ceiling
      from an instance-wide scan to a SUM over roughly one row per active
      subject.
- [ ] **`LlmCall` is untouched** — same columns, same best-effort try/except,
      still written per round, still what admin and cost analysis read.
- [ ] **The inventory test.** This is what replaces the compile-time guarantee
      the context manager gives up, so it is not optional. Enumerate every call
      site of `aiter_chat_edit`, `build_profile`, `vision_report` and
      `read_layout_report` across `app/`, compare against an expected set
      committed in the test, and fail with *"new billable call site — wrap it
      in metered() and add it here"*. Cheap to write and it fires on exactly
      the mistake that is live in the tree right now.
- [ ] **Reconciliation:** a query comparing `usage_counters.uusd` against
      priced `LlmCall` sums per subject per window. Divergence means either
      lost telemetry or a path that billed without metering — surfaced in
      Task 9.

**Done when:** `refresh_profiles` records its spend; the inventory test fails if
a call site is added without a wrap; a pre-flight is 2–3 queries; and two
concurrent turns on a one-credit balance cannot both proceed.

**Verified 2026-10-24.** 480 passed, 1 pre-existing environmental failure.

- Migration 015 round-trips on a throwaway Postgres, and `\d usage_counters`
  and `\d llm_calls` are both **identical between a migrated database and an
  `ensure_schema()` one** — the check that caught the FK-name drift in Task 1.
- The UPSERT increments: two inserts of 100 leave one row at 200. Both check
  constraints reject (`alien` subject kind, negative `uusd`).
- **The inventory test was verified by making it fail**: a temporary
  `app/routers/_tmp_probe.py` calling `build_profile` unwrapped tripped both
  guards, with the message naming the file and telling the reader to wrap it.
  A guard that has never fired is not a guard.
- `_record_spend` moved out of `routers/talent_me.py` into
  `services/profile.record_build_spend`, because the cron is one of its two
  callers and a job has no business importing from a router.

**One behaviour change worth knowing.** The old `REFRESH_LIMIT_PER_HOUR` ran
unconditionally; the tier limit it became respects `USAGE_LIMITS_ENABLED` like
every other limit. **Production is unchanged** — still 5 builds an hour, still
enforced — but development no longer enforces it, so
`test_refresh_is_rate_limited` now opts in and a sibling test asserts the
development half.

---

## Task 13: Entitlement grants — BACKLOG (spawned 2026-10-24)

What makes the tiers *manageable*. Today there is no way to give one person
more quota — not for a support case, not for a beta user, not for testing on
production — short of a deploy or a manual `UPDATE`.

- [ ] `entitlement_grants`: subject, `extra_credits`, `reason`, `granted_by`,
      `expires_at`, `created_at`. Append-only.
- [ ] Read by `balance()` as an **addition** to the derived tier, never a
      replacement. Tier assignment stays derived live from auth plus
      `has_consent` — a stored tier is a second source of truth that can
      disagree with the consent ledger, and serving a withdrawn person to an
      employer is the one failure here that cannot be taken back.
- [ ] Admin endpoint to grant, with `reason` required. Surfaced in Task 9.
- [ ] Tier *limits* (4 / 20 / 100) stay Python constants in `usage.TIERS` —
      and on this host that is also the cheapest thing to change: a pull plus
      `kill -HUP` is zero downtime, where an env var needs a full API restart
      and a frontend constant needs a ~40s web deploy. Task 6's page reads them
      through the API for exactly this reason.
- [ ] **No `user_tier` table.** Decided 2026-10-24. Tier assignment stays
      derived from auth plus `has_consent`, because a stored copy is a second
      source of truth that can disagree with the consent ledger, and serving a
      withdrawn person to an employer is the one failure here that cannot be
      taken back. If this is ever revisited, the minimum safeguard is updating
      the tier row inside the same transaction as the consent write, plus a
      reconciliation test asserting the two never diverge — which is one more
      place that has to remember, the same class of bug as the
      `refresh_profiles.py` hole this plan exists partly to close.

---

## Task 14: Record refusals — DONE 2026-10-24

- [ ] One row per refusal: subject, tier, which limit, when. Append-only.
- [ ] Nothing is written today when someone is turned away, so *"how often do
      guests hit the wall, and do they then sign in or leave?"* is unanswerable
      except by scraping logs — and that is the number the tier sizes should be
      tuned from, rather than from the estimates in the spec.
- [ ] Refusal rate by tier on the admin dashboard (Task 9).

**Done 2026-10-24.** 489 backend tests pass (1 pre-existing environmental
failure). Migration 016 round-trips on a throwaway Postgres, both check
constraints bite, and `\d` is identical between a migrated database and an
`ensure_schema()` one for all three new tables.

- Recorded from the single point where the 429 is built, so all five limits are
  covered by construction.
- **On its own session, for a sharper reason than the meter's**: raising an
  `HTTPException` makes `get_db` roll the request session back, so a row
  written on it would be discarded exactly when it is most wanted. A test
  drives a real refused request over HTTP and asserts the row survives.
- Wrapped in try/except — a lost data point is acceptable, a 500 in place of a
  429 is not, and a test asserts that.
- `refusals_by()` for tuning and for Task 9; `prune_refusals()` at the 90 days
  the privacy policy now states.

**Two corrections to what this plan previously said.**

- **I was wrong that the guest wall → sign-up funnel needs no new field.**
  `/v1/auth/claim` nulls `resumes.guest_session_id` and records consent against
  the user alone, so **there is no stored link from a guest session to the
  account it became**. Rebuilding one would mean adding personal-data linkage
  for an analytics question. Instead the question is answered in aggregate —
  guest refusals per day against claims per day, which `consent_events` already
  timestamps with `source='claim'`. Enough to size a tier, and it adds no
  tracking.
- **`prune_refusals` is not scheduled by anything.** The policy's 90 days is
  currently a promise with no mechanism. It needs a systemd *user* timer the
  way the catalog ingest has one (AGENTS.md section 8) — host work, so left for
  Task 11 or Task 8, and called out in the function's docstring rather than
  left implied.

---

## Task 5: The live meter in the editor, the refusal card, and the settings panel — DONE 2026-10-24

- [ ] **Meter in the chat panel**, near the composer: a bar, "N of M credits this
      week" (or "N of 4 edits" for guests), "≈ K edits left", "resets Monday".
      Fetch on mount from Task 4, update from the `quota` SSE event — no
      polling.
- [ ] **One new `ChatStreamEvent` variant** in the discriminated union, handled
      in `EditorShell.tsx` beside the existing `error` branch. An unhandled
      variant is inert by the established contract, so this cannot break an
      older client.
- [ ] **The refusal is a card, not a toast.** It is the most important thing on
      screen at that moment. Guest → what signing in gives, and the Google button.
      Signed-in → what matching gives, and a link to settings, worded as an offer
      and never as a wall. Both → when it resets, and a link to `/usage`.
- [ ] **Low-balance state** at ≤20% remaining: the bar changes, nothing is
      blocked and nothing is interrupted. The wall must never be the first
      mention of the limit — a meter that only appears when it is empty is a
      surprise, and requirement 4 was transparency.
- [ ] **Settings: a usage card** — the week by day, the tier, the reset, a link
      to `/usage`. Place it near the matching toggle but **not inside it**: the
      toggle's copy leads with matching and mentions the bonus second, and
      nesting a credit meter inside the consent control inverts that.
- [ ] Three locales for every string.

**Done 2026-10-24.** `tsc --noEmit` and `eslint` both clean.

- `lib/api.ts`: a `quota` variant on `ChatStreamEvent`, the `Usage` and
  `UsageRefusal` types, `fetchUsage()`, and `usageRefusalOf(err)` — which digs
  the structured refusal out of an `ApiError`, since `errorMessage` has already
  flattened the human sentence onto `err.message` and loses which limit fired.
- `components/editor/UsageMeter.tsx` and `UsageLimitCard.tsx`, rendered by
  `EditorChatPanel` through one grouped `usage` prop rather than nine label
  props — the shell owns the copy, as it does for every other label there.
- `EditorShell`: fetches once on mount, updates from the `quota` frame (no
  polling), and on a 429 sets the refusal *and* the balance that caused it, so
  the meter lands on empty without a second request.
- `components/UsageCard.tsx` on settings, placed near the matching toggle but
  **not inside it**: that toggle leads with what matching is and mentions the
  quota second, and nesting a credit meter inside a consent control inverts it.
- 23 new message keys across `en` / `zh-TW` / `zh-CN`, with a parity assertion.

Two things worth knowing:

- **`eslint` caught a real bug**: the label `useMemo` had landed after the
  component's early returns, breaking the hook order. Moved above them.
- **`npm run build:public` cannot run in a worktree.** Turbopack rejects a
  symlinked `node_modules` ("points out of the filesystem root"), and the
  symlink is *not* gitignored — `node_modules/` only matches real directories,
  so `git add -A` would commit it. `tsc` and `eslint` do work through the
  symlink; remove it afterwards. The build is a merge-time step in the main
  checkout regardless, which is where AGENTS.md §3 builds.
- The cards link to `/usage`, which Task 6 creates. Nothing deploys before
  Task 11, so the link is never live while broken — but a browser walk before
  Task 6 will 404 on it.

---

## Task 6: The public `/usage` page in three locales, sitemap and footer — DONE 2026-10-24

Build on the `LegalDocument` / `TrustDocument` pattern — same `landing-page`
shell, `Nav`, `legal-article`, `Footer`, `JsonLd`, `pageMetadata`. The section
order is in the spec and the order **is** the argument; keep it.

- [ ] `app/[locale]/usage/page.tsx` plus a component. Sections, in order: what's
      free and unlimited everywhere → why the AI costs money → the tier table →
      what makes an edit cost more → how we count → matching is a thank-you, not
      a toll → reset time and where to watch the meter.
- [ ] **The tier table is fetched from the API at runtime, not written into
      `messages/*.json`.** A published allowance that drifts from the enforced
      one is the worst possible bug on this page, and fetching makes the drift
      structurally impossible rather than merely tested for.

      It is also the cheaper thing to operate, which is the part that is
      specific to this host. Per [AGENTS.md §4](../../../AGENTS.md), the API's
      `kill -HUP` reloads **code** with no downtime but never the environment,
      while a frontend change needs `npm run build:public` and a web restart
      (~40s of downtime). So tuning 4 / 20 / 100 after Task 14 gives real
      refusal rates costs a pull plus a HUP — **as long as the numbers live in
      `usage.TIERS` and the page reads them**. Hard-code them in the message
      files and every tier tweak becomes a frontend deploy.
- [ ] That needs one shape `/v1/usage` does not yet return: all three tiers,
      not just the caller's. Either extend it or add `GET /v1/usage/tiers`
      (uncached, public, no subject).
- [ ] This supersedes the "pin it with a test" approach. Keep a test that the
      endpoint's numbers equal `usage.TIERS` — that is cheap — but the page
      itself must not hold a copy.
- [ ] State plainly that guests cannot publish — already true (Finding 3) — and
      that hand-editing, preview, PDF export and ATS checks are unlimited at every
      tier, which is true because nothing in those paths calls a model
      (Finding 9). Do not write either sentence more softly than the code
      supports, and do not write it more strongly.
- [ ] `/usage` into `app/sitemap.ts`'s `staticPaths` and into `Footer.tsx`
      beside Terms and Privacy.
- [ ] Three locales.

**Done 2026-10-24.** 483 backend tests pass (1 pre-existing environmental
failure); `tsc` and `eslint` clean; **rendered and checked in all three
locales** against a real backend.

- `GET /v1/usage/tiers` — public, subject-free, no database. A crawler, a
  signed-out visitor and a signed-in one get the same table; asking does not
  mint a guest session. `tests/test_usage.py` pins it to `usage.TIERS` and
  asserts it does not vary by caller.
- `lib/usage-tiers.ts` fetches it **server-side**, so the numbers are in the
  HTML — verified: `4 edits` / `about 20 edits` / `about 100 edits` all appear
  in the served markup in each locale, which a client fetch would not give a
  crawler.
- If the endpoint is unreachable the page says so rather than printing a
  number. The table is the one thing on this page worth refusing to guess at.
- `/usage` in `app/sitemap.ts` and in `Footer.tsx`. The sitemap emits one
  `<loc>` with hreflang alternates for all three locales, matching the existing
  pages.

Two things worth recording:

- **The zh-CN copy had to be written by hand.** Deriving it from zh-TW by
  string replacement produced a mix of both scripts (`我們`, `為什麼`, `沒有`,
  `當`, `時`…) and left an English word stranded in the zh-TW text. No
  converter is installed — no `opencc`, `zhconv` or `hanziconv` on either
  interpreter. Both locales are now written out, with a check for
  Traditional-only characters in the zh-CN blob. **Do not machine-derive one
  from the other in Task 7.**
- **The table cell said "20 credits — roughly 20 edits"**, which is one fact
  twice: a credit *is* a reference edit by the peg. It now says "about 20
  edits", because that is the unit a job-seeker has intuition for, and the
  credit is explained in the "how we count" section instead.

---

## Task 7: Terms and privacy policy — DONE 2026-10-24 (no bump needed)

- [ ] Replace the Terms' AI-section sentence — *"Guest use is rate-limited to 10
      chat turns and 20 PDF exports per hour"* — with the new tiers, in all three
      locales, and link `/usage` for the detail.
- [ ] Add a line to the Terms about the matching bonus being a thank-you that can
      be withdrawn at any time with no loss of saved work. This is the public half
      of the consent constraint; the spec explains why it earns its place in the
      Terms rather than only on the usage page.
- [ ] If Task 8 ships, add the guest network key to the privacy policy's cookie
      and data table — what it is (a truncated keyed hash, never the address), why
      (abuse control on the free tier), how long (14 days), and that no signed-in
      path reads it.
- [ ] Bump `LEGAL_UPDATED` in `components/legal/LegalDocument.tsx` **and**
      `POLICY_VERSION` in `app/services/consent.py` to the same date.
      `tests/test_consent.py` fails on drift by design. This is correct: the
      policy genuinely changed, and every consent row from here on records the
      new version.
- [ ] Check `lib/agent-markdown.ts` and `app/llms.txt` for any limit claim that
      this change falsifies. Neither states one today — confirm that is still
      true rather than assuming it.

**Done 2026-10-24. No version bump was needed, and that is worth explaining.**

`LEGAL_UPDATED` and `POLICY_VERSION` already both read `2026-10-24`, which *is*
today — the previous policy change landed earlier the same day. The date is
therefore already correct, `tests/test_consent.py`'s equality check passes
untouched, and bumping would have meant writing a date that is not the one the
policy changed on.

**But the structural gap is real: a date-based version cannot distinguish two
changes made on the same day.** Production holds two `consent_events` rows
stamped `2026-10-24` (one grant, one withdrawal, both from the owner's own
testing that morning), so today nobody's real consent is misattributed. The
first time a user consents in the morning and the policy changes in the
afternoon, the ledger will say they agreed to text they never saw. The cheap
fix, if it matters: make the version a date plus a revision counter and keep
displaying only the date. **Not done — it is a decision about the consent
ledger, not a copy change.**

What changed:

- The Terms' AI section: the stale "10 chat turns and 20 PDF exports per hour"
  sentence replaced by three paragraphs — the weekly allowance and what is
  never limited; matching as a thank-you that can be withdrawn with no
  clawback and is explicitly *not* required to use the product; and the hourly
  and site-wide caps.
- The privacy policy gained two paragraphs: what the meter stores (token
  counts, model, cost, attached to an account or guest session) and that
  refusals are recorded with the tier and which limit — plus, in Retention,
  90 days for both and window-length for the counters. Written to cover **both
  this task and Task 14 under one policy version**, which is why Task 14 must
  land before any deploy.
- `LegalDocument` gained a see-also link to `/usage`, because the Terms now
  describe an allowance whose numbers live there and the component renders
  paragraphs as plain text with no inline link parsing.
- All three locales, with paragraph counts asserted equal.

---

## Task 8: The guest network key, its retention sweep, and the guest ceiling

The guest tier is per *cookie*, and clearing a cookie is free. This is the one
unbounded number in the design. It can ship separately from Tasks 1–7, but it
should not ship much later.

- [ ] A coarse second key beside the cookie: `HMAC-SHA256(ip, secret)`,
      truncated. **HMAC and not a bare SHA-256**, unlike
      `GuestSession.key_hash` — the input there is 256 bits of
      `secrets.token_urlsafe` and unguessable, whereas the IPv4 space is
      enumerable in an afternoon. A dedicated secret, **not** `auth_token_secret`.
- [ ] Remaining for a guest is the **minimum** of the cookie's remaining and the
      network key's, with the network allowance set higher (start at 12 edits /
      week) so NAT, a campus or a café does not lock out honest visitors. Watch
      the refusal rate after it ships and raise it if it bites.
- [ ] **Retention: 14 days, then deleted** — the window plus a little. Add it to
      the existing `offerfy-ingest` systemd *user* timer's pass, or a sibling
      timer installed the same way (see `AGENTS.md` §8 — user unit, needs
      lingering, no sudo). A rate key that outlives the window it limits has no
      purpose.
- [ ] Rotating the secret expires every key by construction. Write that down as
      the intended emergency reset.
- [ ] `GUEST_WEEKLY_CEILING_UUSD` — when guest spend for the week crosses it, new
      guests get *sign in to keep editing*, not a refusal.
- [ ] Privacy policy entry (Task 7).

---

## Task 9: Cost and ceilings on the admin dashboard — DONE 2026-10-24

- [ ] Add cost to the existing `AdminDayPoint` series: `llm_cost_uusd`, and the
      same split by `purpose`. The token sums and the cache hit rate are already
      there; this is a derived column over them, computed with
      `usage.round_cost_uusd` so admin and enforcement can never disagree.
- [ ] Split spend by tier, so the three-tier shape is visible and the ratios can
      be tuned from data rather than from this document.
- [ ] Top spenders this window, with their tier. The whole point is to see one
      bad actor before the invoice does.
- [ ] **Both ceilings and the current spend against them, on the overview.** A
      ceiling nobody is told about is an outage with no explanation.
- [ ] Surface `unmeasured_rounds` as a share. If it climbs, the meter is
      guessing and nobody would otherwise know.
- [ ] **The reconciliation from Task 12**: `usage_counters` against priced
      `LlmCall` sums. A persistent gap means lost telemetry or a path billing
      without metering, and it is the only thing that catches a missed wrap the
      inventory test cannot see.
- [ ] **Refusal rate by tier** (Task 14). The number the tier sizes should be
      tuned from.
- [ ] ~~Grant a quota entitlement~~ — Task 13 is in the backlog, so the page
      leaves room for it rather than stubbing it.

**Done 2026-10-24.** 557 backend tests pass (1 pre-existing environmental
failure); `tsc` and `eslint` clean apart from main's own `admin-ingest.test.ts`
error; the endpoints and the Next `/api` rewrite exercised against a seeded
database.

`GET /v1/admin/usage` plus a `/admin/usage` page: spend against both ceilings,
top spenders, the split by tier and by purpose, refusals by tier and limit, and
the counter-vs-telemetry reconciliation. Everything read off `usage_counters`
and priced through `services.usage.round_cost_uusd`, so no figure on the page
can disagree with the one someone was limited by.

**Per-user tracking, which was the explicit ask**, in two places: the user list
grows tier / credits / edits for the window plus an `order=usage` that puts the
biggest spenders first, and the detail page shows this week against their tier
alongside an all-time total. The ordering is an **OUTER** join — a user who
spent nothing has no counter row, and an inner join would have made "sort by
usage" quietly also mean "hide everyone who was idle". There is a test for
exactly that.

**A real bug, found by running the page against seeded data rather than by any
test.** `price_uusd` truncates integer micro-USD at the end, so pricing a
per-model SQL aggregate truncated once where the meter truncated once per
round — up to one µUSD per round. Invisible in a cost report and fatal in a
reconciliation: **every correctly metered subject showed up as a gap.**
`llm_call_cost` now sums per row, the same way the counter accumulated, with a
regression test. A reconciliation that cries wolf is worse than none, and this
is the second time this work has been saved by actually running the thing
rather than trusting a green suite.

---

## Task 10: Tests

- [ ] **Cost arithmetic**, unit, no HTTP: cached tokens are a subset not a
      sibling; reasoning is a subset not a sibling; a null usage block is charged
      the fallback and flagged unmeasured; an unknown model is charged at the
      most expensive known rate.
- [ ] **Both attribution holes, as regression tests.** These are the reason
      Task 1 exists and they are the easiest things here to silently undo:
      - delete a resume, assert the spend still counts against its owner
      - spend as a guest, `POST /v1/auth/claim`, assert the new account starts
        the week at zero and the guest's spend did **not** follow it
- [ ] **Tier transitions:** granting `talent_introduction` raises the limit;
      withdrawing it does **not** claw back the current window; a `withdrawn` row
      in the same transaction as a `granted` one resolves to withdrawn, because
      that is what `_LATEST_FIRST` already guarantees and the quota must not
      quietly disagree with the ledger.
- [ ] **Overrun:** a turn allowed to start with a sliver of balance runs every
      round to completion and leaves the balance negative; the next turn is
      refused; the window reset clears it.
- [ ] **Window:** Monday 00:00 Asia/Taipei, including a Sunday-night /
      Monday-morning pair either side of the boundary in UTC.
- [ ] **Refusal shape:** 429 body carries `reset_at` and `next_tier` for every
      tier, and the guest-ceiling refusal is conversion-shaped.
- [ ] **The published numbers equal the enforced ones** — the Task 6 pin.
- [ ] **`quota` event fires on success and carries no token counts, no model
      name and no dollars.** Assert the *absence*, or the comment at
      `chat.py:239` quietly stops being true.
- [ ] Existing tests: `test_rate_limit.py` changes shape in Task 3, and
      `test_llm_telemetry.py` asserts on `LlmCall` rows that now carry two more
      columns. Update, do not delete.

---

## Task 11: Verify, then deploy — DONE 2026-10-24

- [x] `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q` —
      **469 passed, 1 failed** as of 2026-10-24. The failure is
      `test_chat_template_switch_includes_package_example`, which is
      environmental: it needs the Typst Universe package cache, and the
      worktree has 27 cached files against the main checkout's 1,198. It passes
      in `/home/ruby0322/offerfy/apps/backend`. Note the worktree has no
      `.venv` or `.tools` of its own — run with the main checkout's:
      `PATH=/home/ruby0322/offerfy/apps/backend/.tools:$PATH /home/ruby0322/offerfy/apps/backend/.venv/bin/python -m pytest -q`
- [ ] `cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build:public`
      — **never a bare `next build`**, per `docs/deploy.md` rule 10.
- [ ] **A real browser walk through all three tiers**, because lint and build are
      the only automated gates on UI here:
      - guest: burn 4 edits, see the meter fall, hit the signup card
      - sign in, see the balance reset and the limit rise
      - turn on matching, see it rise again; turn it off, confirm nothing is
        clawed back
      - read `/usage` in all three locales, at phone width
- [ ] **Merge `main` first — this branch is 15 commits behind**, and those
      commits touch 15 of the files this work changes, including `chat.py`,
      `lib/api.ts`, `EditorChatPanel.tsx`, `EditorShell.tsx` and all three
      message files. `git merge-tree` reports no textual conflict between the
      two *committed* trees, but this work is still uncommitted, so that proves
      nothing yet. Known semantic overlaps to check by hand: both branches add
      a variant to `ChatStreamEvent` (`context` on main, `quota` here), both
      add imports to `chat.py`, and both add keys to the editor message
      namespace.
- [ ] **Then re-run Task 0 and re-peg `CREDIT_UUSD`.** `4db08d1` cut ~13,700
      tokens of history out of a ~25,900-token round, so the reference edit is
      nearer $0.045 than the $0.07 these tiers were pegged to. Safe direction
      — people get more than advertised — but the public page's "about N edits"
      should be right before it is published.
- [ ] Migrate production, forwards only, with a plan to roll back. `ensure_schema()`
      builds from the models, so deploying the code before the migration is the
      ordering that works — confirm against `docs/deploy.md` before doing it.
- [ ] Deploy per `AGENTS.md` §3–§5: merge to `main`, push over SSH, hardlink the
      `.next` backup, `npm run build:public`, check the `/api` rewrite is `:8001`,
      restart (`kill -TERM` the web unit — ~40s of downtime, say so out loud
      first; `kill -HUP` the API), then the full smoke including
      `/api/healthz` and the `redirect_uri` check.
- [ ] After a day, read the admin dashboard: measured per-turn cost against Task
      0's number, refusal rate by tier, guest network-key refusals, spend against
      both ceilings. **Tune the constants from that, not from this document.**

### Deploy record, 2026-10-24

Live at 20:42 Taipei. Two restarts of `offerfy-web`, 7s and 4s of downtime --
well under the ~40s AGENTS.md warns about. `offerfy-api` took SIGHUP with none.

**Production was on a migration this branch had never seen.** `alembic_version`
read `016_workday_source`; this branch's head was `016_usage_refusals`. Both
chains descended from `013`, so alembic had two heads and `upgrade head` would
have refused -- after the merge and the push, before the build. The merge could
not have caught it: different files, colliding revision numbers, no textual
conflict. Renumbered to 017/018/019 onto main's head, then rehearsed the exact
production path on a throwaway Postgres: fresh database to `016_workday_source`,
the three new ones up, all three down, up again, and `\d` identical against an
`ensure_schema()` database for all three tables.

Migrations ran **before** the restart, deliberately: they are additive -- three
columns and two tables -- and the old code ignores them, so production served
normally throughout. The backfill caught all 23 existing `llm_calls` rows with
zero orphans. `usage_counters` starts empty by design.

**The smoke check caught a silent data loss.** The live Terms page still
published "Guest use is rate-limited to 10 chat turns and 20 PDF exports per
hour", which this work had just made false. The merge's key-level resolution of
the three message files carried every key that was *new* on either side -- but
the Terms and privacy changes were modified values inside arrays that already
existed in the merge base, so they were dropped in favour of main's version and
nothing complained. Lost in all three locales: three Terms paragraphs, one
privacy paragraph in "What we collect", one in "Retention". Restored, rebuilt,
redeployed, re-smoked.

The resolution script asserted that none of *main's* keys were lost and never
asserted the same for its own side. **An asymmetric check on a symmetric
operation reads as thorough and is not** -- and only a grep against the live
site found it, which is the second time this work was saved by running the thing
rather than trusting a green suite.

Verified live: all three locales of `/usage`, `/terms` and `/privacy` carrying
the new copy; `/usage` in the sitemap and the footer; `GET /v1/usage` reporting
`enforced: true`, `0/4` edits for a guest and a Monday reset; the admin usage
endpoint 401ing without a session; zero errors in either unit's journal.

**Open, in priority order:**

1. **Re-peg `CREDIT_UUSD`.** The history trim is now live, so a round costs
   ~16,400 input tokens rather than ~25,900 and the reference edit is nearer
   $0.045 than the $0.07 the tiers are pegged to. Safe direction -- a credit
   buys more than advertised -- but the public page understates by ~35%. Re-run
   the Task 0 queries after a few days of real traffic and re-peg.
2. **Task 8**, the guest network key. Guests are capped per cookie and a cookie
   is free to clear, which is the one unbounded term in the whole design.
3. **`prune_refusals` is not scheduled.** The privacy policy now promises 90
   days with no mechanism behind it; it needs a systemd user timer like the
   ingest has.
4. Task 10's remaining test gaps, and Task 13 (grants) in the backlog.

## Not in scope

- Paid tiers, payment, or anything that makes the credit convertible to money.
- Retiring `rate_events` outright. Task 3 may leave `export` on it; `export` is
  CPU and not tokens, so it does not belong in a cost meter.
- Metering `/compile` and `/preview`, which are unlimited for guests today and
  are genuinely CPU-expensive. A real abuse surface, and a separate problem.
- ~~Trimming `_history()`.~~ **Done and merged** — `4db08d1` on `main`, the
  same day, built from this plan's measurements. Spawned as a backlog task,
  picked up, and landed with its own spec
  (`docs/superpowers/specs/2026-10-24-chat-history-window-design.md`). It is
  the reason this plan's cost figures are now stale in the safe direction; see
  the spec, and re-run Task 0 after merging `main`.
- ~~Lowering `max_rounds` from 8 to 6, and chasing the cache hit rate.~~
  **Both retracted after Task 0.** Turns use 2–3 rounds against a ceiling of 9,
  so lowering the ceiling saves nothing; and the cache hit rate is already 82.6%
  (88–99% warm) against a ~90% ceiling. The spec records what was wrong with the
  original reasoning.
