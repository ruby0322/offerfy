# Chat reasoning visibility

**Date:** 2026-10-22
**Status:** approved in companion (show the thinking; settings chosen by measurement)

## Goal

Make the model's thinking visible in the editor chat, and pick `reasoning.effort`
and `reasoning.summary` from measurement rather than taste.

**This supersedes the "Reasoning/thinking traces" non-goal** in
[2026-10-22-chat-token-streaming-design.md](2026-10-22-chat-token-streaming-design.md),
which was written on the assumption that `effort: "none"` was forced. It was not.

## Why this came up

Token streaming shipped, and the first real turn showed only tool cards plus the
final round's text — no narration anywhere. Two separate causes, both confirmed
by probe, neither a bug in the streaming work:

1. `completions_tool_extras` pinned `reasoning: {"effort": "none"}`, and nothing
   ever requested a reasoning summary, so there was no thinking to show.
2. Under that config the model emits **no preamble text in tool rounds** — round
   one returns `['function_call']` with zero text frames. The preamble-keeping
   logic was correct and had nothing to keep.

## Measurements

All against the live `/v1/responses` with `gpt-5.6-terra`, this module's real
`SYSTEM_PROMPT` and `TOOLS`, driving the production parser. **Sample sizes are
small (n=2 to n=5); treat these as directional.**

### Does a tool round carry preamble text?

| config | round-1 output items | text frames |
| --- | --- | --- |
| `effort: none` (as shipped) | `['function_call']` | 0 |
| `effort: low` | `['reasoning', 'function_call']` | 0 |
| `effort: medium` | `['reasoning', 'function_call']` | 0 |

No preamble under any setting. A `reasoning` item appears once effort is on, but
it carries no text unless a summary is requested.

### Which summary setting reliably shows thinking? (n=3 each)

| config | summary appeared | first visible | frames | chars | total |
| --- | --- | --- | --- | --- | --- |
| `low` + `auto` | **2/3** | 1734–1870ms | ~81 | ~434 | 2851–4707ms |
| `low` + `detailed` | **3/3** | 1363–3016ms | ~82 | ~430 | 2570–4336ms |
| `medium` + `auto` | **3/3** | 1322–1941ms | 71–80 | ~395 | 2537–3183ms |

`detailed` buys **reliability, not verbosity** — same ~430 chars, same latency,
but it actually shows up. `auto` lets the model skip the summary, and at low
effort it skipped one run in three.

### Which effort? (full multi-round turns, n=2 each)

| config | tool rounds used | reasoning chars | answer chars | outcome |
| --- | --- | --- | --- | --- |
| `low` + 6 | 6/6 | 1307 | 106 | real answer |
| `low` + 6 (repeat) | 6/6 | 972 | 117 | real answer |
| `medium` + 6 | 6/6 | 856 | 118 | real answer |
| `medium` + 6 (repeat) | 6/6 | 485 | 114 | *"I wasn't able to complete the update safely"* |

Both efforts spent the **entire** budget, so effort bought no efficiency, and
`medium` gave up once where `low` finished both times. `low` therefore wins on
this evidence: same visible thinking, fewer reasoning tokens.

### The reserved wrap-up round (n=5)

| config | tool rounds | reasoning | answer | fell back? |
| --- | --- | --- | --- | --- |
| `medium` + 2 (squeezed) | 2 | 947 | 164 | no |
| `medium` + 3 (squeezed) | 3 | 433 | 100 | no |
| `medium` + 8 | 8 | 1367 | 121 | no |
| `medium` + 10 | 10 | 1809 | 119 | no |
| `medium` + 8 (repeat) | 8 | 1866 | 117 | no |

5/5 real answers, including the deliberately squeezed runs. At `max_rounds=2` it
renamed the document and then *asked* about the date range rather than failing
silently — graceful degradation.

## Decisions

- `reasoning: {"effort": "low", "summary": "detailed"}` for `gpt-5.6*`.
- **Reasoning text is display-only and never persisted.** It is not something the
  assistant said to the user. Persisting it would replay it on reload and feed it
  back to the model next turn as its own speech. It streams as its own
  `{"type": "reasoning"}` SSE event and is dropped at turn end.
- **Reasoning never joins the answer bubble.** It renders as a separate dimmed
  block above it, so the one frontend rule from the streaming spec still holds
  and reasoning cannot be mistaken for the answer.
- **One round past `max_rounds` is reserved for the answer**, with
  `tool_choice: "none"`. Without it, a turn that spends every round on tools ends
  on the canned `"Updated the Typst source."` and the user is told nothing.
- `max_rounds` stays at 8 / 6. The reserved round makes the budget a **cost**
  knob rather than a correctness risk, and the model expands to fill whatever it
  is given (8 rounds → 8 tool calls, 10 → 10).
- Reasoning items are echoed back to the API by the existing
  `input_items.extend(output)`. Verified over real multi-round turns; no change
  needed.

## Things that turned out to be wrong

Recorded because each cost a probe and would otherwise be re-derived.

- **"GPT-5.6 rejects custom tools unless reasoning is off."** The old comment
  justifying `effort: "none"`. No longer true: `low` and `medium` both return
  function calls against this module's `TOOLS` without complaint.
- **"`medium` produces no summary."** Concluded from a single run that returned
  zero frames. Three runs later it was 3/3. Variance at n=1, not a property.
- **"Effort pays for itself by avoiding edit retries."** The stated reason for
  choosing `medium`. Disproven: both efforts used the full round budget.
- **"Raise `max_rounds` so the turn can finish."** Done, then reverted. The
  reserved round solves the fallback at any budget, and raising the ceiling only
  let the model spend more.

## Non-goals

- Raw chain-of-thought. Only the model's own summary is surfaced.
- Persisting or replaying thinking; no schema change, no new column.
- Per-path effort (one setting for every round of every turn).
- Collapsing/expanding the thinking block, or a "thought for Ns" affordance.
