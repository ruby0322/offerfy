"""Bound the chat history that each model round re-sends.

The transcript is append-only and unbounded: `_history()` in the chat router
returns every `ChatMessage` row for a resume, and every round of every turn
re-sent all of it. Measured against production on 2026-10-24 that was ~13,700
tokens of the ~25,900-token round input (63%) on a resume with 146 user and
assistant rows, and it grows with the document's lifetime rather than with the
size of the edit -- at 500 rows it would be ~47,000.

This module decides what part of that history goes to the provider. It changes
nothing about what is stored or shown: the rows stay, `GET /messages` still
returns all of them, and the UI is untouched.

Two properties drive the design, and either one alone would pick a worse scheme.

**The prompt cache must keep working.** The provider discounts a re-sent prefix
to 10% of list and charges 1.25x to write a new one, and the measured hit rate
is 82.6% overall (88-99% within a warm session). That inverts the naive
arithmetic: re-sending history is cheap, and *changing* it is what costs. A
window that slid by a message per turn would hand the provider a new prefix
every turn, and at these rates that loses: rewriting a ~4,200-token prefix
costs ~$0.0096 to save ~$0.0019, about five times what it buys. So the cut is
snapped to a grid of `FOLD_STEP` messages counted from the start of the
conversation. Between folds the items this returns are byte-identical and the
tail only grows at the end, which is exactly the shape the cache wants; a fold
-- one rewrite, and of the short prefix left just after a cut, not the long one
just before it -- comes once every `FOLD_STEP` messages, so roughly every 19
turns. Amortised, that is ~$0.0002 per turn against a saving that starts at
~$0.0009 and grows.

So it is cost-positive from the ~68 rows where it starts dropping anything, and
increasingly so above: the saving per turn grows with the history no longer
sent while the cost of a fold does not. At the measured 146 rows it sends
~4,800 tokens of history instead of ~13,700, taking the round from ~25,900
input tokens to ~17,000. The ceiling matters more than that number -- the
history it sends averages ~4,200 tokens and never exceeds ~6,500, whether the
transcript holds 150 rows or 1,500.

**Dropping context can make a turn undo work.** Three things carry state
forward and none of them live in the middle of the transcript: the Typst
document is the real state and the model is told to `read_typst` before acting;
durable preferences have their own channel (`propose_memory`, replayed into the
role card every turn by `app.services.role_card`); and the opening messages,
which usually hold the brief or the upload instruction, are pinned here. What
the middle holds is recent conversational context, and the budgets below keep
16 to 63 messages of it -- 8 to 30 turns. The elision is also announced rather
than hidden, so a model that needs something from the dropped span asks
instead of guessing.

Nothing is dropped below `HEAD_MESSAGES + FOLD_STEP + TAIL_MIN_MESSAGES` = 68
user and assistant rows, so short conversations go to the provider exactly as
they did before. Only the long ones change, which is also where the cost is.

Sizes are measured in characters because that is what the stored rows are;
production measured 3.99 chars per token on this history, so the budgets below
read as roughly a quarter of their value in tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The opening of the conversation, kept forever. The first user message is
# normally the brief ("tailor this for X", "use the attached resume"), and
# pinning it costs ~375 tokens that are cached on every turn after the first.
HEAD_MESSAGES = 4

# The cut may only land on `HEAD_MESSAGES + n * FOLD_STEP`. This is the whole
# cache story: a bigger step means rarer prefix rewrites, a smaller one a
# smoother bill and more of them. It is deliberately as coarse as the budget,
# so the grid rather than the budget is what sets the fold rate.
FOLD_STEP = 48

# Target size for the retained tail, in characters: ~4,500 tokens, about 48
# messages at the 374 chars/message production average. It is a target rather
# than a hard cap, because the grid and the floor below can both outvote it --
# see `TAIL_MIN_MESSAGES`.
TAIL_BUDGET_CHARS = 18_000

# However big the messages are, this many at the end are always sent verbatim.
# It guarantees the current turn's own message survives -- an upload puts a
# base64 image in it and a pasted job description can be tens of thousands of
# characters -- and it is the floor of the sawtooth. It is also what makes
# `TAIL_BUDGET_CHARS` soft: the cut cannot take the next grid step if that
# would leave fewer than this many, so the tail runs from 16 messages up to
# 16 + `FOLD_STEP` - 1 = 63, or fewer if they are big enough to hit the budget
# first. Going a little over budget beats collapsing the recent turns.
TAIL_MIN_MESSAGES = 16

ELISION_ROLE = "user"


def _elision_note(dropped: int) -> str:
    """The stand-in for the dropped span.

    Worded to prevent the specific failure that truncation causes: a model that
    cannot see an earlier instruction, assumes what the document contains, and
    rewrites work that was already settled. It is stable between folds, because
    `dropped` is a multiple of `FOLD_STEP`.
    """
    return (
        f"[{dropped} earlier messages in this conversation are not included in "
        "this request. Nothing was deleted -- the user can still see them. "
        "The Typst document holds the current state of the work, so call "
        "read_typst instead of assuming what it contains. If you need an "
        "instruction, a name or a decision from that earlier part and it is "
        "not in the document, ask for it rather than guessing or redoing work "
        "that may already be done.]"
    )


def message_chars(content: Any) -> int:
    """How big one message's content is, for budget purposes.

    Multi-part content (an image upload) is measured by its text only. The
    base64 data URL alongside it is hundreds of thousands of characters and
    bills as a handful of image tokens, so counting it would be a wildly wrong
    proxy and would blow the budget on the one message that must never be
    dropped. `TAIL_MIN_MESSAGES` is what protects that message instead.
    """
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                total += len(text)
        return total
    return 0


@dataclass(frozen=True)
class WindowedHistory:
    """What to send, and enough numbers to see whether the bound is working."""

    items: list[dict]
    # Index of the first message after the pinned head that is sent verbatim.
    # Always `HEAD_MESSAGES + n * FOLD_STEP`; `HEAD_MESSAGES` means nothing was
    # dropped and `items` is the whole history.
    cut: int
    dropped: int
    sent_chars: int
    total_chars: int

    @property
    def folded(self) -> bool:
        return self.dropped > 0


def window_history(messages: list[dict]) -> WindowedHistory:
    """Pick the prefix-stable slice of `messages` to send to the provider.

    Input is the chat history as the router builds it: `{"role", "content"}`
    dicts, user and assistant only, oldest first. Rows of any other role are
    dropped, as they already were -- `tool` rows in particular, which the same
    production instance held 1.44M characters of on a single resume against
    55,000 of history.
    """
    history = [
        {"role": m.get("role"), "content": m.get("content") or ""}
        for m in messages
        if m.get("role") in {"user", "assistant"}
    ]
    total_chars = sum(message_chars(m["content"]) for m in history)
    count = len(history)

    # Nothing to gain below this: the head and the protected tail already
    # overlap, so no grid cut could drop anything.
    max_cut = count - TAIL_MIN_MESSAGES
    if max_cut <= HEAD_MESSAGES:
        return WindowedHistory(
            items=history,
            cut=HEAD_MESSAGES,
            dropped=0,
            sent_chars=total_chars,
            total_chars=total_chars,
        )

    # Suffix sums, so the loop below does not re-add the tail for every
    # candidate. `suffix[i]` is the size of `history[i:]`.
    suffix = [0] * (count + 1)
    for index in range(count - 1, -1, -1):
        suffix[index] = suffix[index + 1] + message_chars(history[index]["content"])

    # The smallest grid cut whose tail fits the budget. `suffix` falls as the
    # cut advances, so this is the first one that qualifies; if none does --
    # one oversized message in the tail is the way that happens -- take the
    # largest cut the floor allows and go over budget rather than starve the
    # recent turns.
    cut = HEAD_MESSAGES
    while cut + FOLD_STEP <= max_cut and suffix[cut] > TAIL_BUDGET_CHARS:
        cut += FOLD_STEP

    dropped = cut - HEAD_MESSAGES
    if dropped <= 0:
        return WindowedHistory(
            items=history,
            cut=HEAD_MESSAGES,
            dropped=0,
            sent_chars=total_chars,
            total_chars=total_chars,
        )

    note = {"role": ELISION_ROLE, "content": _elision_note(dropped)}
    items = [*history[:HEAD_MESSAGES], note, *history[cut:]]
    return WindowedHistory(
        items=items,
        cut=cut,
        dropped=dropped,
        sent_chars=sum(message_chars(m["content"]) for m in items),
        total_chars=total_chars,
    )
