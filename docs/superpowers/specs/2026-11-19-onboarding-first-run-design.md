# Onboarding: the first run, and matches that renew without a daily artifact

Status: design, awaiting approval. Parent: `2026-11-12-job-discovery-loop-design.md`
(slices 1–4, shipped). This amends that spec's cadence ruling; see **Approaches**.

## Intent

A new account cannot feel what Offerfy is for. Measured from the code, the path
from signing up to seeing a single scored match is four manual steps and up to
**6 days 19 hours**, and one of those steps was impossible.

The job is to make the product demonstrate itself inside the first session, and
to keep demonstrating it without waiting for Monday.

### What was actually in the way

Found by walking the funnel in code, in order of how many people it loses:

1. **The facts build was refused for every new account.** `profile_builds_per_hour`
   was 0 for the `registered` tier, and `enforce_usage` refuses on `burst >= limit`,
   so `0 >= 0` refused on sight. That number was sized for the *talent* profile,
   where only matching tier can build at all; matching's resume facts go through
   the same purpose and need no such grant. With no facts, the preference seed is
   empty; with an empty preference, `constrains_nothing` returns the empty set;
   so the match count was 0 for every new user regardless of the catalogue.
   **Fixed and shipped ahead of this design** — see **Already landed**.
2. **Publishing was a hidden gate wearing a share label.** Matching reads
   `resume_facts.default_source`, which only considers masters carrying a
   `published_revision_id`. Nothing in the upload path set one, and
   `/v1/auth/claim` does not either, so the person who took the hero's own CTA
   ("Upload your resume") arrived signed in with a document matching could not
   read. **Fixed and shipped ahead of this design.**
3. **The first save is silent.** `PUT /v1/me/job-preferences` writes a row and
   returns. No facts build, no scoring, no digest. The feedback for the one step
   that unlocks everything is a "Saved" label.
4. **Scored matches arrive weekly, on a fixed Monday.** Sign up Monday afternoon
   and the first scored match is 6 days 19 hours away.
5. **Every build re-scores from scratch.** There is no per-(user, job) score
   record, so `build_digests` pays for 30 pairs per user per week, including the
   pairs it scored last week — and `strong_fit` deliberately re-shows a posting
   until the person acts on it, so repeat scoring is the normal case, not an edge.

### Why now

(3), (4) and (5) are one mechanism, not three. Built in the order they were
first proposed, the first-run code written for (3) is thrown away by (5); built
cache-first, (3) is a few lines.

## Goals

- A new account sees scored matches, with reasons, **inside the first session**.
- A posting that enters the catalogue is scored for everyone it could suit
  **within about 30 minutes**, not by the following Monday.
- The marginal cost of keeping a returning user's matches current falls to
  roughly zero, so freshness is no longer rationed by a weekly budget.
- The weekly digest keeps working, keeps being frozen, and gets cheaper.

## Non-goals

- **Inventory.** The parent spec measured 851 active Taiwan postings of which 36
  are software-family, and says twice that the fix for a thin market is inventory,
  not ranking. Nothing here changes that, and nothing here should be read as
  attempting it.
- **Email or push.** Offerfy still notifies nobody. Every surface below is read
  by someone who came back on their own.
- **The per-user soft score** the parent spec left unimplemented in
  `build_digests.candidates` (recency half-life, `max_per_company` spread). Still
  deferred, still has no task.
- **Re-scoring on every resume edit.** Staleness is recorded and repaired by the
  sweep's next pass, not chased in real time. (Up to six hours, not up to a
  week; see §3's facts rebuild.)

## Anti-goals

Things that would make this a failure even if it shipped and worked:

- **An artifact that is empty most days.** With one new posting a week for a
  typical preference, anything that promises a daily quota will fail to fill it
  six mornings in seven. A heading that claims novelty it cannot supply makes the
  product feel deader than the weekly rhythm it replaced. This is the single
  reason approach A below was rejected after measurement.
- **Spending a person's edit allowance on matching.** The parent spec's rule
  stands: *"a digest is not an edit, so it must not consume the person's edit
  allowance — they did not ask for it."* A first run that eats a registered
  user's 20 weekly credits before they have edited anything would be worse than
  the wait it replaces.
