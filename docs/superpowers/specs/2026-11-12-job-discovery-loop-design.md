# Closing the loop between the catalogue and the resume

**Date:** 2026-11-12
**Status:** approved — not implemented
**Touches:** backend `app/services/jobs/enrich.py` (new),
`data/job_taxonomy.json` (new), `app/services/jobs/ingest.py`,
`app/services/jobs/ingest_workday.py`, `app/services/jobs/store.py`,
`app/services/resume_facts.py` (new), `app/services/profile.py`,
`app/services/match/` (new: `prefilter.py`, `score.py`, `reasons.py`,
`digest.py`), `app/jobs/build_digests.py` (new), `app/jobs/match_sweep.py`
(new), `app/routers/jobs.py`, `app/routers/dashboard.py` (new),
`app/routers/applications.py` (new), `app/routers/match.py` (new),
`app/models.py`, `app/schemas.py`, `app/schemas_jobs.py`,
`app/services/metering.py` (purpose constants), `tests/test_metering.py`
(call-site inventory), several alembic revisions; ops `offerfy-digest.service`,
`offerfy-digest.timer`, `offerfy-digest-failure.service` (new);
frontend `app/[locale]/dashboard/page.tsx`,
`components/dashboard/` (new), `components/jobs/JobSearchForm.tsx`,
`components/jobs/JobView.tsx`, `components/jobs/JobResults.tsx`, `lib/api.ts`,
`lib/jobs.ts`, `messages/{en,zh-TW,zh-CN}.json`

## The finding

Offerfy holds a structured, published resume for every registered user. That is
the richest matching signal on the platform, and the job surface uses none of
it.

**The catalogue and the product do not touch.** Nothing in the schema connects a
resume to a job. `grep` for `job_id` across `app/routers/resumes.py` and
`app/models.py` returns nothing; there is no `TailorRequest` carrying a job
description, and `components/jobs/JobView.tsx` offers exactly one action — an
outbound link to someone else's apply page. A tailor resume is an orphan file
named after a posting it has no reference to. `messages/en.json` ships the
sentence as product copy:

```json
"metaDescription": "... Apply on the source site. No matching, no apply tracking."
```

**Search is three controls over three columns.** `JobSearchForm.tsx` offers free
text, ATS source and a remote tri-state. The backend (`app/routers/jobs.py`,
`_filtered_query`) runs `ILIKE '%term%'` against `title`, `company` and
`location` only — descriptions are stored and never searched — and orders by
`posted_at DESC` unconditionally. There is no relevance ranking, no result
count, and no facet for location, seniority, function or date posted. The one
filter that does exist, `source`, is an ingestion detail: nobody looks for work
by ATS vendor.

**Recommendation is editorial, not personal.** `app/services/jobs/spotlight.py`
scores `recency × role-keyword weight × source weight × remote boost` with
company and source diversity constraints. It is careful work and it produces
**the same 24 postings for every visitor**, computed without reference to any
user. `/v1/jobs/featured` takes no identity.

**Nothing pulls anyone back.** There is no saved search, no alert, no digest,
and no email sender anywhere in the backend — no SMTP, SES or Resend in
`requirements.txt` or `app/`. `/dashboard` is a resume file manager: list,
group master → tailors, open, delete.

### What the inventory supports

89 curated employers (77 in `data/ats_boards.json`, 12 in
`data/workday_boards.json`): the frontier-lab and developer-tools set (OpenAI,
Anthropic, Cursor, Linear, Supabase, Ramp, Sentry) plus the semiconductor
employers with real Taiwan presence (NVIDIA, Micron, Applied Materials, NXP,
Intel, Marvell) and Appier. `app/services/jobs/scope.py` already restricts
intake to technical roles, stating the audience outright:

> Offerfy sells resume craft to technical candidates chasing competitive roles

This is a narrow, high-signal catalogue rather than a scraped one, which is what
makes per-user matching tractable at all. It also means **"nothing worth your
time this week" is a genuine state, not an edge case** — 89 elite employers
against a user base that includes new graduates will produce empty weeks. Every
decision below treats that state as first-class.

