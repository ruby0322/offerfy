# Usage policy: three tiers, metered on real cost

**Date:** 2026-10-24
**Status:** design, not implemented
**Plan:** [docs/superpowers/plans/2026-10-24-usage-policy.md](../plans/2026-10-24-usage-policy.md)

## Goal

Three usage tiers — guest, signed in, open to matching — with a weekly ceiling
each, metered on what the model calls actually bill, explained on a public page,
and visible to the person inside the editor while they spend it.

The guest tier is deliberately sized to be *insufficient*: enough to feel the
thing work on their own resume, not enough to finish polishing one page.

## What already exists (do not rebuild it)

Four of the pieces this needs are already in the codebase, and three of the
stated requirements are already met. Reading them first changes the shape of the
work from "build metering" to "attribute and enforce metering that already runs".

| # | Finding | Evidence |
| --- | --- | --- |
| 1 | **Per-call token telemetry already lands in the database.** `LlmCall` holds `input_tokens`, `cached_tokens`, `output_tokens`, `reasoning_tokens`, `model`, `purpose`, `round_index`, `source_chars` — one row per model round, written by the chat router and the profile builder. There is no need for a new ledger table. | [models.py:343](../../../apps/backend/app/models.py) `LlmCall`, [chat.py:109](../../../apps/backend/app/routers/chat.py) `_persist_usage` |
| 2 | **Metering off `LlmCall` rows is already the house pattern.** The talent-profile refresh limit counts `LlmCall` rows rather than keeping its own table, with the reason written down: "the row is written anyway." | [talent_me.py:37](../../../apps/backend/app/routers/talent_me.py) `REFRESH_LIMIT_PER_HOUR` |
| 3 | **Guests already cannot publish.** `POST /v1/resumes/{id}/publish` and `PUT /v1/resumes/{id}/share` both take `require_user` and then `_require_user_owned`. Requirement 1.1's publish clause needs no backend work — only accurate copy. | [resumes.py:412](../../../apps/backend/app/routers/resumes.py), [shares.py:81](../../../apps/backend/app/routers/shares.py) |
| 4 | **The opt-in already exists and has exactly one meaning.** `talent_introduction` is the only self-serve consent purpose, toggled in settings as "Open to matching", read through `has_consent`. Tier 3 keys off that and nothing else. | [consent.py:26](../../../apps/backend/app/services/consent.py) `SELF_SERVE_PURPOSES`, [settings/page.tsx:73](../../../apps/frontend/app/[locale]/settings/page.tsx) |
| 5 | **The admin dashboard already sums tokens per day and already computes a cache hit rate.** Adding money is a derived column over series that exist. | [admin.py:158](../../../apps/backend/app/routers/admin.py) |
| 6 | **The SSE channel takes additive event types without breaking clients**, by stated contract — `ChatStreamEvent` is a discriminated union and unknown types are inert. A live meter costs one new variant. | [api.ts:416](../../../apps/frontend/lib/api.ts), [docs/superpowers/plans/2026-10-22-chat-token-streaming.md](../plans/2026-10-22-chat-token-streaming.md) |
| 7 | **One chat turn is up to 9 model rounds** — though production says it uses 2–3; see the measurement section, which retracts the "fills its budget" reading. `max_rounds=8` whenever `prefer_full_source or verify_compile` — and the chat router always sets `verify_compile=True` — plus one reserved answer round. The compile-repair loop can then run up to 3 more turns of the same shape. The existing `LIMITS = {"chat": 10}` therefore caps *requests*, not spend: a 10-turn hour can bill anywhere from 10 to ~300 rounds. | [llm.py:810](../../../apps/backend/app/services/llm.py), [llm.py:563](../../../apps/backend/app/services/llm.py), [chat.py:292](../../../apps/backend/app/routers/chat.py), [2026-10-22-chat-reasoning-visibility-design.md](2026-10-22-chat-reasoning-visibility-design.md) |
| 8 | **`purpose='chat' AND round_index=0` is exactly one row per chat turn.** `purpose` starts at `chat` and the repair loop flips it to `repair` before restarting its own round count, and vision rows carry `purpose='vision'`. So a turn *count* is available with no new column. | [chat.py:232](../../../apps/backend/app/routers/chat.py) `purpose = "chat"` → [:304](../../../apps/backend/app/routers/chat.py) `purpose = "repair"` |
| 9 | **Nothing in the compile, preview, export or ATS path calls a model.** `from app.services.llm import` appears in one router. Those paths are CPU, not tokens — which is what makes "editing and exporting stay free, forever, at every tier" a true statement rather than a marketing one. | `grep -rn 'from app.services.llm import' apps/backend/app` → [chat.py:20](../../../apps/backend/app/routers/chat.py) only; [profile.py:43](../../../apps/backend/app/services/profile.py) imports helpers |
| 10 | **The Terms already publish a specific limit**, so this is a Terms edit, not only a feature: *"Guest use is rate-limited to 10 chat turns and 20 PDF exports per hour."* | `legal.terms.sections[].paragraphs` in [messages/en.json](../../../apps/frontend/messages/en.json) |