- **A second churn model.** Weekly spend already tracks engagement through
  `is_eligible`. Adding a parallel notion of "active" for the sweep would give
  two answers to one question.
- **Losing the frozen record.** `Digest` is evidence of what a person was shown
  on a date, alongside `input_hash`, `opened_at` and the
  `full`/`degraded`/`empty` vocabulary. This codebase treats that kind of record
  as load-bearing (see `disclosure_events`), and so does this design.

## Constraints

- **Catalogue arithmetic.** Parent spec, measured read-only against production:
  TW + (backend|software) + mid/senior → **15 eligible, 1 new in 7 days**. Any
  country + (backend|software) + mid/senior → 910 eligible, 75 new in 7 days. The
  design must be correct at both ends of that range.
- **No CI, nothing self-deploys.** `AGENTS.md`: the host is rebuilt and restarted
  by hand, and `docs/deploy.md` (gitignored) is the runbook.
- **Migrations have no test coverage and the only live Postgres is production.**
  Verify `042` against a throwaway database before it goes near the host.
- **Alembic head is `041_chat_messages_resume_index`.** Revisions collide across
  branches; confirm the head again at implementation time.
- **The ingest unit's exit code is load-bearing.** `ingest_catalog` exits
  non-zero when a pass refreshed nothing, so a stalled catalogue shows up as a
  failed unit. The sweep must not share that unit: a scoring fault must not
  read as a stalled ingest, and the sweep spends money and has to be disablable
  on its own.
- **Spend ceilings.** `global_weekly_ceiling_uusd` is $150/week.
- **`Digest` is unique on `(user_id, period_start)`**, and `is_eligible` filters
  on `Digest.period_start < window_start(now)` so a digest is never evidence
  about itself. Any change to the period interacts with both.
- **SSE is pool-safe as of 2026-11-15**, via the `_stream_owned_id` pattern
  (authorize on a session that closes before the stream opens). A long-lived
  stream no longer pins a connection, which is what makes a progress stream
  affordable here.

## Approaches

Three were considered. The first was eliminated by measurement after it had been
chosen, which is recorded here because the reasoning matters more than the
outcome.

### A — daily frozen digest (chosen, then rejected)

Move `Digest.period_start` to a day boundary, run `build_digests` daily, and put
a score cache behind it so a run only pays for pairs it has not scored.

The cache answers the parent spec's cost objection to daily ("7× the prose
calls"), which is why this looked right. Measurement killed it on the other
half. For a typical preference, six builds in seven find no new posting, so they
mint a row whose `new_this_week` track is empty and whose `strong_fit` track is
yesterday's five roles under a heading that says *today*. The reader opening on
Thursday sees Monday's list with nothing on the page admitting Thursday produced
nothing. It also multiplies digest rows sevenfold, most of them empty, and makes
`opened_at` a noisy daily signal where it is currently a clean weekly one.

### A′ — daily scoring, weekly digest (chosen)

Scores live on the `(user, job)` pair and are written when a posting arrives.
Nothing is created on a day when nothing arrived, so nothing can be empty. The
weekly `Digest` stays exactly what it is: the frozen record, the prose, the
engagement signal.

Trades away the daily heading. Gains lower latency than A — a posting is scored
about 30 minutes after it enters the catalogue, against up to 24 hours for a
daily build and up to 7 days today — and gains a `Digest` that costs prose only,
because its scores are already paid for.

### B — scores on the pair, no digest at all

`JobScore` becomes the source of truth; the dashboard reads "my best unacted-on
matches" live and the concept of a cadence disappears.

Simplest to reason about and rejected: it discards the frozen record, `opened_at`
and digest history, which are working machinery, in exchange for a framing
change. A′ gets the same freshness without the demolition.

**Ruby picked A′**, after seeing the measurement that eliminated A.

## Already landed

Shipped ahead of this design, under TDD, because both are bug fixes rather than
features and the rest of the work is meaningless without them. Full backend
suite green (1883 tests).

