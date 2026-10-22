# Chat token streaming (preamble kept, not retracted)

**Date:** 2026-10-22
**Status:** approved in companion (option A — keep the preamble, persist it)

## Goal

The editor chat already streams *events* (tool cards, source applies) over SSE. It does not stream *text*: the assistant's words land in one lump because `aiter_llm_turn` does a buffered `client.post` and reads `resp.json()`. Make the words arrive token by token, without changing what the turn does or what the database holds at the end of it.

Second, fix a thing streaming exposes. A turn runs up to 6–8 rounds. Today only the **last** round's text survives — a round that ends in a tool call has its text overwritten and silently dropped. Once text streams, those dropped words become visible, so the turn has to decide what they are. They are kept: an abandoned round's text becomes a real assistant message sitting above that round's tool cards, and it is persisted.

## Locked

- Text streams as a new SSE event `{"type": "delta", "text": "..."}`. Additive; every existing event type keeps its shape and meaning.
- The provider call becomes `client.stream`, but **all decisions still happen on the terminal `response.completed` object**. Deltas are a pure side channel. The round loop, tool dispatch, `_output_text`, `_citation_sources` and the function-call scan are not restructured.
- Unknown provider event types are ignored, never fatal.
- **No retraction.** A round's text is never erased from the transcript. When a round turns out to end in tool calls (or in a retry nudge), its text is emitted as an ordinary `assistant` event *before* that round's tool events, and persisted by the existing `_persist_assistant`.
- Therefore the frontend has exactly one reconcile rule: `delta` appends to a provisional bubble; **any** `assistant` event clears the provisional bubble and appends the real message. `done` and `error` also clear it. There is no discard event, no round counter, no "that didn't count" signal.
- The provisional bubble lives in its own `streamingText` state, not inside `messages`. It gets no id and can never be persisted by accident.
- A turn may now write more than one `assistant` row. `GET /messages` replays them in order; the next turn's history carries them. This is consistent with `llm.py`'s existing `input_items.extend(output)`, which already shows the model its own preamble inside the same turn.
- Exactly one `assistant` event carries the final answer, and it is the last one. `assistants[-1]` stays the final answer for any consumer that reads it that way.
- The fallback `"Updated the Typst source."` fires only when a turn would otherwise emit no assistant text at all.
- Markdown re-render is batched (~50ms), not per token.
- Timeouts move from one whole-request budget to an inter-chunk read budget.

## Non-goals

- Streaming tool-call arguments. Tool cards keep appearing whole, after the tool runs.
- Streaming the `source` event or incremental Typst application. An edit still lands once, complete.
- Reasoning/thinking traces. `completions_tool_extras` pins `reasoning.effort = "none"` for gpt-5.6, so there is nothing to surface.
- Cancel / stop-generating. Out of scope; `sendChat` has no abort path today.
- Changing the post-turn compile-repair loop in `chat.py`, the ATS pass, or revision coalescing.
- Resuming an interrupted stream. A dropped connection still means the turn is lost to the client, exactly as today.
- Frontend unit tests (the frontend has no runner).

## Shape of a turn, after

```
user: "fix my ATS failures"

  delta ×N        ──▶ provisional bubble grows: "Let me check the report"
  assistant       ──▶ bubble replaced by a real, persisted message
  tool            ──▶ [read_ats]
  delta ×N        ──▶ bubble grows again: "Two checks fail. Fixing"
  assistant       ──▶ real, persisted message
  tool            ──▶ [apply_typst_edit]
  source          ──▶ editor + preview update
  delta ×N        ──▶ bubble grows: "Both checks pass now because"
  assistant       ──▶ real, persisted message  (the final answer)
  done
```

Nothing in that column ever disappears.

## Risks accepted

- More `assistant` rows per turn means slightly more context fed to the next turn. Accepted as more accurate than today's silent drop.
- `onEvent` is awaited serially in `sendChat`, and the `source` handler awaits a preview recompile. Deltas queued behind it will flush in a burst. Pre-existing serialization; streaming only makes it visible. Not fixed here.