## What this builds

One loop, and a subscription that feeds its top:

```
weekly digest → open posting → tailor master resume → apply → log the outcome
```

Success is return visits and tailors per user, not catalogue pageviews.
Monetisation is explicitly **out of scope**: there is no billing surface in this
design, and `components/PricingPage.tsx` stays a pricing page with no prices.
"Subscription" here means a standing interest that produces a dated digest.

## The profile question, settled

A match profile and the employer-facing talent profile overlap, but they are not
the same object and must not become one.

`TalentProfilePayload` (`app/schemas.py:494`) carries `display_name`,
`headline`, `summary`, `years_experience`, `locations`, `languages`, `skills`,
`roles[]`, `education[]`. The evidence half of a match score is exactly that,
and `_derived()` in `app/services/profile.py` already builds `skills_text` as
pipe-delimited lowercase (`|python|go|`) specifically so `LIKE '%|go|%'` cannot
match `javascript`. `input_hash` plus `is_stale()` already solve "the resume
changed, rebuild the derived view."

Two reasons it cannot simply be read by the matcher.

**Direction.** The profile is retrospective by design and by prompt: *"Use only
what the resume and the listed facts about the person say; never infer, guess or
fill a gap"*, and *"headline is the person's own current role in their words,
not a pitch"*. `locations` means where they are, not where they would move.
Target role, target seniority, remote requirement and intent to change
discipline are absent, and the builder is forbidden from inventing them. They
have to be asked for once.

**Consent purpose.** A `TalentProfile` row exists only while the owner's newest
answer for `talent_introduction` is `granted`, and
`delete_profile_for_user()` destroys it on withdrawal. If the matcher reads that
table, a user who does not want employers browsing them also loses job
recommendations — gating the primary retention loop behind an unrelated privacy
decision — and a consent collected for "introduce me to employers" would be
doing "rank jobs for me." That is purpose creep on the one subsystem in this
codebase most careful about it (three independent gates, an append-only
disclosure ledger, contact scrubbing, content hashes as evidence).

### One extraction, two consumers, two purposes

Lift the extraction out of `profile.py` into `app/services/resume_facts.py`: the
prompt, the JSON schema, `clamp_payload`, `scrub_contacts` and the derived
columns move; `ResumeFacts` is keyed on `(user_id)` with
`source_resume_id`, `source_revision_id` and `input_hash` carried over, and is
built from the user's published master revision plus accepted memory items,
exactly as today.

- **Employer side.** `talent_profiles` keeps its table, its columns, its hashes
  and its lifecycle unchanged. It becomes a consent-gated projection built from
  the same extraction: still written only under consent, still deleted on
  withdrawal, still scrubbed, still cited by `disclosure_events`. **No migration
  touches `talent_profiles`** — the disclosure ledger cites
  `profile_content_hash`, so the stored bytes must not move.
- **Match side.** `ResumeFacts` (evidence, no special consent — the user's own
  data serving the user's own benefit) plus `JobPreference` (intent, user-owned
  and user-editable).

`ResumeFacts` is built on publish-staleness like the profile is, and one build
serves both consumers. Where both are wanted, build once and write both rows
under one `input_hash`; where only one is, build only that one.

**Contact scrubbing stays on both.** The matcher has no use for contact details,
and a derived table that cannot hold them is cheaper to reason about than one
that may.

### One taxonomy, both sides

Job enrichment, resume facts and employer-side talent search share **one** skill
vocabulary and **one** seniority ladder, in `data/job_taxonomy.json`. If the job
side says `staff` and the resume side says `Staff Engineer` as free text, the
scores are noise. This is less machinery than the alternative, not more.

## Data model

Six new tables. Revision numbers are **not** fixed here: concurrent branches
collide on alembic revisions, so run `alembic heads` at implementation time and
number from there.