| 11 | **Cache writes are billed and are not recorded.** `_usage_counts` reads input / cached / output / reasoning and stops, but the provider returns `cache_write_tokens` — a live response in the tree carries 2,069 of them — and terra bills them at 1.25x the uncached input rate. 20–45% of the real bill is invisible to today's columns. | [llm.py:342](../../../apps/backend/app/services/llm.py) `_usage_counts`, [2026-10-22-render-layout-inspection-design.md:282](2026-10-22-render-layout-inspection-design.md) |
| 12 | ~~`_history()` re-sends every message ever written on a resume, unbounded — 63% of a round's input.~~ **Fixed on `main` the same day, in `4db08d1`, from this spec's measurements.** History is now snapped to a 48-message grid: ~4,200 tokens on average, ~6,500 at most, whether the transcript holds 150 rows or 1,500. The fold is grid-aligned rather than sliding precisely because at an 82.6% hit rate a re-sent prefix bills at 10% and a new one at 125%, so sliding would have lost about five to one. See [2026-10-24-chat-history-window-design.md](2026-10-24-chat-history-window-design.md). **This spec's cost figures predate it — see below.** | `4db08d1`, `app/services/chat_window.py` |

### Two attribution holes, found by reading the join

`LlmCall` has `resume_id` and no owner column. The existing refresh limit reaches
the owner by `join(Resume, Resume.id == LlmCall.resume_id)`. That join leaks in
two directions, and both leaks are quota resets:

- **`resume_id` is `ON DELETE SET NULL`.** Delete the resume and its spend
  becomes unattributable. Deleting a resume is a supported, one-click action
  ([ConfirmDeleteDialog.tsx](../../../apps/frontend/components/ConfirmDeleteDialog.tsx)),
  so a cost quota joined through it can be cleared on demand.
- **`/v1/auth/claim` rewrites the owner of past work.** It sets
  `resume.guest_session_id = None` and `resume.user_id = user.id`
  ([auth.py:198](../../../apps/backend/app/routers/auth.py)). Spend a guest
  budget, sign in, and every one of those rounds retroactively joins to the new
  account — charging a fresh signup for the trial it just converted from.

Both are fixed the same way: denormalise `user_id` and `guest_session_id` onto
`LlmCall` at write time, exactly as `DisclosureEvent` already copies in
`recipient_label` rather than reading it through `api_key_id`, and for the same
stated reason — the row has to still answer the question after the thing it
pointed at has moved or gone.

## Why cost, and not turns

The requirement asked whether to meter by API cost "directly". Yes, with one
layer of indirection, for three reasons that Finding 7 makes concrete.

**A turn count does not bound spend.** Two turns on the same resume can differ by
more than 10x: a one-word fix that ends in round 1 against a template switch that
runs 8 rounds at `prefer_full_source`, fails to compile, and drags the repair loop
through three more passes. Counting turns prices those the same, which means the
quota is either too mean for the first or too generous for the second.

**The cost signal is already measured, per round, including the discount.**
`cached_tokens` is recorded, and cached input is an order of magnitude cheaper.
A token count that ignores it overcharges by up to ~8x on a well-cached turn —
and the admin dashboard already graphs the hit rate, so the discount is real and
observed, not theoretical.

**It keeps the product honest in the user's favour.** Metering on cost means a
cheap edit costs the person less of their week. A turn count cannot reward them
for asking a small question.

### But the number shown to people is not dollars

The ledger is integer micro-USD. The number on screen is a **credit**, where

```
1 credit = one reference edit = 70_000 µUSD = $0.07
```

Three reasons the published unit is not the dollar:

1. **Provider prices move; what someone gets should not.** A price cut should
   lower Offerfy's bill, not silently re-denominate a published allowance. The
   peg is a constant in config, changed deliberately.
2. **It does not publish the margin.** "20 credits" is a complete, honest
   statement of what you get. "$2.40 of inference" invites a conversation about
   gross cost that a free Phase 1 product does not need to have.
3. **A job-seeker has no intuition for $0.07 of inference, and a perfect one for
   "about one edit".** Pegging the credit *to an edit* rather than to a round
   number of cents is what makes the tier table readable at a glance.

The editor therefore shows both: the credit bar, and "≈ N edits left", where N
divides the remaining balance by **that person's own trailing mean turn cost**
(falling back to the constant before they have history). Self-calibrating, and it
makes the fairness visible — someone who writes short prompts on a short resume
really does get more edits.

## The price table — verified against the provider, 2026-10-24

From [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing),
cross-checked against two independent aggregators. µUSD per 1M tokens:

| model | input | cached read | **cache write (30m)** | output |
| --- | --- | --- | --- | --- |
| `gpt-5.6-terra` | 2_000_000 | 200_000 | **2_500_000** | 12_000_000 |
| `gpt-6-luna` | 100_000 | 10_000 | 125_000 | 500_000 |

Cache reads are 10% of input; **cache writes are 1.25x input** at the 30-minute
TTL (2.5x at 12h). Terra's input and output both fell 20% on 2026-07-30, so any
figure of $2.50 / $15.00 in the wild is pre-cut and stale — one aggregator still
served those.

Two corrections to what this spec assumed before the prices were checked:

- **cached input $0.20/1M — assumption confirmed.** The 10:1 ratio generalised
  from luna.
- **output $12.00/1M, not $16.00** — the assumption was 33% high. It matters
  less than expected; see below.

And one thing nobody had priced:

> **Cache writes are billed, and `LlmCall` does not record them.**
> `_usage_counts` reads `input_tokens`, `cached_tokens`, `output_tokens` and
> `reasoning_tokens` and stops. The provider *does* return
> `cache_write_tokens` — [2026-10-22-render-layout-inspection-design.md:282](2026-10-22-render-layout-inspection-design.md)
> records a live response carrying `cache_write_tokens: 2069` with
> `ttl_30m_tokens: 2069`, and flags it as sitting on an endpoint "whose pricing
> has no cache line". On terra it has a line, and it is **1.25x the uncached
> input rate** — plausibly the largest single item on the bill. A meter built on
> today's columns would undercount by 20-45%. Storing that column is now part of
> the work.

The two subtleties that are still the only correctness risk in the arithmetic:

- **`cached_tokens` is a subset of `input_tokens`**, not a sibling — the live
  response above shows the cache details nested under `input_tokens_details`.
  Billable input is `(input - cached)` at the input rate **plus** `cached` at the
  cached rate. Summing both at full rate double-charges.
- **`reasoning_tokens` is likewise a subset of `output_tokens`**, under
  `output_tokens_details`. Adding them is double-charging the thinking.

## What one edit actually costs — measured, 2026-10-24

### Read this first: these numbers predate the history fix

**Every cost figure below was measured before `4db08d1` landed on `main`**, and
that commit removed the largest single component of them. The measurement was
taken at ~18:17 Taipei on 2026-10-24; the fix was committed at 19:08 the same
evening, using these very numbers.

What changes: history was ~13,700 of a ~25,943-token round and is now ~4,200,
so input per round drops to **~16,400 tokens**. Re-pricing the same mix at the
same verified rates and the same 82.6% hit rate:

| line | tokens | cost |
| --- | --- | --- |
| fresh input read | ~2,860 | $0.0057 |
| cached input read | ~13,580 | $0.0027 |
| output | 271 | $0.0033 |
| cache write (still unmeasured) | ~2,290 | $0.0057 |
| **per round** | | **~$0.017** (was $0.020–0.038) |
| **per turn**, at the measured 2.57 rounds | | **~$0.045** (was $0.065) |

So the reference edit is now closer to **$0.045 than $0.07**, and
`CREDIT_UUSD = 70_000` is about 55% too high. **The error is in the safe
direction** — a credit buys more than advertised, so the published "about 20
edits" understates what someone actually gets, rather than overstating it.
Nothing is broken and no allowance needs cutting.

**Action: re-run the Task 0 calibration after merging `main`, before deploy,
and re-peg `CREDIT_UUSD`.** The editor's own `approx_edits_left` already
self-corrects, because it divides by each person's trailing rate rather than by
the peg; only the public page's static "about N edits" is affected.

### Read this first: the sample is one person

20 `LlmCall` rows, 7 turns, one resume, three days, one user. The instance holds
**3 users, 111 guest sessions, 246 resumes, 861 chat messages**. Nothing here is
a population estimate and no tier should be set from the *volume*.

What the sample does carry is **structure** — properties of the prompt
architecture rather than of who was typing. Those generalise; the counts do not.
Marked below.

| measured | value | generalises? |
| --- | --- | --- |
| cache hit rate, all rows | **82.6%** | **yes** — a property of a stable prefix re-sent every round. 88-99% within a warm session; the two cold starts in 18 rounds are the only misses. |
| input tokens / round | **avg 25,943** (p50 25,348, max 30,189) | **yes, as a function of history length** — decomposed below |
| output tokens / round | **avg 271** (p90 830) | **yes** — the rounds emit tool calls, not prose |
| reasoning tokens / round | **avg 85** | **yes** — `effort: "low"` is doing what it was set for |
| rounds / turn | avg **2.57**, p50 3, max **3** | **no** — 7 simple turns. The architectural ceiling is 9. |
| turns with a compile repair | **0 / 7** | **no** — n=7 says nothing |
| turns with a vision call | **0 / 7** | **no** |
| source | 9,797-9,834 chars | **no** — that is their resume |

