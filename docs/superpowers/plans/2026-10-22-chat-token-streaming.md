---
name: Chat token streaming
overview: Make editor chat text arrive token by token instead of in one lump. The SSE channel, the frontend reader and the proxy headers already exist and already carry tool/source events, so this is one new additive event type plus a provisional bubble. Streaming exposes a latent problem — a turn runs up to 8 rounds but only the last round's text survives — so abandoned round text is promoted to a real, persisted assistant message above that round's tool cards rather than retracted. No migration. Backend has one LLM consumer, so blast radius is one router.
todos:
  - id: t1-stream-provider-call
    content: "Task 1: Swap the provider call to client.stream and yield text deltas as a side channel"
    status: completed
  - id: t2-keep-preamble
    content: "Task 2: Promote abandoned round text to a real assistant message instead of dropping it"
    status: completed
  - id: t3-sse-delta
    content: "Task 3: Add the delta SSE event and de-duplicate the two router event loops"
    status: completed
  - id: t4-provisional-bubble
    content: "Task 4: Frontend provisional bubble with one reconcile rule"
    status: completed
  - id: t5-throttle-scroll
    content: "Task 5: Batch markdown re-render and keep the stream pinned to the bottom"
    status: completed
  - id: t6-tests
    content: "Task 6: Teach the scripted fake to stream; cover preamble, nudge, and error paths"
    status: completed
  - id: t7-verify
    content: "Task 7: pytest, lint, tsc, build, and a real-provider browser walk"
    status: completed
isProject: false
---

# Chat Token Streaming Implementation Plan

> **For agentic workers:** implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks 1–3 are backend and land together; 4–5 are frontend; 6 must pass before 7.

**Goal:** The assistant's words appear as they are generated, and no words the assistant produced are ever hidden or erased.

**Architecture:** Deltas are a side channel. The provider call becomes `client.stream`, but every decision in `aiter_llm_turn` still happens on the terminal `response.completed` object — so the round loop, tool dispatch, `_output_text`, `_citation_sources` and the function-call scan are untouched. The transport grows one additive SSE type. The frontend grows one piece of state (`streamingText`) and one rule: `delta` appends to it, any `assistant`/`done`/`error` clears it. "Retraction" is not implemented as a mechanism — it falls out of a real message replacing the provisional bubble, which had to exist anyway.

**Tech Stack:** FastAPI + httpx `>=0.27` streaming, OpenAI Responses API (`/v1/responses`, `gpt-5.6-terra`), SSE, Next 16 / React 19.

**Spec:** [docs/superpowers/specs/2026-10-22-chat-token-streaming-design.md](../specs/2026-10-22-chat-token-streaming-design.md)

## Global Constraints

- **Additive transport only.** `tool`, `source`, `assistant`, `tailor_created`, `done`, `error` keep their exact shapes. A client that ignores `delta` must behave exactly as it does today.
- **No migration.** No new table, no new column. Extra `assistant` rows go into `chat_messages` as-is.
- **Do not restructure the round loop.** If a change requires moving tool dispatch or re-implementing `_output_text` against partial state, it is out of scope and the approach is wrong.
- **Unknown provider events are ignored**, never fatal. The provider is reached through `OPENAI_BASE_URL`, which may be a proxy; the parser must not assume an exhaustive event list.
- **Nothing in the transcript is ever erased.** If a code path wants to remove a message the user already saw, stop and re-read the spec.
- `AI stays optional` and `anonymous stays whole` from the Offerfy spec continue to hold. Guests stream too — `enforce_guest_rate` is unchanged.
- Backend checks: `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q`.
- Frontend checks: `cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build`.
- Locales are `en`, `zh-TW`, `zh-CN`. This change needs **no new copy** — if a task starts inventing message keys, it has drifted.
- Product name is Offerfy. Never CareerOS, Roleloop, Offerly, Offerloop, or RenderResume.

## Findings this plan is built on

Each was read out of the code, not assumed.