**`job_facets`** — derived, one row per posting, `job_id` PK/FK `ON DELETE
CASCADE`. `country`, `region`, `city`, `seniority`, `function`,
`description_hash`, `enriched_at`. Separate table rather than widening `jobs`,
for the reason `TalentProfile` and `ResumeContent` are separate: derived state
that can be dropped and rebuilt should not be interleaved with ingested state
that cannot.

**`resume_facts`** — derived, `UniqueConstraint(user_id)`, `ON DELETE CASCADE`
to `users` and to `resumes`. Mirrors `talent_profiles`' columns:
`source_resume_id`, `source_revision_id`, `input_hash`, `schema_version`,
`facts_json` (Text, canonical JSON — not JSONB, same reasoning as
`profile_json`: stable bytes across a round trip), `skills_text`,
`search_text`, `years_experience`, `built_at`, `model`.

**`job_preferences`** — user-owned, `UniqueConstraint(user_id)`.
`target_functions` and `target_locations` (pipe-delimited, matching
`skills_text`'s idiom), `target_seniority`, `remote_required` (tri-state
nullable, matching `jobs.remote`), `activity` (`actively_looking` |
`browsing` | `paused`), `updated_at`. Seeded from `resume_facts` on first
visit, then only ever written by the user.

**`applications`** — the loop's spine. `user_id`, `job_id`,
`tailor_resume_id` (nullable FK, `ON DELETE SET NULL` — deleting the tailor
must not erase the record that you applied), `status`
(`saved` | `tailored` | `applied` | `interviewing` | `closed`), `source`
(`digest` | `search` | `saved`), `applied_at`, `last_status_at`, `notes`.
`UniqueConstraint(user_id, job_id)`.

**`digests`** and **`digest_items`** — `digests`: `user_id`, `period_start`,
`built_at`, `model`, `input_hash`, `status` (`full` | `degraded` | `empty`),
`candidate_count`, `opened_at`. `digest_items`: `digest_id`, `job_id`, `rank`,
`tier`, `fit_score`, `reason`, `gap`, plus the four component probabilities.
`UniqueConstraint(digest_id, job_id)`.

**Digest text is frozen once written.** Someone who reads *"your Kafka work maps
onto their streaming platform team"* and returns tomorrow must see that
sentence. Same discipline as `ResumeRevision` and `profile_json`: derived, but
immutable after display.

## Job enrichment: deterministic only

There is **no** LLM skill-extraction layer, and no stored skill tags per
posting. The chicken-and-egg resolves the other way: the prefilter does not need
skills (function, seniority, location, remote and recency reduce the catalogue
on their own), and the scoring step reads descriptions anyway, where in-context
reasoning is better than a tag join. A precomputed skill table would be a third
vocabulary to keep synchronised for no gain.

`app/services/jobs/enrich.py` derives three closed vocabularies from
`data/job_taxonomy.json`:

- **Location** → `country`, `region`, `city`, parsed per source. Five ATS
  conventions, enumerable, with `remote` already carried on `jobs`.
- **Seniority** → one of `intern`, `new_grad`, `junior`, `mid`, `senior`,
  `staff_plus`, `manager`, `unknown`. Title-driven. The catalogue's own
  idioms must be encoded, not guessed: "Member of Technical Staff" is senior at
  the frontier labs, "SDE II" is mid.
- **Function** → `backend`, `frontend`, `fullstack`, `ml`, `data`, `infra`,
  `security`, `hardware`, `product`, `design`, `other`. Title first,
  description keywords as a tiebreak.

Tune the needle lists against the live catalogue, as `data/job_scope.json` was.
Follow `scope.py`'s bias: **unknown is kept, not dropped** — a false negative
silently loses a good posting and nobody notices.

Runs inside the ingest upsert, gated on `description_hash`: a posting is
enriched once per content version, not once per six-hour pass. No provider on
the ingest path — `offerfy-ingest.service` must not fail for reasons outside its
control, and it already carries `OnFailure=` and `Persistent=true` because the
catalogue going stale is the failure it exists to prevent.