### Where 26,000 input tokens per round come from

This was the biggest surprise, and it is the finding with the most money in it.
Measured against the tree:

| component | tokens | share |
| --- | --- | --- |
| **chat history** — 146 user/assistant rows, 54,630 chars | **~13,700** | **63%** |
| Typst source, from `read_typst` | ~2,460 | 11% |
| `TOOLS` schemas (3,052 chars) | ~760 | 4% |
| `SYSTEM_PROMPT` (2,117 chars) | ~530 | 2% |
| role card, family tool specs, in-turn tool results, reasoning items | ~4,300 | 20% |
| **round 0 of a turn, observed** | **21,693** | |

> **`_history()` re-sends every user and assistant message ever written on a
> resume, on every round of every turn, unbounded.** 146 messages is already
> 13.7k tokens; at 500 it would be ~47k. Per-round cost therefore grows with the
> *lifetime* of a document, not with the size of the edit.

Two mitigations are already in place and are worth noticing before anyone
panics: `tool` rows are filtered out of `messages` and never sent — which is
load-bearing, because this instance holds **1.44M characters of tool traces on a
single resume** against 55k of history — and the 82.6% cache hit means most of
the history is billed at 10%. It is the *cache writes* on it, at 1.25x, that are
not discounted.

### The arithmetic

Per round, from the measured token mix at verified rates:

| line | tokens | rate | cost |
| --- | --- | --- | --- |
| fresh input read | 4,423 | $2.00/1M | $0.0088 |
| cached input read | 21,520 | $0.20/1M | $0.0043 |
| output (reasoning included, not added) | 271 | $12.00/1M | $0.0033 |
| **priced subtotal** | | | **$0.0164** |
| cache write | **unmeasured** | $2.50/1M | **$0.004-$0.022** |
| **per round** | | | **$0.020-$0.038** |

The cache-write band is wide because the column does not exist: the floor
assumes only genuinely new content is written, the ceiling assumes every
cold-round prefix is. It is 20-45% of the round either way, which is why it is
the first thing to measure once the column lands.

| turn shape | rounds | cost |
| --- | --- | --- |
| **measured** | mean 2.57, p50 3 | median **$0.045**, mean **$0.065**, p90 $0.121 |
| reference edit (the mean, rounded — budget math wants the mean) | ~3 | **$0.07** |
| complex — template switch, upload, `prefer_full_source` | 8-9 | **$0.23-$0.34** |
| worst case — complex plus three repair passes | up to 36 | **~$1.00** |

**Reference edit: $0.07.** `CREDIT_UUSD = 70_000`.

### Where the earlier estimate went wrong, and why it nearly didn't matter

The pre-measurement guess was $0.12 — 1.7x high against the reference edit and
2.7x against the measured median — from two large errors that mostly cancelled:

| | estimated | measured | error |
| --- | --- | --- | --- |
| rounds per turn | 9 (the ceiling) | 2.57 | **3.5x too high** |
| input tokens per round | 8,000 | 25,943 | **3.2x too low** |
| cache hit rate | 60% | 82.6% | understated the discount |
| output tokens per round | 400 | 271 | mildly high |
| output price | $16/1M | $12/1M | 33% high |

The "~$0.016 per round" figure this repo states in
[2026-10-22-render-layout-inspection-design.md:149](2026-10-22-render-layout-inspection-design.md)
came from the same 8k-token assumption. Real is $0.020-$0.038 — the number is
roughly right, for the wrong reason: 3x more tokens at an 82.6% discount.

The cancellation is luck and should not be trusted twice. Specifically: **the
assumption that the model fills its round budget is false in production**, so the
cost of a turn is far more sensitive to *which kind* of edit it is than to the
ceiling. See the levers section.

## The three tiers

| | guest | signed in | open to matching |
| --- | --- | --- | --- |
| **AI edits / week** | **4, counted** | ~20 | ~100 |
| **weekly budget** | — (count, not cost) | **20 credits** ($1.40) | **100 credits** ($7.00) |
| burst cap | 4 / hour | 15 / hour | 15 / hour |
| publish a resume | no *(already true)* | yes | yes |
| edit Typst by hand, preview, PDF export, ATS | **unlimited** | unlimited | unlimited |
| window | rolling hour + week | week | week |

**Guests are capped on a turn count, not on cost, on purpose.** Three reasons,
and the mixed unit is worth the inconsistency:

1. At four turns the worst case is $1.36 even if every one of them is a
   complex, full-source turn, so cost precision buys nothing.
2. "4 AI edits to try it out" needs no explanation to someone with no account.
   "0.6 of 4 credits" needs a paragraph.
3. It makes the floor a guarantee. A guest whose first turn is an upload import
   — `prefer_full_source=True`, 8 rounds, the expensive shape — could eat half a
   cost budget before seeing anything. A count cannot do that to them: four
   turns is four turns.