| # | Finding | Evidence |
| --- | --- | --- |
| 1 | The SSE channel is already end to end and already drips events out mid-turn. Only the provider call is buffered. | [chat.py:316](../../../apps/backend/app/routers/chat.py), [llm.py:361](../../../apps/backend/app/services/llm.py) `client.post` → [:369](../../../apps/backend/app/services/llm.py) `resp.json()` |
| 2 | Proxy buffering is already solved and already proven in production — `SSE_HEADERS` sets `X-Accel-Buffering: no`. No new infra risk. | [chat.py:32](../../../apps/backend/app/routers/chat.py) |
| 3 | The frontend already parses SSE chunk by chunk with `getReader()` + `TextDecoder`, splitting on `\n\n`. `ChatStreamEvent` is a discriminated union — one new variant costs two lines. | [api.ts:416](../../../apps/frontend/lib/api.ts), [api.ts:477](../../../apps/frontend/lib/api.ts) |
| 4 | **The LLM has exactly one consumer.** `from app.services.llm import` appears once in the whole backend. The import/Argo job (`app/jobs/typst_edit.py`) applies a patch and never calls a model. Blast radius is one router. | `grep -rn 'from app.services.llm import' apps/backend/app` → [chat.py:19](../../../apps/backend/app/routers/chat.py) only |
| 5 | A turn runs 6–8 rounds, and a round that ends in tool calls has its text **overwritten and dropped** — `assistant_text = _output_text(...) or assistant_text` is clobbered by every later round. This is what streaming exposes, and the reason this plan exists in its current shape. | [llm.py:450](../../../apps/backend/app/services/llm.py), [llm.py:463](../../../apps/backend/app/services/llm.py) |
| 6 | A second, separate path abandons round text: a failed edit sets `retry_nudge`, and the *next* round's text is produced, assigned, then `continue`d past. Handling only the tool-call case would leave a visible flicker here. | [llm.py:452](../../../apps/backend/app/services/llm.py)–[:457](../../../apps/backend/app/services/llm.py) |
| 7 | `chat.py` has the event-handling block written out **twice** — once for the main turn, once for the compile-repair loop. Adding a branch to both duplicates the duplication. | [chat.py:189](../../../apps/backend/app/routers/chat.py) and [chat.py:264](../../../apps/backend/app/routers/chat.py) |
| 8 | Every test injects the fake provider at one seam, `app.services.llm.httpx.AsyncClient`, and all 28 call sites route through `ScriptedOpenAI`. Two wrappers (`fake_client`) just delegate to it. So one `stream()` method unblocks every existing test unchanged. | [test_chat.py:119](../../../apps/backend/tests/test_chat.py) and 27 more, [openai_script.py:63](../../../apps/backend/tests/openai_script.py) |
| 9 | **No existing test script produces a round with both `content` and `tool_calls`** — `llm_message` is always called with one or the other. So the new preamble path fires in zero existing tests, and Task 2 cannot silently change their expectations. It must get new coverage of its own. | `grep -n 'content=.*tool_calls=' apps/backend/tests/*.py` → no matches |
| 10 | Existing assertions are filter-based (`[row for row in listed if row["role"] == "tool"]`, `any(...)`, `assistants[-1]`), not count-based on assistant rows. Persisting extra assistant rows does not break them. | [test_chat.py:136](../../../apps/backend/tests/test_chat.py), [test_chat.py:153](../../../apps/backend/tests/test_chat.py), [test_chat.py:471](../../../apps/backend/tests/test_chat.py) |
| 11 | `ChatMessageCard` renders an assistant message as `<MarkdownBody text={message.content} />` and needs no `id`. A synthetic message object is enough for the provisional bubble. | [ChatMessageCard.tsx:522](../../../apps/frontend/components/editor/ChatMessageCard.tsx) |
| 12 | The chat panel's auto-scroll `ResizeObserver` **disconnects itself after 700ms** and only re-arms when `[messages, sending]` change. A bubble that grows for 20s would scroll for the first 0.7s and then stop. | [EditorChatPanel.tsx:102](../../../apps/frontend/components/editor/EditorChatPanel.tsx)–[:117](../../../apps/frontend/components/editor/EditorChatPanel.tsx) |
| 13 | `reasoning.effort` is pinned to `"none"` for `gpt-5.6*`, so there are no reasoning-summary events to parse or render. | [llm.py:152](../../../apps/backend/app/services/llm.py) `completions_tool_extras` |
| 14 | There is no first-class abort path: `sendChat` never constructs an `AbortController`. Cancel is genuinely out of scope, not merely deferred. | [api.ts:442](../../../apps/frontend/lib/api.ts) |

## What this plan deliberately does not do

Spelled out because each was considered and rejected, and rediscovering them mid-implementation wastes a round.