- `TIERS[registered].profile_builds_per_hour`: 0 → 3. The comment that claimed
  the registered value was "unreachable by construction" was true when written
  and false once matching started reading the same extraction; it now says so.
  Guest stays 0 and is genuinely unreachable — both endpoints on this purpose
  require a signed-in user.
- `import_run.finish` now calls `pin_current_draft`, after the supersession
  guard. A finished import is the one moment the document is known to hold the
  person's own content rather than the starter's placeholders, which is why it is
  that seam and not `create_resume`. Pinning is not publishing to the web;
  `public` is a `ResumeShare` row and a test asserts it stays untouched.
- `components/jobs/preferenceRefusal.ts`, extracted and unit-tested: a `burst`
  refusal now reads "try again shortly" at every tier. It previously read "turn
  on matching recommendations first", which was the right message when the cap
  was 0 below matching and becomes a lie the moment the cap is real. The dead
  `rebuildNeedsMatching` key is gone from all three locales.
- **New regression guard:** one test walks signup → facts → seed → match count
  with `USAGE_LIMITS_ENABLED=true`. Every link in that chain already had a
  passing test; the chain did not, which is how a 0 in a tier table became an
  invisible funnel break. Note that the whole suite otherwise runs with
  `APP_ENV=development`, where `limits_enabled()` is False — so enforcement
  paths are untested unless a test asks for them by name.

## The design

### 1. `job_scores` — one row per (user, posting)

Migration `042_job_scores`, which also adds the server-side "last looked at
matches" column on `users` that §5 needs. One revision, because the two land in
the same release and a half-applied pair has no useful meaning.

| column | notes |
|---|---|
| `id` | uuid pk |
| `user_id` | FK users, CASCADE, indexed |
| `job_id` | FK jobs, CASCADE, indexed |
| `facts_input_hash` | `ResumeFacts.input_hash` as it was at scoring time |
| `description_hash` | `JobFacets.description_hash` as it was at scoring time |
| `model` | the scoring model |
| `tier`, `fit_score` | as `Scored` |
| `requirements_met`, `seniority_fit`, `location_fit`, `direction_fit` | the four components, nullable |
| `reason`, `gap`, `prose_model` | nullable; written only when prose was bought |
| `outcome` | `scored` \| `refused` \| `prose_rejected` — see below |
| `scored_at` | |

Unique on `(user_id, job_id)`. `tier` and `fit_score` are **nullable**, because
not every answer the provider gives is a score.

**A refusal is remembered, and this is why the row exists at all for one.**
`score.py` treats a refusal as its own answer type: the pair goes to
`ScoreRun.refused`, gets no `Scored`, and deliberately does *not* set `error`,
because reading a number off a refusal would invent a confident "no". If a
refused pair wrote no row it would be a cache miss on every subsequent pass and
be re-bought four times a day forever — invisible in the per-pass number the
deploy plan depends on. So a refusal writes a row with `outcome='refused'` and a
null tier. It is **not retried** while both hashes hold: a refusal is about the
content of that pair, and re-asking the same question costs money to get the
same answer. A changed resume or a rewritten posting makes it a new question and
it is asked again.

`prose_rejected` is the same bargain one level up: `reasons.py`'s validator
drops a reason it cannot cite, and without a record the sweep would re-buy that
prose every pass. The score stands; only the sentence is marked unavailable.

**Why the two hashes are recorded rather than keyed on.** Keying on them would
accumulate a row per resume edit per posting, unbounded. One row per pair, with
the hashes written down, still answers "is this score stale?" while staying
bounded at one row per pair the person could ever be shown.

