# Bounding the chat history sent to the model

**Date:** 2026-10-24
**Status:** implemented
**Touches:** `apps/backend/app/services/chat_window.py` (new),
`app/services/llm.py`, `app/routers/chat.py`, `app/services/role_card.py`,
`scripts/history_window_report.py` (new); frontend `lib/api.ts`,
`components/editor/{EditorShell,EditorChatPanel}.tsx`, `messages/{en,zh-TW,zh-CN}.json`

## The finding

`_history()` returned every `ChatMessage` row for a resume and the chat router
sent all the `user`/`assistant` ones as `messages` on every round of every turn.
Nothing truncated, windowed or summarised it.

Measured against production on 2026-10-24: input per model round averaged
**25,943 tokens**, of which **~13,700 (63%) was chat history** — 146 rows,
54,630 characters on one resume. Per-round cost $0.020–$0.038 at verified
`gpt-5.6-terra` rates (input $2.00/1M, cached read $0.20/1M, 30-minute cache
write $2.50/1M, output $12.00/1M).

The shape of the problem matters more than the number: **per-round cost grew
with a document's lifetime, not with the size of the edit.** 146 rows is 13.7k
tokens; 500 would be ~47k; 1,200 would be ~114k.

Two existing mitigations were load-bearing and are unchanged:

1. `tool` rows are never sent. The same instance held **1.44M characters of
   tool traces on one resume** against 55k of history.
2. The prompt cache hit rate is **82.6%** overall, 88–99% within a warm
   session, so most re-sent history bills at 10% of list.

## What the cache does to the obvious answer

Because history is mostly cached, re-sending it is cheap and **changing** it is
what costs: a new prefix bills at 125% of the uncached rate. At these numbers a
window that slides by a message per turn rewrites a ~4,200-token prefix (≈$0.0096)
to save ~$0.0019 — it loses about five to one. "Keep the last N turns" is the
intuitive design and it is the wrong one.

A rolling LLM summary has the same defect plus two more: the summary call is
itself a billed round, and a summary that changes invalidates the prefix it
sits in.

## The design: a stable head, a stepwise cut, a growing tail

`window_history()` sends `head + elision note + tail`:

| Constant | Value | Why |
|---|---|---|
| `HEAD_MESSAGES` | 4 | The opening rows, pinned forever. Normally the brief or the upload instruction. ~375 tokens, cached after turn one. |
| `FOLD_STEP` | 48 | The cut may only land on `4 + n*48`. This is the whole cache story. |
| `TAIL_BUDGET_CHARS` | 18,000 | ~4,500 tokens; ~48 rows at the measured 374 chars/row. A target, not a cap. |
| `TAIL_MIN_MESSAGES` | 16 | Hard floor. Guarantees the current turn's own message survives — an upload puts a base64 image in it. |

The cut is the smallest grid point whose tail fits the budget, capped so the
floor holds. Because it is snapped to a grid counted from the start of the
conversation, **between folds the items are byte-identical and the tail only
grows at the end** — exactly the shape the cache wants. A fold is one prefix
rewrite, and it rewrites the *short* prefix just after a cut, not the long one
just before it, so it costs ~$0.0002/turn amortised.

Nothing is dropped below `4 + 48 + 16 = 68` rows, so short conversations go to
the provider exactly as before. Only long ones change, which is where the cost
is.

### Measured, 1,200 synthetic rows at the production 374 chars/row

| Rows | History tokens, before | After | Cut | Tail rows |
|---|---|---|---|---|
| 68 | 6,431 | 1,996 | 52 | 16 |
| 146 | 13,822 | 4,846 | 100 | 46 |
| 350 | 33,174 | 5,984 | 292 | 58 |
| 500 | 47,404 | 2,000 | 484 | 16 |
| 1,200 | 113,857 | 4,668 | 1,156 | 44 |

Peak history sent at any length 68–1,200 rows: **6,474 tokens**. Prompt-cache
rewrites over the whole 1,200-row life: **24** (~one per 25 turns). At the
measured 146 rows the round goes from ~25,900 input tokens to ~17,000; the
ceiling is the real result.

## The quality bar

Truncation can make a turn lose an earlier instruction, undo settled work, or
re-ask a decided question. Three things carry state forward and none of them
live in the middle of the transcript:

- **The Typst document is the real state**, and `SYSTEM_PROMPT` already tells
  the model to `read_typst` before acting.
