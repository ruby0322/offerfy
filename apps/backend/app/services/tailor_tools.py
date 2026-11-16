"""The two tools that give a tailor pass a visible middle and a usable end.

A tailor pass used to read the resume, read the posting, and then write one
long paragraph of prose. Everything it worked out -- which requirements the
posting actually states, where the resume already answers them, what to change
-- arrived as text, so none of it could be laid out as a table, counted,
linked back to a requirement, or applied with a click. These two tools are
that same reasoning, recorded as data:

`map_jd_requirements` is the comparison. One row per requirement the posting
states, each with what the resume says about it. It **describes, it does not
score**: the statuses say what the document contains, there is no total, no
percentage and no verdict on the person. That is a product decision, and it is
in the tool description because the description is the only part of this a
model reads.

`propose_resume_actions` is the end: concrete changes, each carrying an exact
`before`/`after` pair. The person's browser applies the one they pick by
finding that `before` in the draft and replacing it -- no second call to a
model, nothing to pay for, instant. Which is why this module's real work is
validation: an action whose `before` is not in the document, or is in it
twice, cannot be applied, and a button that claims otherwise lies. So every
action is checked against the resume's current `typst_source` and the result
says, per item, whether it can be applied and why not.

Three properties worth keeping if this file is edited:

**Neither tool writes anything.** No rows, no source changes, no ids. They are
pure functions of their arguments plus the current draft, which is what makes
the retry loop safe: a model that gets `appliable: false` for two actions can
re-read the source and call again, as many times as its round budget allows,
with no state to corrupt.

**The validation corpus is the resume as the model saw it.** `read_typst`
returns the live draft, and `aiter_chat_edit` yields each `source` event
inside the per-call loop, so by the time a handler runs the database row and
the model's view agree. A validator that read anything narrower would reject
real items -- `app.services.jobs.reasons` has the scar: checking citations
against a trimmed body rejected four of five genuine ones on its first
production run.

**`before` is never clamped.** Every other prose field here is truncated to
fit a card, which is this repo's standing rule. `before` is the exception
because it is a key, not prose: a truncated key is a guaranteed mismatch, so
an over-long one is rejected with a reason instead.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.revisions import source_hash

MAP_REQUIREMENTS_TOOL = "map_jd_requirements"
PROPOSE_ACTIONS_TOOL = "propose_resume_actions"

# What a requirement row's status may say. Descriptive, not graded -- see the
# module docstring, and the enum's own description below, which is what the
# model actually reads.
STATUSES = ("in_resume", "partly_in_resume", "not_in_resume", "unclear")

MAX_REQUIREMENTS = 12
# Raised from 6 when suggestions became required to cover every gap the table
# names: the table holds up to 12 rows, and a cap below that would silently
# decide which gaps go unanswered.
MAX_ACTIONS = 10
REQUIREMENT_CHARS = 200
NOTE_CHARS = 200
QUOTE_CHARS = 240
TITLE_CHARS = 120
WHY_CHARS = 240
# One canned reply, and how many an item may carry. Short because they are
# rendered as buttons and the person is reading them, not a paragraph.
ASK_CHARS = 60
MAX_ASKS = 3
# A cap, not a clamp: see the module docstring. Generous enough for a whole
# experience entry, small enough that one action cannot smuggle the document
# into a chat row.
BEFORE_MAX_CHARS = 1200


_MAP_REQUIREMENTS = {
    "type": "function",
    "name": MAP_REQUIREMENTS_TOOL,
    "description": (
        "Record what this posting asks for and where this resume already says "
        "it. One row per requirement the posting states explicitly, in the "
        "posting's own words. This is a DESCRIPTION, not a score: `status` "
        "says what the resume contains, never how good the person is. Do not "
        "rank, grade, total or percentage the rows, and never call anyone a "
        "weak or a strong match. The person sees these rows as a table, so "
        "keep each one short and do not repeat the table in prose afterwards. "
        "Call it once per turn, after reading both the resume and the "
        "posting. At most 12 rows: the requirements that decide the "
        "application, not every line of the posting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "requirements": {
                "type": "array",
                "maxItems": MAX_REQUIREMENTS,
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement": {
                            "type": "string",
                            "description": (
                                "What the posting asks for, in the posting's own words, "
                                "trimmed to one clause."
                            ),
                        },
                        "status": {
                            "type": "string",
                            "enum": list(STATUSES),
                            "description": (
                                "in_resume: the resume states this outright. "
                                "partly_in_resume: the resume says something smaller or "
                                "adjacent -- a related tool, a shorter span, one of "
                                "several named things. not_in_resume: the resume does "
                                "not mention it. unclear: the requirement as written "
                                "does not say what would count ('strong communication "
                                "skills'), so no resume can answer it either way. "
                                "`unclear` is about the POSTING's wording, never about "
                                "the person."
                            ),
                        },
                        "resume_quote": {
                            "type": "string",
                            "description": (
                                "The exact phrase from the current resume source that "
                                "says it, copied verbatim. For in_resume and "
                                "partly_in_resume; omit otherwise."
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                "One short descriptive sentence: what the resume says, "
                                "or what it does not say. Never an evaluation."
                            ),
                        },
                    },
                    "required": ["requirement", "status"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["requirements"],
        "additionalProperties": False,
    },
}


_PROPOSE_ACTIONS = {
    "type": "function",
    "name": PROPOSE_ACTIONS_TOOL,
    "description": (
        "Record what the person could do about this posting. One suggestion "
        "per item, and **at least one for every requirement you marked "
        "partly_in_resume or not_in_resume** -- those are the gaps the table "
        "just showed them, and a list that skips the gaps answers only the "
        "parts that were already fine.\n"
        "Two shapes, and you pick per item:\n"
        "- A CONCRETE REWRITE, when the resume already has text to change. "
        "Give `before` and `after`. `before` MUST be copied character for "
        "character from the resume source you read and MUST appear in it "
        "exactly once -- the person's browser applies the change by finding "
        "that one occurrence, with no further call to you. Prefer the "
        "smallest unique span (one bullet, one line) over a whole section.\n"
        "- An OPEN SUGGESTION, when you do not know enough to write the text "
        "yourself: the resume says nothing about the requirement, or what to "
        "write depends on something only they can tell you. Omit `before` and "
        "`after`, say in `title` and `why` what is missing and what you would "
        "need, and give `asks` so they can answer in one tap. Never invent "
        "experience to turn one of these into a rewrite.\n"
        "This tool does NOT change the resume; the person decides. The reply "
        "tells you, per action, whether its `before` was found exactly once "
        "-- fix the ones that were not by reading the source again and "
        "calling this again with corrected spans, rather than describing the "
        "change in prose. At most 10 actions, most useful first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "maxItems": MAX_ACTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": (
                                "What this change does, one imperative line, in the "
                                "user's language."
                            ),
                        },
                        "where": {
                            "type": "string",
                            "description": (
                                "The section it touches, for the card's subtitle, in "
                                "the user's language."
                            ),
                        },
                        "requirement": {
                            "type": "string",
                            "description": (
                                "The requirement text from map_jd_requirements this "
                                "action answers, copied so the two cards line up. Omit "
                                "when it answers none."
                            ),
                        },
                        "why": {
                            "type": "string",
                            "description": (
                                "One short sentence tying it to the posting. No praise "
                                "and no grading."
                            ),
                        },
                        "before": {
                            "type": "string",
                            "description": (
                                "The exact existing text to replace. Verbatim from the "
                                "resume source, including indentation, and unique in "
                                "the document. Omit for an open suggestion."
                            ),
                        },
                        "after": {
                            "type": "string",
                            "description": (
                                "The replacement text. Must differ from `before`. Never "
                                "invent experience the person does not have: rewrite "
                                "what is there, or leave this out and ask. Omit for an "
                                "open suggestion."
                            ),
                        },
                        "asks": {
                            "type": "array",
                            "maxItems": MAX_ASKS,
                            "items": {"type": "string"},
                            "description": (
                                "Up to 3 replies the person can send in one tap, each "
                                "written as THEIR words in their language, under 60 "
                                "characters -- 'I have used Go, on an internal API' or "
                                "'No Go experience, skip this'. For an open suggestion "
                                "these are how they answer without typing, so cover "
                                "the likely answers including the negative one. A "
                                "concrete rewrite does not need them: it already has an "
                                "apply button."
                            ),
                        },
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["actions"],
        "additionalProperties": False,
    },
}


def tailor_tool_specs(resume: Resume) -> list[dict]:
    """Both tools, for every resume.

    Not gated on `kind == "tailor"`: the comparison is just as useful on a
    master resume against a posting the person is only considering, and a
    model that has the tool in a typed turn can answer "how do I stack up
    against this one" with a table instead of a paragraph.
    """
    return [_MAP_REQUIREMENTS, _PROPOSE_ACTIONS]


def _clamp(value: Any, limit: int) -> str | None:
    """One prose field, trimmed to fit a card. None when it says nothing."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _asks(value: Any) -> list[str]:
    """The canned replies for one item, clamped and capped.

    Optional by design: a concrete rewrite already has an apply button, so
    these only earn their place on an open suggestion, where they are the
    whole way in for someone who does not want to type.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _clamp(item, ASK_CHARS)
        if text is not None and text not in out:
            out.append(text)
        if len(out) == MAX_ASKS:
            break
    return out


def map_requirements(source: str, items: Any) -> dict[str, Any]:
    """Validate and normalise one requirement table. Pure.

    Rows are dropped only when they are structurally broken -- no
    `requirement`, or a `status` outside the vocabulary -- and every drop is
    reported so the model can see what happened rather than wondering why its
    table came back shorter.

    A `resume_quote` that does not appear in the source is reported through
    `resume_quote_found` and **not** a reason to drop the row. The row is a
    claim about the posting; the quote is supporting detail. Dropping real
    rows over an inexact quote is the mistake `app.services.jobs.reasons`
    made, and the card simply omits a quote it cannot stand behind.
    """
    if not isinstance(items, list):
        return {"error": "requirements must be a list"}

    rows: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for index, raw in enumerate(items[:MAX_REQUIREMENTS]):
        if not isinstance(raw, dict):
            dropped.append({"index": index, "reason": "not_an_object"})
            continue
        requirement = _clamp(raw.get("requirement"), REQUIREMENT_CHARS)
        if requirement is None:
            dropped.append({"index": index, "reason": "missing_requirement"})
            continue
        status = raw.get("status")
        if status not in STATUSES:
            dropped.append({"index": index, "reason": "unknown_status"})
            continue
        quote = _clamp(raw.get("resume_quote"), QUOTE_CHARS)
        rows.append(
            {
                "requirement": requirement,
                "status": status,
                "resume_quote": quote,
                # Checked against the clamped string, so the flag describes
                # what the card will actually render.
                "resume_quote_found": (quote in source) if quote else None,
                "note": _clamp(raw.get("note"), NOTE_CHARS),
            }
        )

    counts = {status: sum(1 for row in rows if row["status"] == status) for status in STATUSES}
    return {
        "ok": True,
        "total": len(rows),
        "truncated": len(items) > MAX_REQUIREMENTS,
        "counts": counts,
        "source_hash": source_hash(source),
        "items": rows,
        "dropped": dropped,
    }


def validate_actions(source: str, items: Any) -> dict[str, Any]:
    """Validate one action list against the draft it must apply to. Pure.

    Two shapes come through here. An item with a `before`/`after` pair is a
    concrete rewrite and is checked against the draft. An item with neither is
    an **open suggestion** -- the resume says nothing to rewrite, or what to
    write depends on something only the person knows -- and it is kept with
    `kind: "open"` and no apply button. That shape exists because the
    suggestions have to answer every gap the requirement table named, and a
    gap is precisely the case with no `before` to copy: requiring the pair
    made the list skip the rows that mattered most.

    Every action is kept, including the rewrites that cannot be applied: one
    the person can read and ask about is worth more than a silently shorter
    list, and the model needs the per-item reason to correct itself. What
    changes is `appliable`, which is the only thing the card offers an apply
    button for. Items that are genuinely unusable -- no title, or half a pair
    -- are reported in `dropped` rather than vanishing, which is how the
    requirement table has always behaved.
    """
    if not isinstance(items, list):
        return {"error": "actions must be a list"}

    rows: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for index, raw in enumerate(items[:MAX_ACTIONS]):
        if not isinstance(raw, dict):
            dropped.append({"index": index, "reason": "not_an_object"})
            continue
        title = _clamp(raw.get("title"), TITLE_CHARS)
        if title is None:
            dropped.append({"index": index, "reason": "missing_title"})
            continue
        before = raw.get("before")
        after = raw.get("after")
        has_before = isinstance(before, str) and before != ""
        has_after = isinstance(after, str) and after != ""
        # Half a pair is neither shape: a `before` with nothing to put in its
        # place cannot be applied, and an `after` with no anchor cannot be
        # placed. Say so instead of guessing which the model meant.
        if has_before != has_after:
            dropped.append(
                {
                    "index": index,
                    "reason": "before_without_after" if has_before else "after_without_before",
                }
            )
            continue

        occurrences: int | None = None
        if not has_before:
            kind = "open"
            reason = "no_preview"
        else:
            kind = "edit"
            if len(before) > BEFORE_MAX_CHARS:
                # Rejected, never truncated: `before` is a key.
                reason = "before_too_long"
            elif before == after:
                reason = "no_change"
            else:
                occurrences = source.count(before)
                if occurrences == 0:
                    reason = "before_missing"
                elif occurrences > 1:
                    reason = "before_ambiguous"
                else:
                    reason = None

        rows.append(
            {
                "id": f"a{index + 1}",
                "kind": kind,
                "title": title,
                "where": _clamp(raw.get("where"), TITLE_CHARS),
                "requirement": _clamp(raw.get("requirement"), REQUIREMENT_CHARS),
                "why": _clamp(raw.get("why"), WHY_CHARS),
                "before": before if has_before else None,
                "after": after if has_after else None,
                "asks": _asks(raw.get("asks")),
                "occurrences": occurrences,
                "appliable": reason is None,
                "reason": reason,
            }
        )

    appliable = sum(1 for row in rows if row["appliable"])
    out: dict[str, Any] = {
        "ok": True,
        "total": len(rows),
        "appliable": appliable,
        "truncated": len(items) > MAX_ACTIONS,
        "source_hash": source_hash(source),
        "items": rows,
    }
    out["dropped"] = dropped
    # Only the REWRITES that failed to match: an open suggestion is not
    # "unmatched", it is a different shape doing its job, and telling the
    # model to go fix it would push it to invent a `before`.
    unmatched = sum(
        1 for row in rows if row["kind"] == "edit" and not row["appliable"]
    )
    if unmatched:
        out["hint"] = (
            f"{unmatched} action(s) could not be matched to the resume. Read the "
            "source again and copy `before` exactly, including indentation, then "
            "call this tool again."
        )
    return out


def tailor_tool_handlers(
    db: Session, resume: Resume, *, mapped: list[str] | None = None
) -> dict[str, Callable[[dict], str]]:
    """The two tools, bound to one resume's current draft.

    `mapped` is the "one requirement table per turn" state, and it works the
    way `family_tool_handlers`' `created` list does for the same reason: the
    chat router rebuilds these handlers per tool call so that a streaming turn
    holds no session, so a caller that rebuilds must pass the same list each
    time or the cap resets on every call. A caller that builds once per turn
    (the tailor runner) can let it default.

    `propose_resume_actions` is deliberately **not** capped. A model that gets
    `appliable: false` for two of its actions is supposed to read the source
    again and call it again -- that is what the reply's `hint` asks for -- and
    the second card superseding the first is honest about what happened.
    """
    mapped = [] if mapped is None else mapped

    def map_jd_requirements(args: dict) -> str:
        if mapped:
            return json.dumps(
                {
                    "error": (
                        f"{MAP_REQUIREMENTS_TOOL} was already called this turn. Say "
                        "what changed in words instead of drawing a second table."
                    )
                },
                ensure_ascii=False,
            )
        result = map_requirements(resume.typst_source or "", args.get("requirements"))
        if "error" not in result:
            mapped.append("1")
        return json.dumps(result, ensure_ascii=False)

    def propose_resume_actions(args: dict) -> str:
        return json.dumps(
            validate_actions(resume.typst_source or "", args.get("actions")),
            ensure_ascii=False,
        )

    return {
        MAP_REQUIREMENTS_TOOL: map_jd_requirements,
        PROPOSE_ACTIONS_TOOL: propose_resume_actions,
    }