**Why 4 is the right guest number.** Polishing one page to done measures 8–15
turns: an import or create, 3–5 content edits, 2–3 layout fixes, 1–2 ATS
repairs. Tailoring an already-polished master for one role is shorter, 3–6 —
which matches the outside picture of the task, where tailoring runs 15–20 minutes
an application, 68% of candidates spend under 30, and the reported pattern is
targeted revisions against a master rather than fresh drafts. That is Offerfy's
master/tailor model, so the shape transfers. Four turns is ~a third of a first
polish and most of one tailoring pass. The wall lands mid-polish, with a
visibly better resume on screen and the PDF already updating — which is the
"taste the sweet part" requirement, and is also the moment publish is
unavailable anyway (Finding 3), so the account prompt is answering a question
they have just started asking rather than interrupting one.

**Why 20 for signed-in.** It has to cover the whole stated job — finish polishing
one page, or tailor for one specific role — with headroom for the turns that
accomplish nothing. 15 of 20 for a full polish leaves a real margin. This number
is **also a legal requirement, not only a product choice**; see the consent
section.

**Why 100 for opted-in.** 5x, as asked. At 100 turns a week nobody job-hunting
honestly is rationing; the tier's job is to stop being a limit at all for the
people who opted in, while still bounding the blast radius of one bad actor to a
known number. The exposure is in the budget section, and it is the number most
worth arguing about.

### The window: fixed, Monday 00:00 Asia/Taipei

Not a rolling 7 days. Requirement 4 is that this be *easy to understand*, and
"resets Monday morning" is a sentence; "you regain capacity continuously as
individual charges age out" is a support ticket. A fixed window also makes the
meter monotonic — it only ever goes one way between resets — which is what makes
a progress bar readable.

The cost is boundary bursting: someone can spend Sunday's balance and Monday's
back to back. Bounded, by the hourly burst cap and by the per-tier ceiling, and
cheap compared to a meter nobody can read.

Taipei rather than UTC because that is where the users are; a reset at 08:00
local on a Monday is strictly worse than one at midnight. Derived from the clock
on every read — nothing is stored, and no job runs at the boundary.

## Enforcement

**Pre-flight, then let the turn finish.** The quota is checked once, before the
turn starts, and a turn that is allowed to start is allowed to complete. The
alternative — checking between rounds and stopping when the budget runs out —
abandons an edit halfway through a document, which is worse than any overspend it
prevents. The repair loop makes this concrete: cutting off mid-repair leaves a
resume that does not compile.

So the rule is: **start a turn when the balance is positive; finish every turn
that starts; carry the overrun as debt into the same window.** A turn that ends
9 rounds past the line leaves the balance negative, and the next turn is refused
until the window resets. Nothing is clawed back and nothing is hidden — the meter
shows the overrun, because it happened.

**The hourly burst cap is a separate, simpler limit, and it applies to every
tier.** A weekly budget does not stop 100 credits going out in ten minutes, which
is the cost-spike and thundering-herd risk. This is the existing `RateEvent`
mechanism, with the guest-only condition removed and `chat` raised. It stays a
count, because it is an abuse guard and not an allowance.

**Refusals carry structure, not just a 429.** Today the client gets
`detail: "Too many requests"` and nothing to act on. The refusal needs to say
which limit, when it resets, and what the next tier would give — because for a
guest the refusal *is* the signup moment, and for a signed-in user it is the
matching moment. A bare 429 wastes both.

**Dev stays unmetered, and that becomes explicit.** `guest_rate_limits_enabled()`
is `app_env == "production"`, which is right but untestable on staging. The new
check reads a setting that defaults to that expression, so a staging host can
turn it on without pretending to be production.

## Guests can reset their own cookie — and that is the biggest financial risk here

The guest identity is a cookie
([guest.py:10](../../../apps/backend/app/services/guest.py)). Clearing it, or
opening a private window, mints a fresh session with a fresh allowance. A guest
tier is therefore not really 4 edits per guest; it is 4 edits per *act of
clearing a cookie*, which is free and takes one keystroke.

This is the one number in the design that is unbounded, and it has to be
addressed or the guest tier is the hole the budget drains through.

**A second, coarser key counted alongside the cookie.** Remaining is the *minimum*
of what the cookie has left and what the network key has left, with the network
key's allowance set higher (say 12 edits/week) so that shared NAT, a university,
or a café does not lock out honest first-time visitors.

The key is `HMAC-SHA256(ip, server_secret)`, truncated — the same unsalted-hash
reasoning `GuestSession.key_hash` and `TalentApiKey.key_hash` already use, except
that an IP address *is* guessable by enumeration, so this one needs the keyed
HMAC rather than a bare SHA-256, and the secret must not be `auth_token_secret`.

This is personal data, and this codebase holds itself to a standard on that, so
the treatment is part of the design and not an afterthought:

- **Retention is the window plus a little: 14 days, then deleted** by the same
  user timer that runs the ingest ([AGENTS.md §8](../../../AGENTS.md)). A rate
  key that outlives the window it rate-limits has no purpose.
- **The raw IP is never stored**, only the truncated HMAC, and the secret rotates
  — which expires every key that exists, by construction.
- **It goes in the privacy policy**, in the same table that already lists
  `offerfy_guest`, described as what it is: an abuse control on the free tier.
- **It gates nothing but the free tier.** No signed-in path reads it.

**And a global guest ceiling behind it**, because the network key is a speed bump
and not a wall. When guest spend for the week crosses `GUEST_WEEKLY_CEILING`, new
guests get "sign in to keep editing" instead of a refusal — degrading into the
conversion path rather than into an error, which is the failure mode that is
aligned with the business instead of against it.

## Tying quota to the matching opt-in: the constraint that makes it lawful

Tier 3 is "5x your weekly budget for turning on Open to matching". That is an
incentive attached to a consent, and consent has to be **freely given** — GDPR
art. 7(4), and the same principle under Taiwan's PDPA, which the Terms already
choose as governing law. An incentive does not by itself invalidate consent. A
service that is *unusable* without it does.

So the design has one hard constraint, and it is the reason the signed-in tier is
sized the way it is:

> **The signed-in tier must be independently sufficient for the job the product
> advertises** — polish a resume, tailor it for a role, publish it. If 20 credits
> is not genuinely enough for that, the 100-credit tier stops being a thank-you
> and starts being the real product, and the opt-in stops being freely given.

Three rules follow, and they are cheap:

1. **No clawback on withdrawal.** Turning matching off drops the tier at the
   *next* window, never retroactively. A withdrawal that instantly invalidates
   the week's remaining balance is a penalty for withdrawing.
2. **The toggle's copy leads with matching, not with credits.** The quota bonus
   is mentioned second, as a thank-you. The existing label already carries the
   notice itself, deliberately
   ([settings/page.tsx:127](../../../apps/frontend/app/[locale]/settings/page.tsx));
   that note stays true.
3. **Quota writes nothing to the consent ledger.** Tier is derived from current
   consent state via `has_consent`, read at request time. The ledger answers
   what someone agreed to and nothing else — which
   [consent.py](../../../apps/backend/app/services/consent.py) says in so many
   words, and a `quota_bonus` column would quietly contradict.

**Assumption worth confirming:** "opted-in users" is read as
`talent_introduction`, because Finding 4 establishes it is the only opt-in that
exists. If a separate programme was meant, the tier predicate changes and
nothing else in this design does.

## Making it visible

Four surfaces, each answering a different question.

| surface | question | shape |
| --- | --- | --- |
| editor chat panel | "how much have I got left *right now*" | credit bar + "≈ N edits left" + "resets Monday", updated live |
| `GET /v1/usage` | the machine-readable truth | tier, limit, used, remaining, `reset_at`, per-day breakdown |
| settings | "where did my week go" | the week's spend by day, link to the public page |
| `/usage` | "why is there a limit at all" | the public page |
| admin | "what is this costing me" | cost columns on the existing day series, per-tier split, top spenders |

**The live meter is a new SSE event, and it is not the usage event.** `chat.py`
states plainly that usage telemetry is "persisted and never relayed... telemetry
is not the client's business"
([chat.py:239](../../../apps/backend/app/routers/chat.py)). That decision is
correct and stays. The new `quota` event is a different thing with a different
audience: it carries the person's own remaining balance and reset time, and
**never** token counts, model names or dollar figures. Keeping them as two events
is what keeps that comment true.

`GET /v1/usage`, not `/v1/me/usage`: it has to answer for guests too, so it takes
`owner_context(ensure=False)` rather than `require_user`, unlike every `/v1/me/*`
route.

## The public page

`/usage`, in all three locales, built on the existing `LegalDocument` /
`TrustDocument` pattern and registered in `app/sitemap.ts` and the footer.

What it has to say, in this order, because the order is the argument:

1. **What's free, unlimited, at every tier, with no account.** Editing Typst,
   live preview, PDF export, ATS checks, the job catalog. True by Finding 9, and
   it belongs first: the limit is on one feature, not on the product.
2. **Why the AI has a limit.** Every AI edit is a real payment to a model
   provider. Offerfy is free. The limit is what keeps it free rather than
   metered.
3. **The table.** Three tiers, the numbers above, what each unlocks.
4. **What makes an edit cost more or less** — a long resume, a template switch,
   an upload, questions about the rendered page, an edit that fails to compile
   and needs repairing. Each of these is real and each is in Finding 7. This is
   the section that makes the meter feel fair instead of arbitrary.
5. **How we count**, plainly: we add up what the model calls actually billed, we
   don't charge you for the discounted parts twice, cheap edits cost you less.