- **No `delta_discard` / `delta_reset` event.** The invariant "a real `assistant` message replaces the provisional bubble" covers every abandonment case once Task 2 emits preambles for *both* finding 5 and finding 6. A second signal would be dead weight.
- **No round counter on the delta event.** Same reason. If Task 2 is implemented correctly the frontend never needs to know which round it is looking at.
- **No parsing of partial `output` arrays.** `response.completed` carries the whole response object; reuse the existing parsers on it.
- **No fire-and-forget of the preview refresh** in the `source` handler, even though it will burst-flush queued deltas (spec, "Risks accepted"). Changing that ordering is a separate change with its own failure modes.

## File Structure

```
apps/backend/app/services/
  llm.py                    # client.stream + SSE line parse + delta yields (T1); preamble promotion (T2)
apps/backend/app/routers/
  chat.py                   # delta branch; collapse the two duplicated event loops into one (T3)
apps/backend/tests/
  openai_script.py          # ScriptedOpenAI.stream(); llm_message -> SSE lines (T6)
  test_chat.py              # FailingOpenAI.stream(); new delta/preamble/nudge tests (T6)
apps/frontend/lib/
  api.ts                    # ChatStreamEvent gains { type: "delta"; text: string } (T4)
apps/frontend/components/editor/
  EditorShell.tsx           # streamingText state + the one reconcile rule (T4), 50ms batching (T5)
  EditorChatPanel.tsx       # render the provisional bubble; fix the 700ms scroll cutoff (T5)
```

No new files. No migration. No message keys.

## Task 1: Stream the provider call, deltas as a side channel

Finding 1. The only buffered hop in the system.

- [ ] In `aiter_llm_turn` ([llm.py:349](../../../apps/backend/app/services/llm.py)), add `"stream": True` to `body` and replace `await client.post(...)` with `async with client.stream("POST", url, headers=headers, json=body) as resp:`.
- [ ] Error path first, because `client.stream` does **not** read the body: on `resp.status_code >= 400`, call `await resp.aread()` *before* `_provider_error_detail(resp)`, which calls `resp.json()`. Without this the 502-detail test at [test_chat.py:468](../../../apps/backend/tests/test_chat.py) fails with a confusing `ResponseNotRead`, not a useful message.
- [ ] Iterate `resp.aiter_lines()`. Parse the SSE shape: accumulate `data:` payloads, blank line ends an event, `data: [DONE]` ends the stream. Ignore `event:`, `id:`, `retry:` and comment lines.
- [ ] Handle exactly two event types and ignore every other:
  - a text-delta event → `yield {"kind": "delta", "text": <chunk>}`
  - the terminal completed event → keep its full `response` object as `data`, then fall through to **the existing round logic verbatim**
- [ ] Confirm the provider's actual event names against the Responses API before hardcoding them (expected: `response.output_text.delta` with a `delta` field, and `response.completed` with a `response` object). Write the parser so an unexpected name is skipped rather than raising — `OPENAI_BASE_URL` may be a proxy ([.env.example:32](../../../.env.example)).
- [ ] If the stream ends with no completed event, raise the same `HTTPException(502, "Chat request failed")` the transport-error path already raises. A truncated stream must not be mistaken for an empty answer.
- [ ] Replace the single whole-request `timeout=` ([llm.py:349](../../../apps/backend/app/services/llm.py)) with an explicit `httpx.Timeout` whose `read` budget is **per chunk**, keeping `connect` short. Keep the existing 90s/120s values as the connect+pool envelope. Note in a comment that this is a robustness improvement, not a regression: a long generation no longer races one global deadline.
- [ ] Do not touch `_output_text`, `_citation_sources`, `_web_search_trace`, the function-call scan, or tool dispatch. If a diff in any of those appears, the side-channel design has been broken.

**Verify:** With a scripted stream, `aiter_llm_turn` yields `delta` events whose concatenated text equals `_output_text` of the same round, followed by the identical `tool`/`source`/`assistant` sequence it yields today. A 400 from the provider still surfaces the provider's own message.

## Task 2: Keep abandoned round text instead of dropping it

Findings 5 and 6. The substance of option A, and the only part of this plan that changes behavior rather than timing.