Enrichment also finally gives `/jobs` real facets. The `source` dropdown is
**removed** from `JobSearchForm.tsx` and replaced with location, seniority,
function and date-posted; `source` remains a query parameter on `/v1/jobs` for
the admin views, which do care about it.

## Two cadences

Only one of them costs money.

**Every ingest pass (four times daily, free).** Enrichment on changed rows, then
the deterministic prefilter per active user, in SQL. Output is a count:
*"7 new matches"* on the dashboard, refreshed four times a day at zero marginal
cost. `app/jobs/match_sweep.py`.

**Weekly (one scoring pass per active user).** The digest: five postings,
ranked, with reason and gap lines. `app/jobs/build_digests.py`.

Daily freshness without daily spend, and language generated only where language
is shown. A fixed weekly rhythm is deliberate over event-driven firing: a
predictable arrival is what makes a subscription feel like one, and habit
formation beats per-notification usefulness here.

### The deterministic prefilter

Hard filters: `is_active` · `last_seen_at >= freshness_cutoff()` ·
`function ∈ target_functions` · `seniority` within one band of
`target_seniority` · (`country/region/city ∈ target_locations` **or**
`remote` satisfies `remote_required`) · no `applications` row for this
`(user, job)` · not present in the user's last four digests.

Soft score reuses `spotlight.py`'s shape — recency half-life, `max_per_company`,
the source-spread loop in `select_featured` — per user instead of globally. The
diversity loop transfers nearly unchanged.

Top ~30 go to scoring.

## Scoring on the Decisions API

The match score runs on `/v1/decisions`, not chat completions. The endpoint
returns calibrated probabilities and scored rubrics; a chat model writing `92`
is inventing a number, and a `predicate` returning `0.78` is answering a
question it was asked. Contract, from `app/services/layout.py`:

```python
{ "model": model,
  "input": [{"role": "user", "content": [...]}],
  "questions": [
      {"type": "predicate", "name": ..., "instructions": ...},        # → probability
      {"type": "score", "name": ..., "instructions": ..., "levels": [...]},
  ]}
# → {"answers": [...], "usage": {"input_tokens": N}}
```

Three properties shape the design: **input tokens are the whole bill** (no
output charge, and `turn.py` records `cached_tokens: None` because the endpoint
"has no cache line at all"), **independent questions share one input and it is
billed once**, and **refusal is its own answer type** which must never be read
as a low probability.

### One call per (resume, posting) pair

Not one call carrying thirty postings. Questions are answered about the *whole
input*, and this codebase has already paid for that lesson — `_chosen_pages`:

> one request answers one question about its whole input: asked to score the
> density of a two-page resume whose first page was 99% full and whose second
> held six lines, the model returned a single document-wide 0.07

So: resume facts plus one trimmed posting (title, company, location, ~1500
chars of requirements) per call, several questions riding along, `asyncio.gather`
across candidates behind a semaphore.

### The question set

One `score`, following the `SCORE_RUBRICS` shape at `layout.py:118`, whose
levels are the display tiers:

| rubric | levels |
|---|---|
| `fit` | Strong fit · Good fit · Worth a look · Not a fit |

Four `predicate`s for the components:

| name | asks |
|---|---|
| `requirements_met` | meets the stated technical requirements |
| `seniority_fit` | seniority band matches the posting |
| `location_fit` | location or remote arrangement works for this person |
| `direction_fit` | moves them toward their stated target, not sideways |

The predicates are what make the prose honest. A low `requirements_met` beside
three high components localises the gap, so the reason step is not asked to
"write a reason" but told *which dimension the gap is on and to name the
specifics*. That is a harder constraint than post-hoc string checking, and it
falls out of scoring already being done.

### Reuse

`decisions_url()` already handles the proxy-base cases (`/chat/completions`,
`/responses`, bare `/v1`) — `OPENAI_BASE_URL` may point at a proxy, which is the
footgun it exists for. `_error_detail()` and the refusal branch of
`_vision_answers()` transfer as written. The model comes from
`settings.openai_decisions_model` (`gpt-6-luna`), not a new constant.