6. **Matching is a thank-you, not a toll.** Signed-in is enough to finish the
   job; you can turn matching off whenever you like and keep everything you have
   made. This paragraph is the public half of the consent constraint.
7. Reset time, and where to watch the meter.

**The Terms change with it.** The AI section's *"Guest use is rate-limited to 10
chat turns and 20 PDF exports per hour"* becomes wrong on the day this ships
(Finding 10). Replacing it bumps `LEGAL_UPDATED`, which must equal
`POLICY_VERSION` — `tests/test_consent.py` fails on drift, by design
([consent.py:21](../../../apps/backend/app/services/consent.py)) — and
`POLICY_VERSION` is stamped into every future consent row. That coupling is
intended and should be honoured, not worked around: the Terms genuinely changed.

## What this costs, per 100 weekly actives

At the measured $0.07 reference edit, and a 70 guest / 25 signed-in / 5 opted-in
mix. The guest column is costed on *turns*, so its ceiling uses the complex-turn
figure rather than the reference one — a guest who spends all four turns on
uploads is the realistic bad case.

| | ceiling if everyone maxes out | expected (guests ~100%, signed-in ~40%, opted-in ~25% of allowance) |
| --- | --- | --- |
| guests (70 x 4 turns) | $78.40 | $19.60 |
| signed in (25 x 20 credits) | $35.00 | $14.00 |
| opted in (5 x 100 credits) | $35.00 | $8.75 |
| **per week** | **$148** | **$42** |

Two things that table does not capture, and both matter more than its precision:

- **Cookie-reset farming has no upper bound.** 111 guest sessions already exist
  against 3 users, so guests are the population, not the fringe. This is why the
  network key is in scope.
- **Cache writes are 20-45% of every figure above and are not measured.** Treat
  the table as a floor until that column exists.

Two ceilings, both config, both surfaced in admin:

- `GLOBAL_WEEKLY_CEILING` — default **$150**, at the ceiling of the table above.
  Crossing it refuses new AI turns across the board with an honest message.
- `GUEST_WEEKLY_CEILING` — default **$40**. Crossing it converts rather than
  refuses, as above.

A ceiling nobody is told about is an outage.

### How this sits against the market

Published free tiers for AI resume tools cluster at **5-20 AI actions per
month**: ApplyArc 5, ResumeFast 10, Careerkit 10, Teal ~10 bullet credits plus
a few summaries, Canva 20. Against that:

| | per month | vs market free tier |
| --- | --- | --- |
| Offerfy guest, no account | ~17 edits | **at the top of the range** |
| Offerfy signed in | ~87 edits | **4-17x** |
| Offerfy opted in | ~430 edits | far beyond any of them |

Not a like-for-like comparison — a competitor "credit" rewrites one bullet,
while an Offerfy turn reads the document, edits it, recompiles and verifies the
layout, and costs ~26k input tokens against perhaps 1k. One turn is worth several
of their credits and costs more than several.

Two things follow:

1. **These tiers are generous, not stingy**, and the 82.6% cache hit is what pays
   for it. If the guest tier has to be cut, 3 edits is still at market parity.
2. **Weekly beats the market's monthly reset** on exactly this use case. Job
   hunting is bursty; a wasted week costs a week, not a month. Worth saying on
   the public page — it is a real advantage, not spin.

## The two levers that are bigger than the tiers — and the two that are not

Recorded because the first instinct when a bill arrives is to cut allowances, and
because **both levers this spec originally proposed turned out to be spent.**

**Worth doing:**

1. ~~Trim the chat history.~~ **Done, on `main`, in `4db08d1`** — the largest
   cost reduction in the product, taken the same day this spec measured it, and
   worth about a third of a turn. Two details from it are worth carrying
   forward because they generalise: the cache makes the obvious fix backwards
   (a sliding window rewrites more prefix than it saves, five to one), and
   truncation loses exactly one class of thing — a *negative* instruction,
   because something the resume does not do leaves no trace in it to
   rediscover, which is why `propose_memory` now covers never/always rules.
2. **Measure `cache_write_tokens`.** 20-45% of the bill, billed at 1.25x input,
   currently invisible. Cannot be optimised before it is visible. The column is
   one migration.

**Already spent — do not plan around these:**

3. ~~Drop `max_rounds` from 8 to 6.~~ **Retracted.** This spec claimed ~25% off
   every turn, on the strength of
   [2026-10-22-chat-reasoning-visibility-design.md](2026-10-22-chat-reasoning-visibility-design.md)
   recording that "the model expands to fill whatever it is given (8 rounds → 8
   tool calls)". Production says otherwise: the `round_index` histogram over
   every chat row is **0 → 7 turns, 1 → 7 turns, 2 → 4 turns**, and nothing
   deeper. Observed max is 3 rounds against a ceiling of 9. Lowering a ceiling
   nothing reaches saves nothing. The earlier claim was derived from a spec
   sentence rather than from the table, and the table was already there.