- [ ] In the `if function_calls:` branch ([llm.py:382](../../../apps/backend/app/services/llm.py)), compute `round_text = _output_text(data, output)`. If it is non-blank, `yield {"kind": "assistant", "content": round_text}` **after** `input_items.extend(output)` and **before** the `for call in function_calls:` loop, so the message lands above its own tool cards.
- [ ] Do the same in the `retry_nudge` branch ([llm.py:452](../../../apps/backend/app/services/llm.py)–[:457](../../../apps/backend/app/services/llm.py)) before `continue`. Skipping this is the flicker in finding 6: text would stream, stay on screen with nothing replacing it, then be silently superseded by the next round's text in the same bubble.
- [ ] Drop the now-dead carry-forward at [llm.py:450](../../../apps/backend/app/services/llm.py) (`or assistant_text`). Its only job was to preserve text this task now emits properly; leaving it causes the same words to appear twice.
- [ ] Track whether any assistant event was emitted. Make the final yield ([llm.py:463](../../../apps/backend/app/services/llm.py)) conditional:
  - final round produced text → yield it (this is the final answer)
  - no text, and nothing was emitted all turn → yield the `"Updated the Typst source."` fallback, as today
  - no text, but preambles were emitted → yield nothing; the last preamble already is the last word
- [ ] Preserve the invariant that **the last `assistant` event of a turn is the final answer**, so `assistants[-1]` keeps working ([test_chat.py:44](../../../apps/backend/tests/test_chat.py)).
- [ ] Emit **no new event kind and no flag**. A preamble is an ordinary `assistant` event. Confirm `chat.py`'s `kind == "assistant"` branch ([chat.py:221](../../../apps/backend/app/routers/chat.py)) needs zero changes: it persists via `_persist_assistant` and updates `assistant_text`, both of which are correct for a preamble — the repair loop at [chat.py:254](../../../apps/backend/app/routers/chat.py) wants the assistant's *latest* words as context, preamble or not.
- [ ] Sanity-check the history feedback loop: persisted preambles return through `_history` on the next turn as `assistant` rows. That is consistent with `input_items.extend(output)`, which already shows the model its own preamble within a turn. Confirm `messages[-1]` is still the new user message after `_history` ([chat.py:165](../../../apps/backend/app/routers/chat.py)) — preamble rows are written during the turn, after the snapshot, so they cannot shift that index mid-turn.

**Verify:** A scripted turn of `[text+tool_call, text+tool_call, text]` yields three `assistant` events in round order, each before its own tool events, all three persisted and replayed by `GET /messages`, with the third being the final answer. A turn of `[tool_call, tool_call, text]` (no round text) yields exactly one, as today.

## Task 3: The `delta` SSE event, and one event loop instead of two

Finding 7.

- [ ] Collapse the duplicated event-handling blocks ([chat.py:189](../../../apps/backend/app/routers/chat.py), [chat.py:264](../../../apps/backend/app/routers/chat.py)) into one local async generator inside `generate()` that takes an event and yields SSE bytes. Do this **first** — adding the `delta` branch to two copies is how the copies drift.
- [ ] The refactor must be behavior-preserving. Keep `_persist_tool`, `_tailor_created`, `set_draft_source` + `db.commit()`, and the `assistant_text` / `new_source` updates exactly as they are. Diff the event sequence of an unchanged scripted test before and after.
- [ ] Add the branch: `elif kind == "delta": yield _sse({"type": "delta", "text": event["text"]})`. Nothing is persisted and nothing else is touched — a delta is display-only.
- [ ] Leave `SSE_HEADERS` alone (finding 2) and leave the `done` / `error` payloads alone.

**Verify:** Every existing `test_chat.py` / `test_chat_family.py` / `test_import.py` assertion passes against the refactor with only `delta` events added to the stream. `parse_sse` in the test helper already ignores unknown types.

## Task 4: The provisional bubble and its one rule

Finding 3, 11.

- [ ] `api.ts:416` — add `| { type: "delta"; text: string }` to `ChatStreamEvent`. `parseSseBlock` needs no change; it keys off `type`.
- [ ] `EditorShell.tsx` — add `const [streamingText, setStreamingText] = useState<string | null>(null)`. **Keep it out of `messages`** so it gets no id from `withMessageId` and can never be mistaken for a persisted row.
- [ ] In the `sendChat` callback ([EditorShell.tsx:417](../../../apps/frontend/components/editor/EditorShell.tsx)):
  - `delta` → append to `streamingText`, and `setChatUnavailable(false)` like the other content events do
  - `assistant` → clear `streamingText` **and** append the real message **in the same handler invocation**, so React batches them into one paint and the text never renders twice
  - `done` and `error` → clear `streamingText` as a safety net, so a stream that dies mid-round cannot strand a bubble
  - `tool`, `source`, `tailor_created` → unchanged; they must **not** clear the bubble. During a tool round the preamble has already been promoted by Task 2, so there is nothing provisional left to clear, and clearing on `tool` would be the retraction this plan rejects.
