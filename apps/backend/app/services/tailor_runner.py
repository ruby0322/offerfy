"""Drive one tailor resume's first pass against its posting.

A tailor is created for one specific job. This module runs the single agent
turn that opens it: read the resume, read the posting, say how well they match
and what to change -- and it does so as a durable run (see
`app.services.tailor_run`) rather than as a chat message the browser sent.

**No `ChatMessage` with `role="user"` is ever written here**, the same rule
`app.services.import_runner` states and for the same reason. The instruction
below is the product's, not the person's. It used to be built in the frontend
from `editor.tailorKickstartPrompt` and sent through `sendUserChat`, which
persisted it with `role="user"`: the person then saw a robotic paragraph of
instructions attributed to them, and the model saw a turn it was expected to
answer as if they had typed it.

**It also used to be lost on reload.** The work lived in one SSE response, and
the frontend's "has this tailor been opened yet" flag was `messages.length >
0` -- which the orphaned user row satisfied by itself. So a refresh mid-pass
left a user message with no answer and nothing that would ever retry it. The
run row fixes that: progress is in Postgres, a reconnecting client re-attaches
to it, and `tailor_run.is_stale` tells a dead runner from a slow one.

**This function never raises.** Every exit ends in `tailor_run.fail()` or
`finish()`. A run left `running` is the worst outcome: the card spins with
nothing left to update it.

**The language has to be stated explicitly, and that is new here.**
`SYSTEM_PROMPT` ends with "Reply in the user's language", which works for a
typed turn because the user's own words are in the transcript to infer from.
This turn has no user words at all, so without naming the locale the model
answers the English instruction in English to a zh-TW user. `resume.locale` is
the source of truth, the same field the editor was built against.

Event translation mirrors `import_runner.drain`, minus the plan:
- `tool` is persisted (`persist_tool_message`) and moves `phase`.
- `source` is persisted with `set_draft_source` and then `touch`ed again, so
  `version` moves *after* the commit -- that second bump is what the editor's
  preview refresh hangs on (see `tailor_run.snapshot`'s `source_hash`).
- `assistant` is persisted (`persist_assistant_message`).
- `usage` is billed through both halves of the seam and never becomes a chat
  row; one without the other is what `usage.reconcile` reports as a gap.
- every event `touch`es the run, which is both the heartbeat and the
  chokepoint that stops a superseded runner in the other uvicorn worker.

`deferred_guard`, not `preflight`: nobody is holding an HTTP response open
waiting on this turn, so refusing would only mean the pass 429s itself with
nothing watching. Not `system_guard` either -- the person asked for this by
clicking the posting, so the cost and the turn are theirs.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import GuestSession, Resume, TailorRun, User
from app.services.jobs.applications import job_id_for_tailor
from app.services.chat_messages import (
    persist_assistant_message,
    persist_tool_message,
    persist_usage,
)
from app.services.family_tools import family_tool_handlers, family_tool_specs
from app.services.jobs_tools import (
    READ_JOB_TOOL,
    jobs_tool_handlers,
    jobs_tool_specs,
)
from app.services.llm import aiter_chat_edit
from app.services.llm.prompts import READ_TOOL
from app.services.metering import deferred_guard, metered
from app.services.revisions import set_draft_source
from app.services.role_card import build_role_card
from app.services.tailor_run import (
    TailorRunSuperseded,
    advance_phase,
    fail,
    finish,
    touch,
)
from app.services.tailor_tools import (
    MAP_REQUIREMENTS_TOOL,
    PROPOSE_ACTIONS_TOOL,
    tailor_tool_handlers,
    tailor_tool_specs,
)

logger = logging.getLogger(__name__)

# Which tool call enters which stage. The card's checklist is this table in
# order, so the stages are observable rather than inferred: each one is the
# tool that does the work, and nothing else moves the cursor.
#
# `EDIT_TOOL` is deliberately absent. The pass is not supposed to edit at all
# now -- it proposes, and the person applies -- so there is no stage for an
# edit to enter. An edit that happens anyway is still fully visible as its own
# tool card, a `source` event and a moved `source_hash`, which is better
# evidence than a phase string, and it is what lets the stale and failed cards
# keep promising the resume was not touched.
_PHASE_FOR_TOOL = {
    READ_TOOL: "read_resume",
    READ_JOB_TOOL: "read_job",
    MAP_REQUIREMENTS_TOOL: "mapping",
    PROPOSE_ACTIONS_TOOL: "proposing",
}

# One read of the resume, one read of the posting, a narration, and headroom
# for a compile repair if the model edits anyway. Far below the import's 20:
# this is one turn, not a whole declared plan.
MAX_ROUNDS = 8
# A per-chunk read budget, like the import's. `tailor_run.STALE_AFTER_SECONDS`
# (240) must stay above it, or a slow first token reads as a dead runner --
# see that module for what happened to the import when it did.
TIMEOUT_SECONDS = 180.0

# The LlmCall row's purpose. 'chat' rather than a new 'tailor' value because
# `llm_calls.purpose` is a CHECK constraint with a closed vocabulary, and
# widening it is its own migration; the turn genuinely is a chat turn on the
# person's own allowance. The cost of this is that the admin by-purpose
# breakdown cannot separate a tailor pass from a typed turn. Worth revisiting
# if that number is ever wanted.
TAILOR_PURPOSE = "chat"

# What to call the language in the prompt. Not a bare locale tag: "Reply in
# zh-TW" is an instruction about a code, and models answer it less reliably
# than they answer a named language.
_LANGUAGES = {
    "zh-TW": "Traditional Chinese (zh-TW), as used in Taiwan",
    "zh-CN": "Simplified Chinese (zh-CN)",
    "en": "English",
}


def language_of(resume: Resume) -> str:
    return _LANGUAGES.get(resume.locale or "zh-TW", _LANGUAGES["zh-TW"])


def instruction(resume: Resume, job_id: str | None) -> str:
    """The pass's prompt. Server-owned, and never shown as the person's words.

    Two shapes, which is the "read both, posting first" rule: with a posting
    the model is told its id and sent to `read_job`, so the description is
    fetched once on demand instead of being copied into `target_context` and
    then replayed by `build_role_card` on every later turn. Without one, the
    role card's `target_label`/`target_context` are already in the system
    prompt and the model is pointed at them.
    """
    lines = [
        "A new tailored copy of this resume has just been created for one "
        "specific role. The person has not typed anything yet -- this "
        "instruction comes from the product, not from them, so do not thank "
        "them for it, quote it, or answer it as if it were their message. "
        "Open the conversation yourself.",
        "",
        # "Do not edit, at all" rather than the old "not yet". The pass ends
        # at proposals: `propose_resume_actions` carries exact before/after
        # pairs and the person's browser applies the ones they pick, so an
        # edit made here is a change nobody asked for -- and it is what used
        # to make the stale and failed cards' promise that the resume was
        # untouched a lie.
        "Do this in order. Do NOT edit the resume in this pass -- not with "
        "apply_typst_edit, not at all. The person chooses what changes.",
        f"1. Call {READ_TOOL} to see the resume as it stands.",
    ]
    if job_id:
        lines.append(
            f"2. Call {READ_JOB_TOOL} with job_id {job_id!r} to read the "
            "posting this copy is for. Work from that description, not from "
            "the job title."
        )
    else:
        lines.append(
            "2. The role this copy is for is described in your document "
            "context above. If it names no role at all, say so and ask which "
            "role they have in mind before assessing anything."
        )
    lines += [
        f"3. Call {MAP_REQUIREMENTS_TOOL} with the requirements this posting "
        "states and where this resume does or does not address each one. "
        "Describe what the resume says; do not score it and do not total it.",
        f"4. Call {PROPOSE_ACTIONS_TOOL} with the concrete changes you would "
        "make, each with an exact before/after pair copied from the source "
        "you read in step 1. The reply tells you which ones matched; fix any "
        "that did not and call it again.",
        "5. Then write two or three sentences: what this posting is really "
        "asking for, and where you would start. The two cards above carry the "
        "detail -- do not repeat them as a list.",
        "",
        f"Reply in {language_of(resume)}.",
    ]
    return "\n".join(lines)


def _owner(db: Session, resume: Resume) -> tuple[User | None, GuestSession | None]:
    user = db.get(User, resume.user_id) if resume.user_id else None
    guest = db.get(GuestSession, resume.guest_session_id) if resume.guest_session_id else None
    return user, guest


def _owner_guard(user: User | None, guest: GuestSession | None):
    """The guard for one pass.

    `purpose="chat"` here for the reason `import_runner._owner_guard` spells
    out: a Guard's purpose is not telemetry -- `metering.Meter.record` uses it
    to decide whether a round counts as a turn against the weekly allowance.
    This pass is a turn the person asked for, so it counts as one.
    """
    return deferred_guard(
        user=user,
        guest=guest,
        purpose="chat",
        reason="tailor first pass the person asked for; no HTTP caller to refuse",
    )


async def run_tailor(
    db: Session, resume: Resume, run: TailorRun, *, model: str | None = None
) -> None:
    """Drive one tailor pass to completion. Never raises."""
    try:
        job_id = run.job_id or job_id_for_tailor(db, resume)
        messages = [{"role": "user", "content": instruction(resume, job_id)}]
        user, guest = _owner(db, resume)
        extra_specs = [
            *family_tool_specs(resume),
            *jobs_tool_specs(resume),
            *tailor_tool_specs(resume),
        ]
        extra_handlers = {
            **family_tool_handlers(db, resume),
            **jobs_tool_handlers(db, resume, user),
            # Built once, because this is one turn with one session -- so the
            # per-turn cap inside can keep its own default list, unlike the
            # chat router's per-call rebuild.
            **tailor_tool_handlers(db, resume),
        }
        async with metered(_owner_guard(user, guest)) as meter:
            assistant_text = ""
            async for event in aiter_chat_edit(
                messages,
                resume.typst_source,
                model=model,
                prefer_full_source=False,
                extra_system=build_role_card(db, resume),
                verify_compile=True,
                max_rounds=MAX_ROUNDS,
                timeout=TIMEOUT_SECONDS,
                extra_tools=extra_specs,
                tool_handlers=extra_handlers,
            ):
                touch(db, run)
                kind = event.get("kind")
                if kind == "usage":
                    # Both halves, as the chat router does them: the counter
                    # (durable, enforces the allowance) and the LlmCall row
                    # (telemetry). One without the other is an admin gap for
                    # that window forever.
                    persist_usage(db, resume.id, event, TAILOR_PURPOSE, user=user, guest=guest)
                    meter.record(event)
                elif kind == "tool":
                    trace = event["trace"]
                    persist_tool_message(db, resume.id, trace)
                    phase = _PHASE_FOR_TOOL.get(trace.get("name") or "")
                    if phase:
                        # Forward-only: the model picks the order, and a
                        # second read after proposing must not untick steps
                        # the person watched complete. See `advance_phase`.
                        advance_phase(db, run, phase)
                elif kind == "source":
                    set_draft_source(db, resume, event["typst_source"])
                    db.commit()
                    # Bump after the source commits, so the hash the editor
                    # repaints on is actually observable. See tailor_run.
                    touch(db, run)
                elif kind == "assistant":
                    assistant_text = event["content"] or assistant_text
                    persist_assistant_message(db, resume.id, assistant_text)
                    # No phase move here any more. The four stages are the
                    # four tools, and narration is what happens after the
                    # last one -- inferring a stage from prose is what made
                    # the old `assessing` mean nothing in particular.
        finish(db, run)
    except TailorRunSuperseded:
        # Another pass took over this resume. Say nothing and change nothing:
        # the row already belongs to the new runner, and `fail` here would
        # overwrite its status.
        logger.info("tailor run %s superseded; stopping", run.id)
    except Exception as exc:
        logger.exception("tailor run %s failed", run.id)
        fail(db, run, str(exc)[:500] or exc.__class__.__name__)