- **Durable preferences have their own channel**: `propose_memory` →
  `accepted_memory_texts` → replayed into the role card on *every* turn.
- **The opening rows are pinned.**

The elision is announced rather than hidden, so a model that needs something
from the dropped span is told to ask instead of guessing.

### How it is measured: `scripts/history_window_report.py`

Two numbers per transcript, no API calls:

- **Cost**: tokens before/after, and the fold count over the conversation's
  whole life. The fold count is the one that decides whether this saves
  anything at all; anything near one per turn means the grid is broken.
- **Context**: of the user rows the window drops, how many look durable (a
  quoted string, a proper noun, a year, an always/never), and of those how many
  have **no trace in anything the model can still reach** — the retained
  window, the current Typst source, or accepted memory.

That second number is the quality signal. It is a proxy — it asks "could this
turn still find out?", not "what did the model do?" — and only a replay against
the provider answers the latter. Run it on the host before and after:

```bash
cd /home/ruby0322/offerfy/apps/backend
PYTHONPATH=. scripts/history_window_report.py --busiest 10
```

### What it found, and the one change it caused

On a transcript calibrated to the production shape, 64% of history tokens went
away, 2 cache rewrites over ~73 turns, and exactly one dropped instruction with
no trace anywhere:

> `#30: Never mention the Bernoulli consultancy; drop it everywhere.`

**Negative instructions are the class truncation genuinely loses**, because a
thing the document does *not* do leaves no mark in it to rediscover. Positive
ones ("call me Ada", "headline Mathematician") are reachable through the
document itself.

So the role card now tells the model to call `propose_memory` when the user says
*never* or *always*, and says why: only part of a long conversation is replayed,
accepted memory is replayed in full. That pushes the lossy class into the
lossless channel.

**Residual risk, accepted:** accepted memory needs a signed-in user, so a guest
in a 68+ row conversation has no durable channel for a negative constraint. The
elision note's "ask rather than guess" is all that covers them.

### What was not run

**No experiment was run against the model.** Everything above is either
deterministic logic or a static proxy; nothing here measures what the model
*does* with a trimmed context, and the proxy cannot. The two things that would:

1. **Production replay** — the same last turn with the full history and with
   the window, compared. Not run: production reads were not available to this
   session. `--busiest 10` is the command for it.
2. **A paired A/B over real turns against the live provider** — the design is
   in the next section. Not run: it spends real API budget, which was not
   authorised.

`llm_cache_hit_rate` on `/v1/admin/overview`
(`apps/backend/app/routers/admin.py:187`) plus `input_tokens` on `llm_calls` are
what confirm the cost effect after deploy. **Watch the hit rate for a week:**
the prediction is that it holds near 82.6% and that mean `input_tokens` on
`chat` rows stops tracking transcript length.

### The experiment that would actually answer it

Not built, because it should not be built on guesses about which turns matter.
Sketch, so it can be argued with first:

- **Population**: turns on resumes with >68 user/assistant rows — the only ones
  this change touches. Sample the *last* turn of each, where the window is
  widest.
- **Design**: paired. Replay each turn twice against the live provider, once
  with the full history and once windowed, same source, same seed, same tools,
  tool side effects captured rather than applied.
- **Primary outcome**: does the windowed run's resulting Typst source differ
  from the full-history run's in a way that **loses** something — a field
  removed, a reverted earlier edit, a name or heading changed back. Diff the
  two results against the input source, not against each other, so a merely
  different-but-equivalent edit does not count as a regression.
- **Secondary outcomes**: does the windowed run ask a question the
  full-history run answered from context (re-asking a settled thing), and does
  it call `read_typst` at least as often (the mechanism it is supposed to lean
  on).
- **Bar to ship**: zero losses on the primary outcome. It is a small, bounded
  population; a single undone edit is not an acceptable rate.
- **Cost**: two rounds-worth per turn at ~$0.02–0.04 per round, so a 50-turn
  sample is a few dollars, plus whatever the turns themselves spend on tools.

If you want this run, say so and I will build the harness — it needs the
production transcripts and a budget to spend.

## Telling the user

A bound the user cannot see is a bug report waiting to happen: an assistant
that no longer remembers something from a hundred messages ago reads as broken.
So the trimming is stated, in the place it happens.

To be clear about what this is *not*: **nothing is summarized.** There is no
summarization call and no model-written recap, so there is also no slow step
that could make the chat look stuck — folding is a list slice. The notice
exists for honesty, not for latency.