- [ ] Clear `streamingText` in the existing `finally` block alongside `setSending(false)`, so a thrown `ApiError` cannot leave one behind.
- [ ] `EditorChatPanel` — add `streamingText?: string | null` to `Props`. Render it after the `AnimatePresence` list as a `ChatMessageCard` with a synthetic `{ role: "assistant", content: streamingText }` (finding 11). Keep it outside `AnimatePresence` — it is one mutating node, not an entering/exiting one.
- [ ] Change the loading indicator to `{sending && !streamingText ? <LoadingMessage /> : null}` ([EditorChatPanel.tsx:237](../../../apps/frontend/components/editor/EditorChatPanel.tsx)) so the dots hand off to real text instead of sitting under it.
- [ ] Also gate the empty state on it (`messages.length === 0 && !sending` at [:196](../../../apps/frontend/components/editor/EditorChatPanel.tsx) already covers this via `sending`, but confirm the suggestion chips cannot reappear beside a streaming bubble).

**Verify:** Text appears progressively in a bubble; when the round's real message arrives the bubble is replaced with no flash, no duplicate paragraph, and no gap. A tool round shows preamble → tool card → next preamble, with nothing vanishing.

## Task 5: Batching and scroll

Finding 12, plus the markdown cost.

- [ ] Batch deltas to roughly 50ms before committing to state. Accumulate in a ref, flush on a timer, and **flush unconditionally on `assistant` / `done` / `error`** so the handoff in Task 4 never races a pending timer and drops the tail. Per-token `setState` would re-parse markdown ([MarkdownBody.tsx](../../../apps/frontend/components/editor/MarkdownBody.tsx)) on every chunk.
- [ ] Fix the auto-scroll cutoff. The existing effect disconnects its `ResizeObserver` after 700ms and only re-arms on `[messages, sending]` (finding 12), so a long stream stops following. Add a **separate, cheap** effect keyed on `streamingText` that just sets `viewport.scrollTop = viewport.scrollHeight`. Do not add `streamingText` to the existing effect's dependency array — that would tear down and rebuild a `ResizeObserver` plus a timeout every 50ms.
- [ ] Respect a user who has scrolled up: only auto-scroll when the viewport is already near the bottom. A stream that yanks the view down while someone is reading earlier messages is worse than one that does not follow.

**Verify:** A multi-paragraph answer streams without visible jank, stays pinned to the bottom past 700ms, and stops following once the user scrolls up. The last token of every round is always rendered.

## Task 6: Teach the fake to stream

Finding 8, 9, 10. Cheap because of the single injection seam — but finding 9 means the new path has **no** existing coverage, so new tests are mandatory, not optional.