4. ~~Chase the cache hit rate.~~ **Largely spent.** It is **82.6%** overall and
   88-99% inside a warm session, against a theoretical 90% ceiling. The two
   misses in 18 rounds are cold starts past the 30-minute TTL. There is nothing
   material left here.

## Rejected

- ~~A new usage ledger.~~ **Reversed 2026-10-24.** The original reasoning was
  that `LlmCall` is written on every billable path already and that metering
  off it is the established pattern (Finding 2), so a second table would mean
  every writer remembering both. That missed the thing that actually matters:
  **`_persist_usage` is wrapped in try/except by design** --
  [chat.py:157](../../../apps/backend/app/routers/chat.py), with the comment
  "telemetry may be lost, the work may not." Correct while `LlmCall` was only
  telemetry; a contradiction the moment a quota reads from it, because a lost
  telemetry row becomes a free turn. A budget cannot have a best-effort write
  as its source of truth.

  Two further costs of deriving on read, both measured against the shipped
  code: one pre-flight is **9-11 queries**, of which the global ceiling is an
  **instance-wide GROUP BY over every row for the week, on every chat turn**;
  and `enforce_chat_usage` reads-then-decides with no lock, so concurrent turns
  can each see the last credit.

  So: `usage_counters`, keyed `(subject_kind, subject_id, window_start)`,
  incremented with an atomic `INSERT .. ON CONFLICT DO UPDATE .. RETURNING`.
  `LlmCall` stays exactly as it is -- per-round, best-effort, for admin and
  cost analysis -- and the two are reconciled, where **disagreement is itself
  the signal**: either telemetry was lost, or something billed without
  metering.
- **A required meter parameter threaded down to the provider call**, which
  would have made recording impossible to forget because the code would not
  run without it. Rejected in favour of a context manager at the call site, on
  the author's call, for three reasons the comparison did grant it: the
  enforcement and the recording stay adjacent and readable in one place; the
  three provider call sites stay independent rather than coupling to a shared
  client abstraction; and it costs ~8 app and ~18 test call sites less churn.

  **What it gives up is the guarantee.** A new caller can skip the wrap exactly
  as `refresh_profiles.py` does today, and the failure is silent -- free turns,
  found from the invoice. That is compensated deliberately rather than
  accepted: a billable-call-site inventory test (Task 12) fails when a new call
  site appears, and the counter/`LlmCall` reconciliation above catches in
  production whatever a grep cannot see. Revisit if a third billable feature
  lands and the inventory test starts being edited rather than read.
- **Metering on turn count for all tiers.** Finding 7: a turn is 1–9 rounds plus
  up to 3 repair passes. A count prices a one-word fix and a failed template
  switch identically.
- **Showing dollars to users.** Publishes the margin, re-denominates the
  allowance whenever the provider changes a price, and means nothing to a
  job-seeker.
- **Checking the budget between rounds.** Abandons an edit mid-document, and
  mid-repair leaves a resume that does not compile. A turn that starts, finishes.
- **A rolling 7-day window.** Harder to explain and makes the bar non-monotonic,
  against requirement 4, for burst protection the hourly cap already provides.
- **Joining to the owner through `resume_id`.** Two documented quota resets:
  resume deletion nulls it, and `/v1/auth/claim` reassigns it.
- **Clawing back the balance when matching is switched off.** Makes withdrawal
  cost something, which is exactly what "freely given" forbids.
- **Blocking guests from the editor until they sign in.** `anonymous stays whole`
  is a standing constraint of the product, and the guest tier is the top of the
  funnel. The limit is on the model, not on the editor.

## Verify before shipping

Updated 2026-10-24, after the calibration pass. Three of the five original items
are closed.

1. ~~Read the real per-turn cost off production.~~ **Done** — $0.045 median,
   $0.07 reference, and the five estimate errors above. The sample is one user,
   so **the structure is trustworthy and the volume is not**: re-read after a
   fortnight of real traffic and expect the round count, the repair rate and the
   source size to move. The credit peg should not move much, because it is a
   function of the token mix rather than of the volume.
2. ~~Confirm terra's output and cached-input prices.~~ **Done** — $12.00/1M
   output (assumption was 33% high), $0.20/1M cached read (confirmed), and
   $2.50/1M cache write (nobody had this).
3. ~~Read the production cache hit rate.~~ **Done** — 82.6%.
4. **Measure `cache_write_tokens`.** The one unmeasured term, 20-45% of the
   bill. Add the column, let it collect a week, then re-peg `CREDIT_UUSD` if the
   band collapses anywhere but the middle.
5. **Confirm "opted-in" means `talent_introduction`.** Still open; it is the only
   opt-in that exists.
6. **Decide the two global ceilings** against a monthly budget this spec does not
   know. Defaults above are derived from the exposure table, not from a budget.