### Cost

Per user per week: ~30 calls × (facts ≈2k + posting ≈500) ≈ **75k input
tokens, no output billing**. The facts block is byte-identical across all thirty
calls and is an ideal cache prefix, but the endpoint reports no cache line, so
treat caching as unproven rather than assumed and budget the full figure.

## Reason and gap prose

One Responses-API call per digest, over the five survivors only, following
`profile.py` exactly: strict structured output, hand-written JSON schema, fixed
cheap model (nobody is watching this call, so there is no preference for it to
express), pydantic validation after.

Output: `[{job_id, reason, gap|null}]`.

The prompt's prohibitions, in the voice `profile.py` already uses:

- **It may return fewer than five, and it may return none.** The honest empty
  state is only honest if the model is genuinely permitted to produce it, and
  every instinct of a ranking model is to fill the slots.
- **Every reason cites something from the resume facts and something from the
  posting.** "Matches your backend experience" is worthless and is the default
  output.
- **A gap is `null` when there is none.** Manufactured gaps are as bad as
  manufactured strengths.
- **No invented experience.** This is also legal exposure the moment a reason
  line becomes a claim about a person.

Then validate as `profile.py` does, and harder, because this is the only free
text shown to a user as a claim about their own history:

1. Drop any `job_id` not in the scored set.
2. **Reject a reason citing a skill, employer or institution token absent from
   `resume_facts.skills_text` / `search_text`.** On rejection the item keeps its
   tier and score and loses its sentence. An unverifiable sentence is worse than
   no sentence.
3. Clamp rather than reject on length, for the reason `clamp_payload` gives:
   truncating loses a clause, rejecting loses the item.

## Digest assembly and scheduling

`app/jobs/build_digests.py`, run by `offerfy-digest.timer` — a systemd **user**
unit, as `offerfy-ingest.timer` is, because agent sessions on this host have no
passwordless sudo and a user timer plus `loginctl enable-linger` needs none.
`Persistent=true`, `RandomizedDelaySec`, and an `OnFailure=` sibling writing to
the journal, mirroring `offerfy-ingest-failure.service`. Scheduled clear of the
ingest slots so it reads a settled catalogue.

**Who gets built for.** Only users whose `job_preferences.activity` is not
`paused` **and** who opened one of their last two digests. A churned user stops
costing money after two weeks with no separate churn model, and weekly spend
tracks engagement rather than signups. First-time users with a `job_preferences`
row are always built for once.

**No preference row → no digest.** The dashboard shows a one-time setup card
seeded from `resume_facts`. Guessing silently and then showing weak matches
loses someone on first impression.

**Stale facts → rebuild first.** `is_stale()` already detects a republished
resume; rebuild `resume_facts` before scoring, never after.

## Metering

Two new purposes, `match` (Decisions) and `digest` (prose), under
`system_guard(user=..., purpose=..., reason=...)` — the
`app/jobs/refresh_profiles.py` precedent verbatim: records against the ledger,
the weekly budget and the global ceiling, and never 429s itself, because nobody
is waiting on an HTTP response and refusing would only mean the job rate-limits
itself.

A digest build must **not** consume the person's edit allowance. They did not
ask for it, and charging their editing budget for an unsolicited recommendation
is hostile.

`purpose="match"` records `input_tokens` only, every other usage field `None` —
the shape `turn.py` already emits for `purpose="vision"`, and for the same
reason.

`tests/test_metering.py::test_billable_call_sites_are_wrapped` is an inventory
of every billable call site that fails when a new one appears. Both new call
sites go in it. Editing that test to pass without wrapping the call is the one
shortcut that defeats the whole design.

## Failure modes

The Decisions endpoint is described in `layout.py` as beta, and `vision_report`
is built to survive its absence: *"the geometry half of the report is still
worth returning when the beta endpoint is not there."* Putting the primary match
signal on it means the signal can disappear on someone else's schedule, so the
fallback is specified now rather than discovered later.