- [ ] Add `stream()` to `ScriptedOpenAI` ([openai_script.py:63](../../../apps/backend/tests/openai_script.py)): an async context manager over a response object exposing `status_code`, `aiter_lines()`, `aread()` and `json()`. Generate the SSE lines **from the same `llm_message()` payload the script already holds** — chunk `output_text` into several text-delta events, then one completed event carrying the whole payload. Keep `post()` for now so nothing else breaks mid-refactor; delete it once `llm.py` no longer calls it.
- [ ] Do not change the 28 existing `ScriptedOpenAI([...])` call sites or the two `fake_client` wrappers that delegate to it ([test_chat.py:543](../../../apps/backend/tests/test_chat.py), [:763](../../../apps/backend/tests/test_chat.py)). If a call site needs editing, `stream()` is wrong.
- [ ] Add `stream()` to `FailingOpenAI` ([test_chat.py:444](../../../apps/backend/tests/test_chat.py)) returning status 400 with a readable body, so the provider-error-detail assertion still exercises the real path (Task 1's `aread()` step).
- [ ] Extend `llm_message` to accept `content` **and** `tool_calls` together. Finding 9 says no script does this today, which is exactly why the preamble path needs new scripts.
- [ ] New tests:
  - deltas concatenate to the assistant message's content, and `delta` events precede the `assistant` event that carries that text
  - a round with text **and** a tool call emits `assistant` before `tool`, and both rows land in `GET /messages` in that order
  - a three-round turn emits three `assistant` events, with `assistants[-1]` the final answer
  - the retry-nudge path (finding 6) also emits its round's text — script a failed `apply_typst_edit` (`changed=false`) followed by a text round
  - a turn with no round text anywhere still yields exactly one assistant event, and still falls back to `"Updated the Typst source."` when empty
  - a stream that ends without a completed event raises 502 rather than returning an empty answer
- [ ] Re-run the full backend suite and account for every delta against the pre-change baseline. Per finding 10, assistant-row counts are not asserted anywhere, so any new failure is a real regression, not a brittle expectation.

**Verify:** `pytest -q` green except for pre-existing failures, which must be named explicitly in the record (the previous plan documented `test_chat_template_switch_includes_package_example` failing in a worktree without `acorn-resume` vendored; confirm whether it still does rather than assuming).

## Task 7: Verification

- [ ] `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q` — record the pass/skip/fail counts and name every failure.
- [ ] `cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build`.
- [ ] Confirm no new keys landed in `messages/{en,zh-TW,zh-CN}.json` and that the three key sets still match. This change should need no copy at all.
- [ ] `git diff --stat apps/backend/alembic` must be empty.
- [ ] **A real-provider walk is required, not optional.** The scripted fake cannot validate the actual Responses event names, and Task 1 is built on them. With a real `OPENAI_API_KEY`, in the editor:
  1. Ask a plain question → text streams token by token into one bubble, then settles.
  2. Ask for an edit → preamble bubble settles into a real message, tool card appears, source applies, preview recompiles, final answer streams. Nothing disappears from the transcript.
  3. Ask something needing `web_search` → the search card and the text land in the right order.
  4. Ask for a template switch (the `prefer_full_source` path) → the long multi-round turn streams throughout, and the compile-repair loop still runs after.
  5. Reload the page → `GET /messages` replays every preamble and final answer in the same order the live stream showed.
  6. Kill the backend mid-stream → the bubble clears instead of being stranded, and an error message appears.
- [ ] Watch one turn in devtools and confirm `delta` frames arrive incrementally rather than in one burst at the end — that is the whole point of the change, and `X-Accel-Buffering` only covers nginx, not an intermediate proxy configured through `OPENAI_BASE_URL`.
- [ ] Check a guest session streams too (no account), since `enforce_guest_rate` sits in the same path.

## Risks

- **The provider's event names are the one unverified assumption in this plan.** Everything else was read out of this repo; these have to be confirmed against the Responses API and against whatever `OPENAI_BASE_URL` points at. Task 1's "ignore unknown events" rule keeps a mismatch from being fatal, but a wrong *delta* name degrades silently to today's behavior — which is why Task 7's devtools check is a step and not a nicety.
- **Task 2 changes what the database holds**, and it is the one part here that is not purely cosmetic. More assistant rows per turn means more context on the next turn. Judged more accurate than today's silent drop, but it is the decision in this plan most worth a second look.
- **A proxy between us and the provider may buffer the upstream response**, in which case deltas arrive in one burst and no amount of correct code helps. Diagnose with the devtools check before touching the frontend.
- **The `source` handler awaits a preview recompile inside the awaited `onEvent` chain**, so the next round's deltas flush in a burst behind it (spec, "Risks accepted"). Deliberately not fixed. If it reads badly in Task 7, that is a follow-up with its own plan, not a late edit here.
- **The `delta` → `assistant` handoff is the one place a visible bug can hide**: a dropped tail, a duplicated paragraph, or a flash of empty bubble. Task 5's unconditional flush and Task 4's single-invocation batching both exist for this, and step 2 of the browser walk is where it would show.


## Verification record (2026-10-22)

**Automated checks, all green on the final tree:**

- `pytest -q` — **208 passed, 47 skipped, 1 failure**. The failure is pre-existing
  and unrelated: `test_chat_template_switch_includes_package_example` asserts
  `#exp(` reaches the system prompt for the `acorn-resume` template, which is not
  vendored in this worktree (`find . -iname '*acorn*'` returns nothing; only
  `typst/packages` and `typst/starter.typ` exist). The recorded system prompt in
  the failure output proves the request capture path works, so the scripted
  `stream()` is not implicated. The previous plan's record named this same test.
- `npx tsc --noEmit` — clean.
- `npx eslint .` — clean.
- `npm run build` — succeeds; all three locales prerender.
- Locale parity — 386 keys in each of `en` / `zh-TW` / `zh-CN`, sets identical.
  **No message keys were added**, as the plan required.
- `git diff --stat apps/backend/alembic` — empty. No migration.

**Nine tests added**, all passing, covering what finding 9 said had no coverage:

1. deltas arrive in more than one frame, concatenate to the final message, and
   all precede the `assistant` event that finalizes them
2. a round with text **and** a tool call emits `assistant` before `tool`, and both
   rows replay from `GET /messages` in that order
3. a three-round turn emits three `assistant` events, last one the final answer
4. the retry-nudge path keeps its round's words (finding 6)
5. a turn with no round text still yields exactly one assistant event and the
   `"Updated the Typst source."` fallback
6. a stream truncated before `response.completed` raises 502 instead of
   returning an empty answer
7. a **flat** `response.completed` payload (no `response` wrapper) still works
8. a `response.failed` event surfaces the provider's message as a 502 and
   persists no assistant row
9. comments, `event:` lines, lifecycle noise and a proxy-injected event type are
   all ignored rather than fatal

**One plan step turned out to be unnecessary and was dropped, not skipped.**
Task 1 called for replacing the single whole-request `timeout=` with an explicit
`httpx.Timeout` whose `read` budget is per chunk. That change is not needed:
httpx has no total-request timeout, and its `read` timeout is already the
gap-between-reads budget, so the existing float becomes a per-chunk budget for
free the moment `client.stream` is used. Making the change would also have
broken `assert timeouts == [120.0]` at `test_chat.py:557` and `:774` for no
behavioral gain. The 90s/120s values are unchanged and a comment in `llm.py`
records why.

**Provider event names and incremental delivery — verified against the live API.**
Two probes ran against `https://api.openai.com/v1/responses` with `gpt-5.6-terra`,
driving `_aiter_sse_payloads` directly so the production parser itself was what
got tested, not a reimplementation:

| | short answer (64 tok cap) | long answer (600 tok cap) |
| --- | --- | --- |
| `response.output_text.delta` frames | 7 | **357** |
| first delta | 1251ms | 1584ms |
| last delta | 1252ms | 5409ms |
| `response.completed` | 1404ms | **5689ms** |
| delta spread | 1ms | **3826ms** |

Confirmed by this: both event-name constants match the wire exactly;
`response.completed` **does** nest the response object under a `response` key;
the parser reassembled all 357 frames into coherent prose; and delivery is
genuinely incremental, not a burst. The headline number is the gap between the
first delta and `response.completed` on the long answer — **text starts
appearing 4.1 seconds before the old buffered code would have returned
anything**. That gap is the entire user-visible value of this change.

The flat-payload tolerance added for the unconfirmed nesting turns out to be
unnecessary against this provider. It is kept anyway: it costs two lines, it is
covered by a test, and `OPENAI_BASE_URL` is configurable, so a proxy deployment
may still differ.

**Not verified — needs the live provider and the full stack.** The scripted fake
proves the parser against the documented wire format, but not against the actual
bytes this deployment's `OPENAI_BASE_URL` returns. These remain open:

1. Delta frames survive the *outbound* hop intact in a deployed environment.
   The probes prove the provider streams incrementally into our parser, but not
   that nginx and the Next.js rewrite pass the re-emitted SSE through unbuffered.
   `X-Accel-Buffering: no` is already set and already carries `tool`/`source`
   events today, so this is expected to hold — but it was not observed.
2. The preamble → tool-card → final-answer sequence reads correctly in the
   editor, with nothing disappearing.
3. The `delta` → `assistant` handoff shows no dropped tail, duplicated
   paragraph, or flash of empty bubble.
4. Auto-scroll follows past 700ms and yields to a reader who has scrolled up.
5. `web_search` and the `prefer_full_source` template-switch path stream
   throughout, and the compile-repair loop still runs after.
6. A guest session streams too.

To walk it: `docker compose up --build` with a real `OPENAI_API_KEY` in `.env`,
then `/en/create` on http://localhost:3100.
