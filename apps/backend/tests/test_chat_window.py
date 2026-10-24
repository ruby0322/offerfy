"""The bound on what the chat history re-sends, and the cache property it rests on.

The expensive mistake this file exists to catch is a window that slides. The
provider bills a re-sent prefix at 10% of list and a new one at 125%, so a cut
that moves every turn costs more than sending everything; `test_...only_at_folds`
and `test_...fold_count` are the tests that would fail if someone replaced the
grid with a plain "last N messages".
"""

from __future__ import annotations

import math

from app.services.chat_window import (
    FOLD_STEP,
    HEAD_MESSAGES,
    TAIL_BUDGET_CHARS,
    TAIL_MIN_MESSAGES,
    message_chars,
    window_history,
)

# The production average was 374 characters per user/assistant row; the
# parameters were picked against it, so the fixtures use it.
AVG_CHARS = 374
ENGAGE_AT = HEAD_MESSAGES + FOLD_STEP + TAIL_MIN_MESSAGES


def conversation(count: int, chars: int = AVG_CHARS) -> list[dict]:
    """`count` alternating rows, each identifiable and `chars` long."""
    rows = []
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        tag = f"<{role}-{index}>"
        rows.append({"role": role, "content": tag + "x" * (chars - len(tag))})
    return rows


def test_a_short_conversation_is_sent_exactly_as_before():
    # The whole point of the engagement threshold: most conversations must not
    # change at all, so this change cannot regress them.
    for count in (0, 1, 2, 10, ENGAGE_AT - 1):
        history = conversation(count)
        window = window_history(history)
        assert window.items == history, count
        assert window.folded is False
        assert window.dropped == 0
        assert window.sent_chars == window.total_chars


def test_the_bound_engages_exactly_at_the_documented_threshold():
    assert window_history(conversation(ENGAGE_AT - 1)).dropped == 0
    assert window_history(conversation(ENGAGE_AT)).dropped == FOLD_STEP


def test_a_long_conversation_keeps_the_head_the_tail_and_a_note():
    history = conversation(146)
    window = window_history(history)

    assert window.folded is True
    assert window.items[:HEAD_MESSAGES] == history[:HEAD_MESSAGES]
    # The note stands where the dropped span was, never at either end.
    note = window.items[HEAD_MESSAGES]
    assert note["role"] == "user"
    assert f"{window.dropped} earlier messages" in note["content"]
    assert "read_typst" in note["content"]
    assert window.items[HEAD_MESSAGES + 1 :] == history[window.cut :]
    # The turn the user is actually waiting on is always the last thing sent.
    assert window.items[-1] == history[-1]
    assert window.sent_chars < window.total_chars


def test_the_cut_only_lands_on_the_grid():
    for count in range(0, 420):
        cut = window_history(conversation(count)).cut
        assert (cut - HEAD_MESSAGES) % FOLD_STEP == 0, count


def test_what_is_sent_stops_growing_with_the_transcript():
    # The finding: history cost grew with the document's lifetime. At 500 rows
    # the unbounded history would be ~187,000 characters (~47,000 tokens).
    sent = [window_history(conversation(count)).sent_chars for count in (100, 200, 350, 500)]
    assert max(sent) <= TAIL_BUDGET_CHARS + (HEAD_MESSAGES + 1) * 1200
    # And it is a ceiling, not a trend: four times the transcript, same size.
    assert max(sent) - min(sent) < TAIL_BUDGET_CHARS


