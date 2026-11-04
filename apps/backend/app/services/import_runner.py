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
  `app.routers.chat`'s `relay()` does for the live chat turn.
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

**Why this wraps `aiter_chat_edit` in `system_guard`.** The import spends
real provider tokens exactly like a chat turn does, and
`tests/test_metering.py::test_billable_call_sites_are_wrapped` is the
inventory that catches a new billable call site that forgot to be metered --
the exact failure mode `app/jobs/refresh_profiles.py` had before that module
existed. `preflight()` is the wrong half of the seam here: nobody is holding
an HTTP response open waiting on this turn, so refusing would only mean an
import 429s itself with nothing watching. `system_guard()` is the other
half, built for precisely that shape of caller, and demands a written
reason instead of a silent exemption.
"""

from __future__ import annotations

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
from app.services.import_run import ImportSuperseded, advance, fail, finish, touch
from app.services.import_tools import import_tool_handlers, import_tool_specs
from app.services.llm import aiter_chat_edit
from app.services.llm.prompts import EDIT_TOOL
from app.services.metering import metered, system_guard
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
MAX_ROUNDS = 14
TIMEOUT_SECONDS = 180.0

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
    return system_guard(
        user=user,
        guest=guest,
        purpose="chat",
        reason="server-initiated resume import; no HTTP caller to refuse",
    )


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
            async for event in aiter_chat_edit(
                messages,
                resume.typst_source,
                model=model,
                prefer_full_source=False,
                verify_compile=True,
                max_rounds=MAX_ROUNDS,
                timeout=TIMEOUT_SECONDS,
                extra_tools=import_tool_specs(),
                tool_handlers=import_tool_handlers(db, run),
            ):
                touch(db, run)
                kind = event.get("kind")
                if kind == "usage":
                    # Both halves, exactly as the chat router does them: the
                    # counter (durable, enforces the allowance) and the
                    # LlmCall row (telemetry, priced at today's rates). A path
                    # that moves one without the other shows up in
                    # `usage.reconcile` as an admin gap for that window
                    # forever -- see `persist_usage`'s docstring.
                    persist_usage(db, resume.id, event, IMPORT_PURPOSE, user=user, guest=guest)
                    meter.record(event)
                elif kind == "tool":
                    trace = event["trace"]
                    persist_tool_message(db, resume.id, trace)
                    if trace.get("name") == EDIT_TOOL:
                        result = trace.get("result")
                        if isinstance(result, dict) and result.get("changed"):
                            advance(db, run, trace.get("arguments", {}).get("step"))
                elif kind == "source":
                    set_draft_source(db, resume, event["typst_source"])
                    db.commit()
                elif kind == "assistant":
                    persist_assistant_message(db, resume.id, event["content"])

        finish(db, run)
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
