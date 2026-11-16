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
MAX_ACTIONS = 6
REQUIREMENT_CHARS = 200
NOTE_CHARS = 200
QUOTE_CHARS = 240
TITLE_CHARS = 120
WHY_CHARS = 240
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
        "Record concrete changes the person can apply with one click. Each "
        "action carries an exact before/after pair: `before` MUST be copied "
        "character for character from the resume source you read, and MUST "
        "appear in it exactly once -- the person's browser applies the change "
        "by finding that one occurrence, with no further call to you. Prefer "
        "the smallest unique span (one bullet, one line) over a whole "
        "section. This tool does NOT change the resume; the person decides. "
        "The reply tells you, per action, whether its `before` was found "
        "exactly once -- fix the ones that were not by reading the source "
        "again and calling this again with corrected spans, rather than "
        "describing the change in prose. At most 6 actions, most useful "
        "first."
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
                                "the document."
                            ),
                        },
                        "after": {
                            "type": "string",
                            "description": (
                                "The replacement text. Must differ from `before`. Never "
                                "invent experience the person does not have: rewrite "
                                "what is there, or write a placeholder they will fill "
                                "in."
                            ),
                        },
                    },
                    "required": ["title", "before", "after"],
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

    Every action is kept, including the ones that cannot be applied: an action
    the person can read and ask about is worth more than a silently shorter
    list, and the model needs the per-item reason to correct itself. What
    changes is `appliable`, which is the only thing the card offers a button
    for.
    """
    if not isinstance(items, list):
        return {"error": "actions must be a list"}

    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(items[:MAX_ACTIONS]):
        if not isinstance(raw, dict):
            continue
        title = _clamp(raw.get("title"), TITLE_CHARS)
        before = raw.get("before")
        after = raw.get("after")
        if title is None or not isinstance(before, str) or not isinstance(after, str):
            continue

        occurrences: int | None = None
        if not before:
            reason = "before_empty"
        elif len(before) > BEFORE_MAX_CHARS:
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
                "title": title,
                "where": _clamp(raw.get("where"), TITLE_CHARS),
                "requirement": _clamp(raw.get("requirement"), REQUIREMENT_CHARS),
                "why": _clamp(raw.get("why"), WHY_CHARS),
                "before": before,
                "after": after,
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
    unmatched = len(rows) - appliable
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