def test_the_tail_stays_above_its_floor_and_near_its_budget():
    """The budget is a target; the floor and the grid are hard.

    Where no grid cut honours the budget without breaching the floor, the tail
    goes over budget instead of collapsing -- bounded, because one more grid
    step would have left fewer than `TAIL_MIN_MESSAGES`.
    """
    overshoots = 0
    for count in range(ENGAGE_AT, 420):
        history = conversation(count)
        window = window_history(history)
        tail = history[window.cut :]
        assert len(tail) >= TAIL_MIN_MESSAGES, count
        chars = sum(message_chars(m["content"]) for m in tail)
        if chars > TAIL_BUDGET_CHARS:
            overshoots += 1
            assert len(tail) - FOLD_STEP < TAIL_MIN_MESSAGES, count
        assert len(tail) <= TAIL_MIN_MESSAGES + FOLD_STEP - 1, count
    assert overshoots > 0, "fixture no longer exercises the floor"


def test_the_prefix_changes_only_at_folds():
    """The cache property. Between folds a turn must be a pure append.

    Anything else hands the provider a prefix it has not seen and bills the
    whole history at the cache-write rate.
    """
    history = conversation(420)
    previous: list[dict] | None = None
    previous_cut = None
    rewrites = 0
    for count in range(1, len(history) + 1):
        window = window_history(history[:count])
        if previous is not None:
            appended = window.items[: len(previous)] == previous
            if window.cut == previous_cut:
                assert appended, f"prefix moved without a fold at {count}"
            else:
                assert window.cut > previous_cut, f"cut went backwards at {count}"
                rewrites += 1
        previous, previous_cut = window.items, window.cut
    # One rewrite per fold and no more. A sliding window would score ~350.
    assert rewrites == math.floor((420 - HEAD_MESSAGES - TAIL_MIN_MESSAGES) / FOLD_STEP)
    assert rewrites <= 420 / FOLD_STEP + 1


def test_the_note_is_stable_between_folds():
    # The note sits inside the cached prefix, so text that changed every turn
    # would invalidate it every turn just as surely as a sliding cut would.
    notes = {
        window_history(conversation(count)).items[HEAD_MESSAGES]["content"]
        for count in range(ENGAGE_AT, ENGAGE_AT + FOLD_STEP)
    }
    assert len(notes) == 1


def test_one_oversized_message_cannot_starve_the_recent_turns():
    # A pasted job description in the middle of a long conversation. No grid
    # cut can honour the budget while keeping it, so the floor wins and the
    # tail goes over budget rather than collapsing.
    history = conversation(146)
    history[80]["content"] = "J" * 200_000
    window = window_history(history)
    assert len(history) - window.cut >= TAIL_MIN_MESSAGES
    assert window.items[-TAIL_MIN_MESSAGES:] == history[-TAIL_MIN_MESSAGES:]
    assert (window.cut - HEAD_MESSAGES) % FOLD_STEP == 0


def test_an_oversized_current_message_is_never_dropped():
    # An upload: the router puts base64 image parts on the newest message.
    history = conversation(146)
    history[-1]["content"] = [
        {"type": "input_text", "text": "Rewrite from this."},
        {"type": "input_image", "image_url": "data:image/png;base64," + "A" * 400_000},
    ]
    window = window_history(history)
    assert window.items[-1]["content"] == history[-1]["content"]
    # Multi-part content is measured by its text, so the base64 blob -- which
    # bills as image tokens, not characters -- does not consume the budget.
    assert message_chars(history[-1]["content"]) == len("Rewrite from this.")
    assert window.sent_chars < TAIL_BUDGET_CHARS + 10_000


def test_tool_rows_are_still_never_sent():
    history = conversation(146)
    history[40] = {"role": "tool", "content": "T" * 50_000}
    history[41] = {"role": "tool", "content": "T" * 50_000}
    window = window_history(history)
    assert all(item["role"] in {"user", "assistant"} for item in window.items)
    assert "T" * 100 not in "".join(
        item["content"] for item in window.items if isinstance(item["content"], str)
    )


def test_message_chars_survives_content_it_does_not_recognise():
    assert message_chars(None) == 0
    assert message_chars(7) == 0
    assert message_chars([{"type": "input_image"}, "stray", {"text": 3}]) == 0
    assert message_chars([{"text": "ab"}, {"text": "c"}]) == 3
