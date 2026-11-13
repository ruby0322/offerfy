"""Drive one resume import to completion.

An uploaded resume is imported by an LLM agent that declares its own list of
sections and then edits the resume one section at a time, so a progress card
in the chat fills in as it works and the compiled preview grows section by
section. This module is the integration point: it reads the upload, runs one
agent turn over it with the import's own tools, and translates that turn's
events into the progress rows `app.services.import_run` owns.

**No `ChatMessage` with `role="user"` is ever written here.** The import
prompt is server-owned, not something the person typed, and today's chat
flow already shows the symptom of getting this wrong: a robotic upload
instruction persisted as a user message looks like it came from them. The
message handed to the model is built in memory and passed straight to
`aiter_chat_edit`'s `messages` argument instead.

**This function never raises.** Every exit, including one the turn itself
raised, ends in `import_run.fail()`. A run left in `running` after a crash
is the worst outcome here: the progress card has nothing left updating it
and nothing left to tell the client, so it would spin forever.

Event translation, one round at a time:
- `kind == "tool"` is always persisted (`persist_tool_message`). When its
  trace names `EDIT_TOOL` and the edit actually changed the source,
  `advance()` is also called with the edit's `step` argument, passed through
  untouched -- including `None` when the model didn't name one.
  `import_run.advance` owns what an unlabelled or off-plan edit means; this
  module does not special-case either.
- `kind == "source"` is persisted with `set_draft_source`, exactly as
  `app.routers.chat`'s `relay()` does for the live chat turn -- and then
  `touch()` again, so the run's `version` moves *after* the new source is
  committed. That second bump is what the editor's preview refresh hangs
  on; see `import_run.snapshot`'s `source_hash` and this module's
  `drain`.
- `kind == "assistant"` is persisted (`persist_assistant_message`).
- `kind == "usage"` is billed through the metering seam (see below) and
  never turned into a chat row. Both halves of it: the counter
  (`meter.record`) and the `LlmCall` row (`persist_usage`, under
  `IMPORT_PURPOSE`). Writing one without the other is what
  `app.services.usage.reconcile` reports as an admin gap.
- every event, whatever its kind, calls `touch()` so the run's heartbeat
  stays fresh -- that is what lets a reconnecting client (and
  `import_run.is_stale`) tell a live import from one whose worker died. It is
  also the chokepoint that stops a *superseded* runner: `touch` raises
  `ImportSuperseded` once the row is no longer `running`, which is the only
  mechanism that works across two uvicorn workers, where the old runner's
  task is in the other process and cannot be cancelled from here.

**Why this wraps `aiter_chat_edit` in `deferred_guard`.** The import spends
real provider tokens exactly like a chat turn does, and
`tests/test_metering.py::test_billable_call_sites_are_wrapped` is the
inventory that catches a new billable call site that forgot to be metered --
the exact failure mode `app/jobs/refresh_profiles.py` had before that module
existed. `preflight()` is the wrong half of the seam here: nobody is holding
an HTTP response open waiting on this turn, so refusing would only mean an
import 429s itself with nothing watching. `deferred_guard()` is the other
half, built for precisely that shape of caller, and demands a written
reason instead of a silent exemption.

NOT `system_guard`, which is the third door: that one bills subject kind
'system' because the platform decided to spend. Here the person asked --
they uploaded the file -- so the cost is theirs, the turn is theirs, and a
guest's import keeps counting against the guest pool ceiling, which is the
wall that stops a cleared cookie minting a fresh allowance.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import GuestSession, Resume, ResumeImport, User
from app.services.attachment import llm_content_for_upload
from app.services.chat_messages import (
    persist_assistant_message,
    persist_tool_message,
    persist_usage,
)
from app.services.extract import extract_upload_text
from app.services.import_qa import verify_render
from app.services.import_run import (
    ImportSuperseded,
    advance,
    fail,
    finish,
    set_render_check,
    touch,
)
from app.services.import_tools import import_tool_handlers, import_tool_specs
from app.services.llm import aiter_chat_edit
from app.services.llm.prompts import EDIT_TOOL
from app.services.metering import deferred_guard, metered
from app.services.revisions import set_draft_source
from app.services.s3 import get_object

logger = logging.getLogger(__name__)

# The import works through a whole agent-declared plan in one turn, which is
# a different shape of work than one chat message: a resume with more than a
# handful of sections needs more rounds than chat's shared default budget
# gives it, and the whole exchange -- plan, read, N edits, wrap-up -- takes
# longer than one chat round's timeout allows. See the Ruling in this
# feature's plan: silently inheriting chat's defaults truncates any resume
# past about five sections, and the symptom looks like a model failure
# rather than a configuration one.
#
# Raised from 14 to 20 for the render-check gate below: a 7-section import
# already spends close to the old budget (declare + read + 7 edits + close
# is 10), and `verify_render` plus up to `MAX_RENDER_REPAIRS` repair turns
# -- each its own read/edit/close -- needs real headroom on top of that,
# not just the one or two rounds a compile-only retry would have cost.
MAX_ROUNDS = 20
TIMEOUT_SECONDS = 180.0

# How many times `run_import` lets the agent try to fix a render-check
# failure before giving up. Bounded, not retried indefinitely: each round
# spends the person's own quota (see IMPORT_PURPOSE below), and a document
# still failing after three honest attempts has something structurally
# wrong that a fourth identical attempt is not going to find either --
# see `verify_render`'s docstring for why coverage this low this
# consistently is not noise.
MAX_RENDER_REPAIRS = 3

# What an import's model calls are billed as on `LlmCall`. Its own purpose
# rather than "chat", following the compile-repair loop's "repair": an import
# spends tokens on work the person never typed, so the admin overview's
# by-purpose breakdown has to be able to show it. See models.LLM_CALL_PURPOSES.
IMPORT_PURPOSE = "import"

# What goes in `resume_imports.error`: a code the progress card translates,
# never the raw exception text. The card is the only reader of that column,
# and it renders in three languages -- `str(exc)` there was both untranslated
# and a leak (`could not read the uploaded file
# ('uploads/<owner>/<resume>/<name>.pdf')` publishes our storage-key shape).
# The raw text is logged instead, which is where it was always useful. Keep
# these in step with `lib/import-progress.ts`'s `importErrorKey`.
UPLOAD_MISSING = "upload_missing"
UNEXPECTED = "unexpected"
# Set when `verify_render` is still not `ok` after MAX_RENDER_REPAIRS
# automatic repair attempts -- see the gate at the end of `run_import`.
RENDER_CHECK_FAILED = "render_check_failed"

IMPORT_INSTRUCTION = (
    "Import this uploaded resume into the Typst document.\n\n"
    "First, call declare_import_plan with the sections you actually found in "
    "the attached document, in the order you will fill them in. Decide for "
    "yourself how many sections there are and what to call each one -- "
    "nothing is fixed, and you must not assume any particular resume has "
    "any particular section.\n\n"
    "Then call read_typst to see the current source.\n\n"
    "Then work through your declared plan one section at a time: for each "
    "section, make exactly one apply_typst_edit call that fills it in from "
    "the attached document, passing that section's own key as "
    "apply_typst_edit's `step` argument. That is how the progress card "
    "knows which section you just finished -- there is no separate tool for "
    "reporting it.\n\n"
    "Watch for Typst syntax that is easy to type by accident inside prose "
    "copied from a resume: `/*` opens a comment that runs to the end of the "
    "*whole document* (not just the line) if it is never closed with `*/`, "
    "`@` starts a reference, and `[` opens a content block. Escape any of "
    "these that appear in the person's own text (`\\@`, `\\[`, `\\*`) rather "
    "than writing them bare.\n\n"
    "When every section from your plan has been applied, reply with a short "
    "summary of what you imported."
)


def _upload_filename(run: ResumeImport) -> str:
    """The original filename, out of a key shaped like
    `uploads/{owner}/{resume_id}/{original name}`.
    """
    return Path(run.file_key or "").name


def _owner(db: Session, resume: Resume) -> tuple[User | None, GuestSession | None]:
    """Who this import bills to. Both halves of the metering seam need it."""
    user = db.get(User, resume.user_id) if resume.user_id else None
    guest = db.get(GuestSession, resume.guest_session_id) if resume.guest_session_id else None
    return user, guest


def _owner_guard(user: User | None, guest: GuestSession | None):
    """The guard for one import.

    `purpose` stays `"chat"` here while the `LlmCall` row this import writes
    carries `"import"`, and the two are deliberately different. A Guard's
    purpose is not telemetry: `metering.Meter.record` uses it to decide
    whether a round counts as a *turn* against the weekly turn allowance
    (`purpose == "chat" and round_index == 0`), and `usage_counters` has no
    purpose column to tell them apart afterwards. Moving this to `"import"`
    would quietly stop an import consuming a turn -- a limit change dressed
    up as a telemetry one. The visible-cost requirement is satisfied by the
    `LlmCall` row, which is what the admin by-purpose breakdown reads.
    """
    return deferred_guard(
        user=user,
        guest=guest,
        purpose="chat",
        reason="resume import the person asked for; no HTTP caller to refuse",
    )


def _render_repair_prompt(result: dict) -> str:
    """One round's worth of feedback for a render-check repair turn.

    Follows `app.routers.chat`'s compile-repair loop's shape (state what
    went wrong, name the concrete hazards, say exactly what to do next) --
    see that module's `while new_source != original_source and repairs < 3`
    loop for the pattern this one is not inventing a variant of.

    Two shapes, matching `verify_render`'s two failure modes:
    - `compile_error` set: the source does not compile at all (should be
      rare here -- the turn already runs with `verify_compile=True`, which
      repairs this inline -- but `verify_render` can still hit it on its
      own, e.g. a last edit landing after the turn's own last compile).
    - otherwise, a coverage shortfall: say the number, and if there is a
      cliff (`cliff_at`; see `verify_render`'s docstring for why that is
      the bug's signature) lead with it and name the likely cause, because
      "everything after X is missing" is far more actionable than an
      unordered list of missing strings.
    """
    if result.get("compile_error"):
        return (
            "Typst compile failed while verifying the rendered page:\n"
            f"{result['compile_error']}\n"
            "Call read_typst first, then apply_typst_edit with the full "
            "corrected source."
        )
    lines = [
        "The rendered PDF is missing text that is present in the Typst "
        f"source (coverage: {result['coverage']:.0%}, "
        f"{result['found']}/{result['checked']} candidate strings found).",
    ]
    cliff_at = result.get("cliff_at")
    if cliff_at:
        lines.append(
            "Everything from this point in the source onward is missing "
            f'from the rendered page: "{cliff_at[:200]}". A likely cause is '
            "an unescaped special character earlier in the document: `/*` "
            "opens a Typst comment that runs to end-of-file, `@` starts a "
            "reference, and `[` opens a content block. Find the one that "
            "broke and escape it (`\\@`, `\\[`) or close the comment "
            "(`*/`) -- do not just delete the surrounding text."
        )
    else:
        lines.append("Missing strings, in source order:")
        lines.extend(f"- {m[:200]}" for m in result.get("missing") or [])
    lines.append(
        "Call read_typst to see the current source, fix it so the missing "
        "text renders, and call apply_typst_edit with the full corrected "
        "source."
    )
    return "\n".join(lines)


async def run_import(
    db: Session, resume: Resume, run: ResumeImport, *, model: str | None = None
) -> None:
    """Drive one import to completion. Never raises."""
    try:
        data = get_object(run.file_key)
        if data is None:
            # The one failure that is not an exception, so nothing else would
            # log it: without this the run just shows up as `failed` with no
            # trace of which key could not be read.
            logger.warning(
                "resume import found no readable upload resume_id=%s run_id=%s file_key=%r",
                resume.id,
                run.id,
                run.file_key,
            )
            fail(db, run, UPLOAD_MISSING)
            return

        filename = _upload_filename(run)
        extracted = extract_upload_text(filename, data)
        content = llm_content_for_upload(
            filename, data, instruction=IMPORT_INSTRUCTION, extracted=extracted
        )
        messages = [{"role": "user", "content": content}]

        user, guest = _owner(db, resume)
        async with metered(_owner_guard(user, guest)) as meter:

            async def drain(events) -> str:
                """Consume one turn's events -- the main import turn, or
                one render-check repair turn -- exactly the same way: bill
                usage, persist tool/assistant rows, advance the plan on a
                changed edit, keep the resume's draft source current, and
                keep the run's heartbeat alive. Returns the turn's final
                assistant text, which a repair prompt needs so the next
                turn's conversation reads as a continuation rather than a
                non sequitur (see `app.routers.chat`'s compile-repair loop,
                whose `relay` does the same bookkeeping for the same
                reason).
                """
                assistant_text = ""
                async for event in events:
                    touch(db, run)
                    kind = event.get("kind")
                    if kind == "usage":
                        # Both halves, exactly as the chat router does them:
                        # the counter (durable, enforces the allowance) and
                        # the LlmCall row (telemetry, priced at today's
                        # rates). A path that moves one without the other
                        # shows up in `usage.reconcile` as an admin gap for
                        # that window forever -- see `persist_usage`'s
                        # docstring.
                        persist_usage(db, resume.id, event, IMPORT_PURPOSE, user=user, guest=guest)
                        meter.record(event)
                    elif kind == "tool":
                        trace = event["trace"]
                        persist_tool_message(db, resume.id, trace)
                        if trace.get("name") == EDIT_TOOL:
                            tool_result = trace.get("result")
                            if isinstance(tool_result, dict) and tool_result.get("changed"):
                                advance(db, run, trace.get("arguments", {}).get("step"))
                    elif kind == "source":
                        set_draft_source(db, resume, event["typst_source"])
                        db.commit()
                        # Bump `version` *after* the source is committed.
                        # The editor repaints when `snapshot()`'s
                        # `source_hash` changes, and it only ever sees a
                        # snapshot that a version bump asked it to read:
                        # the bumps this event already did (`touch` above,
                        # `advance` on the tool event before it) all
                        # happened while the resume still held the previous
                        # source, and the next one of its own accord is a
                        # whole LLM round away. Without this the preview
                        # waits out that round -- or, if the agent's next
                        # edit reuses a done step key, longer still.
                        touch(db, run)
                    elif kind == "assistant":
                        assistant_text = event["content"] or assistant_text
                        persist_assistant_message(db, resume.id, assistant_text)
                return assistant_text

            assistant_text = await drain(
                aiter_chat_edit(
                    messages,
                    resume.typst_source,
                    model=model,
                    prefer_full_source=False,
                    verify_compile=True,
                    max_rounds=MAX_ROUNDS,
                    timeout=TIMEOUT_SECONDS,
                    extra_tools=import_tool_specs(),
                    tool_handlers=import_tool_handlers(db, run),
                )
            )

            # The gate, run immediately before finish(): a compile that
            # exits 0 is not proof the page shows what the source says (see
            # the module docstring and app.services.import_qa for the bug
            # this catches). `resume.typst_source` is current here --
            # `drain`'s "source" branch above writes it directly on every
            # changed edit, the main turn's included.
            set_render_check(db, run, "active")
            render_result = await asyncio.to_thread(verify_render, resume.typst_source)
            attempt = 0
            while not render_result["ok"] and attempt < MAX_RENDER_REPAIRS:
                attempt += 1
                set_render_check(db, run, "active", detail="repairing")
                # Fresh each attempt from the *original* `messages`, not a
                # growing chain across attempts -- same shape as
                # app.routers.chat's compile-repair loop, carrying forward
                # only the latest assistant reply and the latest problem
                # description.
                repair_messages = [
                    *messages,
                    {"role": "assistant", "content": assistant_text},
                    {"role": "user", "content": _render_repair_prompt(render_result)},
                ]
                assistant_text = await drain(
                    aiter_chat_edit(
                        repair_messages,
                        resume.typst_source,
                        model=model,
                        prefer_full_source=False,
                        verify_compile=True,
                        max_rounds=MAX_ROUNDS,
                        timeout=TIMEOUT_SECONDS,
                        extra_tools=import_tool_specs(),
                        tool_handlers=import_tool_handlers(db, run),
                    )
                )
                set_render_check(db, run, "active")
                render_result = await asyncio.to_thread(verify_render, resume.typst_source)

        if render_result["ok"]:
            set_render_check(db, run, "done")
            finish(db, run)
        else:
            # Three honest attempts found nothing left to try -- see
            # MAX_RENDER_REPAIRS. The missing list and the cliff are logged
            # (never put in `run.error`, same rule as every other failure
            # here -- see UPLOAD_MISSING/UNEXPECTED above), and the run ends
            # `failed` rather than looping a fourth time for nothing.
            logger.warning(
                "resume import render check failed after %d repair attempt(s) "
                "resume_id=%s run_id=%s coverage=%.2f missing=%r cliff_at=%r",
                attempt,
                resume.id,
                run.id,
                render_result.get("coverage", 0.0),
                render_result.get("missing"),
                render_result.get("cliff_at"),
            )
            fail(db, run, RENDER_CHECK_FAILED)
    except ImportSuperseded:
        # Not a failure: a newer run owns this resume now, and `touch` raised
        # so this runner would stop writing over it. Nothing to record -- the
        # supersession already wrote its own reason on the old row.
        logger.warning(
            "resume import superseded, stopping resume_id=%s run_id=%s",
            resume.id,
            run.id,
        )
    except Exception:
        # The raw text goes here and nowhere else. It reaches no user-facing
        # surface: it is untranslated in a trilingual product, and a provider
        # message or a storage key shaped like
        # `uploads/<owner>/<resume>/<name>.pdf` is nothing to show anyone. The
        # card renders a translated string from the stored code instead.
        logger.exception(
            "resume import failed resume_id=%s run_id=%s", resume.id, run.id
        )
        try:
            fail(db, run, UNEXPECTED)
        except Exception:
            logger.exception(
                "import_run.fail() itself raised; run_id=%s may be stuck running",
                run.id,
            )