| Failure | Behaviour |
|---|---|
| Decisions absent or 4xx | Deterministic prefilter order, `status=degraded`, no tiers, no scores, no reasons |
| Refusal on one pair | Drop that candidate; never score it zero |
| Prose call fails after scoring | Keep real tiers, scores and order; render rows bare |
| Prefilter returns nothing | `status=empty`, and the digest says so with its cause |
| Resume facts unbuildable | No digest; dashboard shows the resume-health card's reason |

Degrading to "seven new roles matching your filters" is fine. Silently
reordering by a worse signal while still showing confident tiers is not.

## Surfaces

### Dashboard

`/dashboard` becomes the loop's home, one column, reading order matching loop
order. One aggregate `GET /v1/dashboard` returns the whole view: the page
currently runs `getMe` → `claimResumes` → `listResumes` sequentially on mount,
and four more sections that way is a six-request waterfall.

1. **Header** — the match profile in one line ("senior · backend, infra ·
   Taipei or remote") with an edit affordance. The cause of a wrong digest sits
   above the digest, not three clicks into settings.
2. **New for you** — the latest digest. Five postings, each with tier, reason
   and gap-when-present, unread-badged, each with one action: *Tailor it*. The
   `empty` state names its cause ("41 new roles, none senior backend in Taipei")
   and offers to widen the preference.
3. **Your applications** — grouped by status, ordered by heat:
   `interviewing` → `applied` → `tailored` → `saved`. One next action per row,
   determined by status: `saved` → *Tailor it*; `tailored` → *Apply*
   (outbound, then "did you apply?" on return); `applied` / `interviewing` →
   *Log a response*. The group `tailored` is named and aged ("ready 3 days
   ago") on purpose: it is where the loop leaks, and folding it into `saved`
   would hide the design's own biggest failure. Applied rows show quiet-time
   ("8 days quiet").
4. **Master resume health** — one card: published state, facts freshness,
   whether matching is running on the current version.
5. **Employers who viewed you** — `disclosure_count` and `last_disclosed_at`,
   which already exist and are already computed in `app/routers/talent_me.py`
   and currently surface only at `app/[locale]/settings/page.tsx:233`.

A grouped list, not a kanban board: a job hunt is 5–20 items, mostly linear,
with occasional self-reported transitions. Kanban buys drag-and-drop that is not
needed, costs more, and collapses on a phone.

Tailors stop being top-level objects. They appear as a link inside their
application row; the master resume is one card at the bottom. The file manager
that is currently the entire page becomes its least prominent element, because
nobody's goal is to manage files.

### `/jobs`

Keeps its public shell and its SEO assets — `jobs-sitemap`, `JobPosting`
JSON-LD, `isJobIndexable` noindex-on-stale — and gains a signed-in layer:
faceted filters from `job_facets`, a result count, a match tier when the viewer
has a preference row, a save action, and **Tailor for this role** on
`JobView.tsx`. A dashboard link to *Browse all roles* gives the pushed-to user a
way to go hunting.

**Apply state is self-reported.** Applications happen on the employer's ATS and
cannot be observed. The outbound click creates or advances an `applications` row
to `tailored`/`applied` optimistically and asks for confirmation on return.

### Tailoring from a posting

`POST /v1/resumes/{resume_id}/tailors` gains an optional `job_id`, which writes the
`applications` row and stores the linkage that does not exist today. This is the
join that makes everything above measurable: digest → tailored → applied is the
success metric, and it is unmeasurable until a tailor knows its posting.

## Rejected

**Semantic retrieval (embeddings over descriptions).** No pgvector, no embedding
provider and no vector dependency exists in `requirements.txt`; it would add a
Postgres extension, a provider and a migration. It also gives the weakest
explainability of the options, and embeddings conflate what someone has done
with what they want next — a backend engineer trying to move into ML gets more
backend postings. With 89 employers the candidate pool is small enough that good
filters plus calibrated scoring reach further. Not blocked by anything here if
match quality later plateaus.

**LLM enrichment per posting.** Puts a provider on the ingest critical path and
scales cost with catalogue size rather than usage, to produce facets that three
closed vocabularies produce for free.

**A unified profile serving both employers and matching.** Collapses two consent
purposes into one and makes job recommendations contingent on willingness to be
browsed by employers. See *The profile question, settled*.

**Email in this spec.** The digest is written as a channel-agnostic object with
an in-app inbox as its first renderer, so an email adapter is additive. Building
a transactional sender — provider, DKIM/SPF on `offerfy.cc`, unsubscribe
handling, bounce tracking — is its own piece of work, and in-app cannot reach a
drifted user, which is the gap that justifies doing it next.

**Event-driven alerting** (fire when something strong appears). More useful per
notification, less able to build a habit. Revisit after the weekly rhythm has
engagement data.

**A kanban pipeline.** See *Dashboard*.

## Testing

- `enrich.py`: table-driven over real catalogue strings, per source, including
  the idioms (`Member of Technical Staff`, `SDE II`) and the unknown-is-kept
  bias. Fixtures from `tests/fixtures/jobs/`.
- `prefilter.py`: the hard filters, the dedupe window, and that an
  `applications` row excludes a posting.
- `score.py`: the response parser against recorded `/v1/decisions` payloads —
  refusal handled as refusal, a missing `probabilities` list, a `job_id` absent
  from the candidate set. No live provider in tests.
- `reasons.py`: the citation validator, including the rejection path keeping
  tier and dropping prose.
- `build_digests.py`: each row of the failure table produces the specified
  `status`, and a build never partially writes.
- `tests/test_metering.py`: both new call sites present in the inventory.
- `resume_facts.py`: that extracting into it leaves `talent_profiles`
  byte-identical for a fixed input — the disclosure ledger cites those bytes.

Two expected local failures are unrelated to this work and documented for
worktrees: `node_modules`, the `typst` CLI and the package cache live only in
the main checkout.

Migrations have no test coverage anywhere in this repository and the only live
Postgres is production. Verify each revision up and down against a throwaway
database before it goes near the host.

## Decomposition

Too large for one implementation plan. Four slices, each shippable, each getting
its own plan:

**1 — Enrichment and faceted search.** `job_taxonomy.json`, `enrich.py`,
`job_facets`, ingest wiring, `/v1/jobs` facet parameters, `JobSearchForm`
rebuilt, result count, `source` dropdown removed. No LLM, no new user state.
Stands alone as a visible improvement to `/jobs` and is the substrate for
everything after it.

**2 — Resume facts and preferences.** Extract `resume_facts.py` from
`profile.py` with `talent_profiles` unchanged, add `job_preferences`, the
seeded-then-user-owned preference editor, and the deterministic per-user match
behind `match_sweep.py` and the "N new matches" count.

**3 — Applications and the dashboard.** The `applications` table, `job_id` on
tailor creation, `GET /v1/dashboard`, the pipeline and resume-health and
employer-views sections, the *Tailor for this role* action on `JobView`.

**4 — Scoring and the weekly digest.** Decisions scoring, prose generation and
its validator, `digests`/`digest_items`, `build_digests.py`, the systemd units,
and the *New for you* card.

Slices 1–3 deliver the loop with deterministic matching. Slice 4 is what turns a
filter into a reason to come back.

## Open, and deliberately deferred

- **Digest size.** Five, decided. Revisit if empty and three-item weeks turn out
  to be the common case, since "we found three worth your time" reads better as
  a statement than as a shortfall.
- **Whether the numeric score is displayed at all.** The rubric gives four
  labelled tiers, which is what the UI renders. `fit_score` is stored for
  ordering and evaluation, and showing the number is a later call, not a
  blocker.
- **Taiwan-local inventory.** `taiwanjobs` is retired and skipped by
  `load_boards`. If digests are routinely empty for Taipei-based users, the fix
  is inventory, not ranking, and that is a separate piece of work.