**What "stale" compares to, precisely, because the wrong reading is an infinite
loop.** `job_scores.facts_input_hash` holds the `ResumeFacts.input_hash` of the
row that was actually scored against. Staleness compares it to the
`input_hash` on the person's **stored `ResumeFacts` row** — never to
`resume_facts.input_hash(...)` recomputed from the live resume. The recomputed
value is what `facts_are_stale` answers a different question with ("has the
resume moved since we extracted?"), and comparing a score against it would make
every pass see the same pairs as stale, rescore them, write the same stale hash
back, and do it again six hours later: 30 pairs × 4 passes a day, forever, for
anyone who edited their resume. The two questions are kept apart: the sweep
repairs the facts first (§3), and only then is the score's hash compared against
a row that has stopped moving.

**Why `description_hash` and not just the resume's.** A posting whose description
changed is a different question, and `JobFacets` already carries the hash that
says so. Without it a rewritten posting would keep a judgement of text nobody
scored.

**Pruning.** Rows whose posting is inactive or older than `freshness_cutoff()`
are deleted. The FK cascade only covers deletion, and postings are flipped
inactive rather than deleted, so without this the table grows forever.

It is **one global statement per pass, outside the per-user loop**, and it runs
even on a pass that scores nothing. Pruning inside the loop would inherit the
loop's audience, and someone who stops opening digests leaves that audience
after two weeks (`ENGAGEMENT_WINDOW`) — so their rows would never be visited
again and never pruned, which is the unbounded growth this exists to prevent,
merely relocated. It must also sit outside the ceiling check: running out of
money to score is no reason to stop deleting dead rows.

### 2. `score_cache` — the read-through

New `app/services/jobs/score_cache.py`, wrapping `score.score_candidates`:

```
async def cached_scores(db, *, user, facts, jobs, cap) -> ScoreRun
```

Returns the same `ScoreRun` the provider path returns, assembled from fresh
cache rows plus whatever it had to buy. Misses and stale rows are scored, up to
`cap`, and written back.

**`error` means a provider fault, and a refusal is not one.** `ScoreRun.error`
is set when a miss could not be bought — the provider was unreachable, returned
unparseable output, or `score_candidates` gave up after a fault. It is **not**
set because the model declined a pair, which matches `score.py` today: a refusal
goes to `refused` and leaves `error` None. Getting this backwards would mean one
permanently-refused posting in someone's pool degrades their weekly digest every
week, forever, which is not what happens now.

**Cached rows survive a fault, and the digest must stop throwing them away.**
This is where `build_digests` needs more than a call-site swap, contrary to what
this design said in its first draft. `_judge` today discards everything on
`run.error is not None` and writes `UNRANKED_TRACK` with the raw prefilter
order. That was right when a fault meant *nothing* had been judged. With a cache
the common outage case becomes 29 real judgements plus one new posting that
could not be bought — and the current branch would answer that by showing 30
postings in prefilter order with no scores at all, while holding 29 scores in
hand. That is strictly less honest than ranking the 29.

So `degraded` changes meaning, from "anything was unscored" to **"nothing was
scorable"**. `_judge` takes the degraded branch only when it has no scores at
all; otherwise it ranks what it has. The `strong_fit` cap already means a digest
shows fewer than its pool, so a missing candidate is not a visible gap.

This is a real change to a frozen vocabulary, and the honest consequence is that
`Digest.status == 'degraded'` means something slightly narrower after this
release than before it. Rows written earlier are not rewritten and not
reinterpreted: the status still says "we could not rank these", which was true
then and is true now. Flagged rather than buried because this design claimed a
one-line change and that claim was wrong.

Side effect worth stating: once the sweep is running, the weekly digest's scores
are already bought, so its cost falls to prose alone.

**One real wrinkle.** `Digest.model` is documented as "the scoring model... what
produced the judgement the person is shown", and cached rows may disagree with
each other after a model change. Store the single model when every shown item
agrees, and the literal `"mixed"` when they do not. Silently recording one of
them would make the column a lie, which is the column's whole job not to be.

### 3. `score_sweep` — the daily half

New `app/jobs/score_sweep.py`, plus `ops/systemd/offerfy-score-sweep.{service,timer}`
and a failure unit, following `offerfy-ingest-failure.service`'s pattern.

- **Schedule: `00,06,12,18:40`.** The ingest runs at `:10` with up to 10 minutes
  of randomised delay, so `:40` reads a catalogue that just settled. A posting is
  therefore scored within about 30 minutes of entering the catalogue. Its own
  unit, not a step inside `ingest_catalog`, for the three reasons under
  **Constraints**.

  (Corrected 2026-11-19, in the final review of this branch: Monday 00:40 is
  dropped, so Monday runs `06,12,18:40`. This schedule plus the shared ceiling
  below made the week's first spender the Monday 00:40 pass — the pass most
  likely to be expensive, because everything whose facts or descriptions moved
  over the weekend is rescored in it — while the digest that the whole
  eligibility system depends on runs at Monday 05:00. The ceiling decision
  stands; the schedule gives the digest first claim. The reasoning lives in
  `ops/systemd/offerfy-score-sweep.timer`.)
- **Audience: `is_eligible`, reused unchanged.** The weekly digest still exists
  in A′, so the signal it feeds still works, and reusing it means there is one
  answer to "is this person still looking" rather than two. Paused preferences
  are skipped. A first-timer with a preference row is in, which is what makes
  the sweep the durable backstop for the first run below.
- **Per-user cap per pass.** `SWEEP_CAP = 30`, newest first. A typical
  preference's whole eligible pool is 15, so it finishes in one pass; a broad
  preference (910 eligible) fills in over several. Bounds the cold-start cost on
  the first pass after deploy, which is the one pass where no cache exists.
- **Prose for strong-tier new arrivals.** The sweep buys `write_reasons` for
  arrivals that land in `STRONG_TIERS`. Ruby's call, taken in conversation: the
  arithmetic is one new posting a week, so this is one or two calls per user per
  week, and it means a fresh role that fits arrives *explained* rather than
  waiting for Monday to acquire a sentence. Without it a swept arrival would
  carry a tier and a score but no reason, and the reason is most of what makes a
  match feel like a judgement rather than a filter.
- **Stale resume facts are rebuilt before scoring, exactly as the digest does
  it.** `build_digests._facts_for_scoring` rebuilds first because "judging
  someone against a resume we know they have replaced reads as a bug in the
  score rather than in the schedule", and nothing else repairs `ResumeFacts` —
  `refresh_profiles` rebuilds `TalentProfile` only. Without this the sweep
  writes judgements against a superseded resume until the next Monday, and the
  non-goal above promises a repair that nothing performs. So the sweep's
  purposes are `profile`, `match` and `digest`, not two.
  **The cost is one extraction per resume edit, not one per pass**: after the
  rebuild the facts are fresh, so the next pass finds nothing to do. Skipped
  when the pass has nothing to judge for that person, same as the digest's
  `judging=False` path — the candidate set is free SQL and the rebuild is not.
- **`system_guard` for all three purposes.** Nobody is waiting on an HTTP
  response, so `preflight` would only make the job 429 itself — the same
  reasoning `build_digests` records.
- **Global ceiling, checked once per user, never inside one.** Lifted wholesale
  from `build_digests.build_all`: a user who starts always finishes, so no
  pass leaves a half-scored pool, and users not reached are listed by id.
- **One shared ceiling, and no reserve. Ruby's call.** The sweep, the weekly
  digest and first runs all draw on the same `global_weekly_ceiling_uusd`,
  first-come-first-served. A per-pass ceiling and a reserved slice for first
  runs were both offered and declined in favour of not adding a second budget
  knob.

  The consequence is real and is written down rather than softened: **the sweep
  can exhaust the week**, and when it does, that week's remaining signups get no
  first run and Monday's digest may not build. A cold-start pass is the obvious
  case, and so is a handful of broad-preference users — 910 eligible postings
  each, 30 a pass.

  What bounds it instead, all of it already here: `SWEEP_CAP` caps per user per
  pass; longest-unswept-first ordering means a truncated pass does not starve
  the same tail twice; refusals and `prose_rejected` are remembered so nothing
  is re-bought; and once a user's pool is scored their marginal cost is zero,
  which is the whole point of the cache. The exposure is therefore concentrated
  in the first pass after deploy, which is exactly what the deploy gate below
  measures. **That gate stops being advisory: the sweep lands disabled and is
  enabled only after a dry run has produced the pass-size number.**
  `skipped_over_ceiling` is the signal to watch afterwards.
- **Order: longest-unswept first**, so a pass cut short by the ceiling does not
  starve the same tail every time. Same self-correcting argument as
  `build_digests.build_order`.

### 4. The first run

The immediacy half, and small because the sweep did the work.

- **Trigger: the client, not the `PUT`.** `PUT /v1/me/job-preferences` gains
  nothing but a `first_save: true` in its response when no row existed before.
  The write stays a pure write; the client takes that flag, lands on the
  dashboard, and starts the run where the person is already looking.
- **`POST /v1/me/matches/first-run`** starts the work as a detached task and
  returns at once. **Progress is the `job_scores` rows themselves** — the run
  writes them back incrementally, and the client polls a cheap
  `GET /v1/me/matches/progress` returning `{scored, expected}`.

  This replaces an earlier draft that said "an SSE stream modelled on the
  resume import's" and, two bullets later, "nothing durable has to be queued".
  Those contradict: the import's shape is a durable `ResumeImport` row written
  by the POST and a *separate* GET stream polling it, which exists because
  `offerfy-api` runs two uvicorn workers and the only place progress can live is
  Postgres. `EventSource` cannot issue a POST, so an SSE version needs either a
  second progress table this design never mentioned, or the POST's own response
  body as the stream — which loses reconnect, a second tab, and anything to show
  after a reload.

  Counting rows needs neither. The rows are already durable, already the thing
  being produced, and already the answer to "how far along is this": a reload or
  a second tab reads the same count, and nothing is left behind if the worker
  dies mid-pass. `expected` is `count_matches(pref)` capped at the run's cap,
  which the dashboard can already compute.
- **Available only when the person has no `job_scores` rows at all.** That is
  what "first" means, and it bounds re-triggering without a separate rate limit.
  Two tabs posting at once both observe zero rows, so the write-back is an
  **upsert** on `(user_id, job_id)` and a duplicated run costs a little money
  and corrupts nothing. Worth a test rather than a lock: the window is seconds
  and the loser writes the same answer.
- **`system_guard`, not `preflight`.** Matching spend is the platform's; see the
  anti-goal. The person asked for matches, not for a build, and billing a
  signup's 15 scoring calls to their 20 weekly edit credits would spend their
  allowance on something they cannot see being spent.
- **Over the global ceiling, it says so, and does not promise a time.** An
  earlier draft had the endpoint answer "your matches are coming" on the grounds
  that the sweep would reach them within six hours. With one shared ceiling
  (§3) that reasoning is circular: the ceiling is the sweep's budget too, so the
  thing being promised is the thing that has run out. The endpoint reports that
  matching is paused for the week and the dashboard says that plainly. Lying
  pleasantly to a brand-new account about when it will see anything is worse
  than the wait it was invented to hide.
- **In the normal case the first run is an accelerator, not the mechanism.**
  Someone who saves their preference and closes the tab is picked up by the next
  sweep pass within six hours, which is why nothing durable has to be queued at
  save time.
- With a 15-posting pool this is about 15 concurrent Decisions calls behind
  `score_candidates`' semaphore: a few seconds, and the whole eligible set.

### 5. The dashboard

Verified in **zh-TW, not English**. CJK labels run about 1.5× wider and read
heavier; fixed widths wrap and filled buttons crowd, and this is the surface
where that has bitten before.

- **An onboarding card, seeded and pre-ticked.** Today the one blocking step is a
  text link inside a pill, and it opens a full checkbox wall — eight functions,
  every country, two selects, an activity picker — with nothing chosen. With the
  facts build working, the seed is a real answer: the card shows what the resume
  already implies, pre-ticked, as three questions, with Save starting the first
  run. The full editor stays at `/preferences` for anyone who wants it.
- **Scored arrivals lead, and the weekly digest card stays below them.**
  Ruby's call: two surfaces doing two jobs — *"this week, picked and explained"*
  and *"what landed since you looked"*. Folding them into one list and reducing
  the digest to a pure record were both offered and declined, which keeps the
  prose the digest buys being read by someone and keeps the frozen record
  visible rather than merely stored. The overlap is accepted: a posting can
  appear in both, and `uniqueByJob` already exists for the within-card case.
  `MatchesBlock`'s current two-column split ("a score on the left, 'not scored'
  on the right") is what gets rebuilt, since the sweep erases exactly the
  distinction those columns were drawing.
- **Arrivals carry rows, not a count.** `new_match_count` currently carries a
  bare number. With the cache it carries tier, score and, for strong arrivals,
  the reason. This is where "renews daily" becomes visible, and it is relative
  to the reader — it fills when they have been away and says nothing when they
  have not, which is the property the daily heading could not have.
- **"Since you last looked" becomes server-side. Ruby's call, and it reverses a
  documented decision.** `lib/last-visit.ts` keeps the marker in
  `localStorage` and argues the per-browser answer is deliberate — *"not a fact
  worth storing"*. That was right while the line was a nicety at the top of the
  page. It is wrong now that it is the product's main surface: a per-browser
  marker makes "new to the reader exactly once" false the moment someone owns a
  phone and a laptop, and a cleared store leaves a returning user with nothing
  to ask with and therefore no arrivals at all.

  So a column on `users` in migration `042`, stamped by the dashboard GET —
  which already writes on read (`digest.opened_at`), so the precedent and its
  failure handling exist. The client `since` parameter is dropped rather than
  kept as a fallback: two sources for one number is how they disagree. The
  `lastVisit === null` collision noted above dissolves with it — a NULL column
  is a genuine first visit and nothing else, so the first-run user with fifteen
  fresh matches stops landing in the "Welcome back." branch.

  `lib/last-visit.ts`'s comment must be updated or the module removed, not left
  asserting a decision that no longer holds.
- **Standing matches sit below, claiming nothing about recency.** A posting is
  new to the reader exactly once. This is the structural answer to `strong_fit`'s
  repetition, which weekly hid and daily would have exposed.
- **A matching posting with no score yet is shown, without a score.** A broad
  preference fills in over several passes (§3), so for up to a day the prefilter
  holds postings `job_scores` has no row for. The read model is therefore a
  LEFT JOIN that keeps those rows rather than an inner join over scores — which
  is also what the dashboard does today, where the right-hand column is
  explicitly unscored and says so. Hiding them would make `match_count`
  disagree with the list under it; showing them unlabelled would read as "poor
  match" rather than "not looked at yet".
- **A bug to fix while here, found in review.** `lastVisit === null` currently
  means two different things — a genuine first visit and cleared browser
  storage — and both render "Welcome back." Worse, the first-run user is in that
  branch at the exact moment they have fifteen brand-new scored matches, so the
  arrivals section has no window to ask with and shows nothing. Whatever answer
  §6 of **Open** takes for the marker, these two states must stop sharing a
  branch.
- **The empty state shows the shape.** A sample pick — tier, score, reason,
  greyed and labelled as an example — so a new account can see what is coming
  before it has anything. Currently four empty cards and *"No picks this week."*
- **`sinceFirstVisit` is "Welcome back."** in all three locales, rendered when
  `lastVisit === null`. It is the literal first sentence a new account reads.

### 6. Strings this falsifies

The parent spec shipped a twelve-string audit as a gate, on the principle that
strings describing the old behaviour must change *in the same release*, not in a
later sweep. Same gate here. At least these, across `en`, `zh-TW`, `zh-CN`:

- `dashboard.sinceFirstVisit` — "Welcome back." to a first-time reader.
- `dashboard.picksEmpty` — "No picks this week." is the empty state a new account
  meets; it describes a quiet week, not an account that has never run.
- `dashboard.picksScoredOn` — "scored {date}" is weekly-digest framing applied to
  rows that may now be scored continuously.
- `dashboard.matchesNewThisWeek` — "new this week" against a window that is now
  "since you last looked".
- `dashboard.matchEmpty` — "Tell us what you want next to see matching roles" is
  the right sentence and now has a card to point at rather than a link.
- **A string that does not exist yet and is needed:** "still scoring", for a
  matching posting whose score has not been bought (the LEFT JOIN rows in §5).
  Today's "not scored" column means "nothing will ever score these"; the new
  state means "not yet", and reusing the old string would promise something the
  old one deliberately did not.

Whoever implements §5 re-greps rather than trusting this list: the parent spec
found twelve where it expected fewer, and marketing negatives in particular go
stale as features ship.

## What is left to the builder

- Exact `score_cache` return shape for the partial-fault case, within the
  contract stated in §2: cached rows are returned, and `error` is set when a miss
  **could not be bought** — not merely when a miss went unscored.

  (Corrected 2026-11-19, after the Task 2 review caught this line contradicting
  §2. It read "`error` set whenever a miss went unscored", which would make a
  refusal and a cap-truncated miss into faults. §2 is the binding text: a refusal
  is an answer and a cap is a budget, and neither is a provider failure. No
  consumer may read `error is None` as "the pass is complete".)
- Whether `cached_scores` takes `cap` as a parameter or reads a module constant
  at each call site. `build_digests` sets the precedent of both — a module
  constant *and* a parameter on both entry points — and that precedent should
  win.
- The sweep's logging and report shape. `BuildReport`'s per-id lists are the
  model to copy, including its deliberate absence of a `failed` list.
- Progress-event vocabulary for the first-run stream. `lib/import-progress.ts`
  (51 tests) is the pattern; reuse its shape rather than inventing a second one.
- The onboarding card's exact three questions and their layout, inside the
  zh-TW constraint.
- Whether `042` needs a backfill. It does not: an absent row means "not scored
  yet", which is exactly what the sweep looks for, so the table ships empty and
  fills itself. Confirm no reader treats absence as an error.
- **`match_sweep._any_token`'s deferred optimisation is now due, and its own
  comment says so:** *"revisit when a later slice runs the prefilter once per
  active user on a schedule, which is where a sequential scan starts to
  matter."* That is this slice. The sweep runs `prefilter_query` per eligible
  user, 28 times a week, against ~20k postings, and every column `_any_token`
  is called with (`JobFacets.function`, `.country`, `.region`, `.city`) holds a
  single token rather than a pipe list — so `lower(col).in_(tokens)` is
  equivalent, index-usable, and sheds the LIKE escaping. Do it as part of this
  work rather than leaving a comment that has now been overtaken twice. It is
  behaviour-preserving and wants a test that pins the equivalence, including the
  cases the escaping existed for (`|tw|` must not match `TWX`, `|ml|` must not
  match `html`, `north_america` must not treat `_` as a wildcard).

## Decided in review

A builder check raised nine questions. Six were holes in this document and are
answered above: refusals and rejected prose get recorded rather than re-bought
forever (§1); staleness compares against the stored facts row, never the
recomputed hash, because the other reading rescores the same pairs every six
hours indefinitely (§1); the sweep rebuilds stale facts as the digest does, so
its purposes are three not two (§3); pruning is one global statement outside the
per-user loop, or it inherits an audience that abandons exactly the rows it is
meant to delete (§1, §3); a matching posting with no score yet is shown unscored
rather than hidden (§5); and the first run's progress is the rows themselves,
which resolves this document's own contradiction between "modelled on the resume
import" and "nothing durable has to be queued" (§4).

One was a mistake in this document's framing rather than a gap: it claimed
`build_digests` needed a one-line change. It does not. `degraded` has to stop
meaning "anything was unscored", or a provider outage discards 29 cached
judgements to show 30 postings in prefilter order (§2).

Three were Ruby's and are recorded where they apply: **one shared spend ceiling
with no reserve** (§3, with its consequence stated), **the weekly digest card
stays, arrivals above it** (§5), and **the last-looked marker moves server-side**
(§5, reversing `lib/last-visit.ts`'s documented choice).

## Open

Nothing blocking. Two things to decide at deploy time, not design time:

- **Deploy order.** This release carries a migration, new API routes and a
  rebuilt dashboard, which is the case `AGENTS.md` §3 covers: `alembic upgrade
  head`, then `npm run build:public`, then `kill -HUP` the API, then roll web.
  The API must lead, because the new frontend calls routes the old API lacks.
  Note that `/en` needs deleting and the web rolled twice, routinely, or ISR
  serves a stale landing page.
- **Turning the sweep on.** It spends money against production on a timer. It
  should land disabled, be run once by hand with `--dry-run` to see the pass
  size, and be enabled only after that number is known.