- `POST /chat` emits `{"type": "context", "dropped": N, "from_message_id": id}`
  as the **first** SSE frame of a turn that trims, before any provider round,
  so the user learns about it as the turn starts rather than inferring it later.
  Nothing is emitted when nothing is trimmed.
- `GET /messages` carries the same thing as `X-Context-Dropped` and
  `X-Context-From` **response headers**. Headers rather than the body because
  the body still has to be the whole transcript in the shape it always had. A
  proxy that strips them degrades to not drawing the marker.
- The editor draws a dashed divider above that message: *"Earlier messages
  aren't sent to the assistant"*, and under it the count, what the assistant
  *does* still get (the start of the chat, everything below, the resume
  itself), that nothing was deleted, and what to do — say it again.
- **A standing one-line notice above the composer**, for as long as the
  condition holds. The divider alone is not a notification: at 150 rows it sits
  ~50 messages up the scroll, and the person sending a message is at the
  bottom and never sees it. The line carries a *Show where* button that scrolls
  the divider into view, so the short notice and the full explanation are one
  gesture apart.

The divider sits at the cut, not at the top, because the pinned head *is* still
sent; the wording says so rather than implying everything above is invisible.

No toast. Folding is a standing condition, not an event — once it starts it
never stops — so a message that fades after four seconds is the wrong shape for
it.

**Verified in a real browser** (2026-10-24), headless Chrome over CDP against a
stub API, desktop 1280x900 and phone 390x844: the divider lands between the
last dropped row and `X-Context-From`, the standing line renders on one line in
the phone sheet without wrapping, and *Show where* scrolls the divider to
centre. See "Seeing it" below.

This is a change to what the UI shows, which the original constraint said to
avoid. It is a deliberate exception, asked for directly, and it preserves the
constraint's intent: nothing disappears, and the row the divider sits above is
the row the window actually kept (asserted in
`test_chat_announces_the_trimmed_context_and_where_it_starts`).

## Constraints honoured

- **Nothing in the transcript is erased.** `chat_messages` rows are untouched,
  `GET /v1/resumes/{id}/messages` still returns every row including `tool`, and
  the UI is unchanged. Covered by
  `test_chat_bounds_the_history_it_sends_without_touching_the_transcript`.
- **`tool` rows are still never sent** — and are now excluded in SQL rather
  than read out of the database and discarded, which stops a 1.44M-character
  read per turn.
- **Reasoning text is still never persisted.**
- **The bound sits at the single provider funnel** (`_history_input` in
  `llm.py`), so the repair loop is bounded by the same rule as the main turn.

## Seeing it

`next dev` cannot run in a worktree against a symlinked `node_modules`
(Turbopack rejects it), and there is no Playwright package — but Playwright's
Chromium *is* cached on the host, and `python3 -m websockets` is importable, so
CDP is enough to drive it:

```bash
cd apps/frontend
cp -al /home/ruby0322/offerfy/apps/frontend/node_modules ./node_modules  # delete after
BACKEND_INTERNAL_URL=http://127.0.0.1:8399 ./node_modules/.bin/next dev --port 3399
```

A stub API on 8399 serving `/v1/resumes/<id>`, `/messages` (with the
`X-Context-*` pair), `/family` and `/memory` is enough to drive the editor.
Two traps cost a cycle each:

- **`/family` must return `tailors`, not `members`** — `ResumeFamilyMenu` does
  `family.tailors.map(...)` and an undefined there kills the whole app with a
  bare "Application error", nothing in the page to say why.
  `--enable-logging=stderr --dump-dom` is what gets the console message out.
- **`--screenshot` lands on the Typst tab.** The chat tab is remembered in
  `sessionStorage` under `offerfy:editor-tab:<id>`, so the page has to be
  loaded, the key set and the page reloaded — which needs CDP, not a flag. On a
  phone width the chat is behind the pull-up sheet, so the *Ask AI* bar has to
  be clicked first.

## Where to look if this misbehaves

- `grep 'chat history folded' journalctl --user -u offerfy-api` — one INFO line
  per turn that actually dropped something, with `dropped`, `kept`,
  `sent_chars`, `total_chars`. No schema change.
- `tests/test_chat_window.py::test_the_prefix_changes_only_at_folds` is the
  test that fails if someone replaces the grid with a plain last-N window.
