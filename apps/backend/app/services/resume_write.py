"""Guarded writes to a resume's Typst source: one field, or the whole document.

Both entry points run the same five steps in the same order, and the order is
the point:

1. compare the caller's hash with the stored source's (409 if they differ);
2. `db.commit()`, releasing the pooled connection;
3. compile the candidate off the event loop (a source Typst rejects is a 400);
4. `db.refresh` and compare the hash a second time;
5. `set_draft_source(..., coalesce=False)`, then commit.

`apply_edit` is the body of `POST /v1/resumes/{id}/edit`, moved here unchanged
so the MCP `edit_resume` tool and the editor share it. `write_source` is the
same discipline applied to a whole replacement document; it backs the MCP
`put_resume_source` tool.

`PUT /v1/resumes/{id}` is a third, deliberately different path: it is the
editor's source tab, it has no hash guard and no compile gate, and it stays that
way. Two write paths with different safety is a recorded decision. Anything an
agent does goes through this module instead.

Nothing here sleeps, polls or retries: the MCP transport holds an in-flight
slot for the whole call.
"""

import asyncio
import dataclasses
import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Resume
from app.schemas import PreviewPages
from app.schemas_editing import IntentIn
from app.services.revisions import set_draft_source, source_hash
from app.services.typst_anchors import anchor_bands
from app.services.typst_compile import compile_preview_pages
from app.services.typst_intents import IntentError, apply_intent
from app.services.typst_projection import parse_projection

logger = logging.getLogger(__name__)

_STALE = "The document changed; reload it."

# The largest `write_source` accepts, in UTF-8 bytes. A real resume is around 10 KB;
# 256 KiB is some 25x that, so no honest document meets it. It exists to bound the
# compile, not to survive it: three Typst subprocesses run for up to 30s each, and an
# agent in a loop can hand us a megabyte at a time. Refused before the compile.
MAX_SOURCE_BYTES = 256 * 1024


def edit_error(status: int, code: str, detail: str, reason: str = "") -> HTTPException:
    """A refusal the client can word for itself.

    `detail` is one English sentence from the service; `reason` is the machine-readable
    half, present where a code covers several quite different refusals (`unsupported`
    covers four for `insert_item` alone) and the client has to pick the sentence in the
    person's own language. Absent when there is nothing to distinguish.
    """
    body = {"code": code, "detail": detail}
    if reason:
        body["reason"] = reason
    return HTTPException(status_code=status, detail=body)


def _preview_pages(source: str, with_bands: bool = False) -> PreviewPages:
    """Compile `source` to preview pages; raises HTTPException if Typst rejects it.

    Shared with the preview routes, which import it from here.
    """
    pages = compile_preview_pages(source)
    if with_bands and pages["text_layers"]:
        # Where each line unit sits, for the editor to highlight. Best effort like the
        # text layer: a preview without bands is still a preview, and the eval is a
        # second subprocess that must not turn a good compile into a failed request.
        try:
            bands = anchor_bands(
                source, parse_projection(source), layers=pages["text_layers"])
            pages["bands"] = {
                uid: [dataclasses.asdict(b) for b in rows] for uid, rows in bands.items()}
        except Exception:
            logger.warning("anchor bands unavailable", exc_info=True)
    return PreviewPages(**pages)


async def _compile_gate(candidate: str, with_bands: bool):
    """The compile is the gate and the preview in one: it raises HTTPException on a
    source Typst rejects. Off the event loop, as `switch_template` does it."""
    try:
        return await asyncio.to_thread(_preview_pages, candidate, with_bands)
    except HTTPException as exc:
        if exc.status_code != 400:
            # Typst missing (503), timed out (504) or silent (500): not the
            # person's edit breaking the document.
            raise
        first = str(exc.detail).strip().splitlines()[:1]
        raise edit_error(400, "compile_failed", first[0] if first else "")


def _land(db: Session, resume: Resume, candidate: str, expected_hash: str) -> None:
    """Steps 4 and 5: re-read, re-check, write."""
    # A chat edit or a second tab can land while the compile runs; re-read and
    # re-check rather than overwrite it. This is what makes releasing the
    # connection before the compile safe.
    db.refresh(resume)
    if source_hash(resume.typst_source) != expected_hash:
        raise edit_error(409, "stale_hash", _STALE)

    # Never coalesced. `set_draft_source` merges into the latest revision inside
    # its window whoever wrote it, and the request's hash cannot say whose that
    # was: it equals the latest revision's hash whenever the draft is in sync.
    # So "coalesce only when hash == latest.content_hash" always coalesces, and
    # a field edit right after a chat reply would rewrite the reply's revision.
    # No `restamp_content`: the snapshot did not change, the Typst did, so
    # re-keying it would make a snapshot without this edit claim to describe the
    # edited document, and the next template switch would emit the old text. Left
    # alone it is stale, and that switch re-extracts.
    set_draft_source(db, resume, candidate, coalesce=False)
    db.commit()


async def apply_edit(db: Session, resume: Resume, intent: IntentIn) -> dict:
    """Apply one field edit. Every guard runs before anything is written.

    The projection is parsed here, from the resume's current source -- never
    taken from the client -- so the offsets `apply_intent` writes at are the
    ones the stored text actually has.

    Returns `hash`, `typst_source`, `projection` (a plain dict) and `preview`.
    """
    current = resume.typst_source
    if intent.hash != source_hash(current):
        raise edit_error(409, "stale_hash", _STALE)
    try:
        candidate = apply_intent(current, parse_projection(current), intent)
    except IntentError as exc:
        raise edit_error(400, exc.code, exc.detail, exc.reason)

    # Release the pool connection before the compile: whatever SELECT loaded the
    # resume opened a transaction, and three 30s subprocess timeouts (SVG, PDF and
    # the anchor eval; 90s worst case) must not hold a slot (a starved pool takes
    # down even /healthz). Everything needed is already in plain strings; the
    # object is only re-read after the compile, in `_land`.
    db.commit()

    preview = await _compile_gate(candidate, True)
    _land(db, resume, candidate, intent.hash)
    return {
        "hash": source_hash(candidate),
        "typst_source": candidate,
        "projection": dataclasses.asdict(parse_projection(candidate)),
        "preview": preview,
    }


async def write_source(db: Session, resume: Resume, *, source: str, expected_hash: str) -> dict:
    """Replace the whole document, if it is still the one the caller read.

    The same five steps as `apply_edit`. A source that is empty or blank is
    refused outright: Typst compiles it happily, and a blank resume is never
    what a caller meant to store.
    """
    if expected_hash != source_hash(resume.typst_source):
        raise edit_error(409, "stale_hash", _STALE)
    if not source.strip():
        raise edit_error(400, "empty_source", "The document cannot be empty.")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise edit_error(
            400, "source_too_large",
            f"The document is larger than {MAX_SOURCE_BYTES // 1024} KiB.")

    # Release the pooled connection before the compile; see `apply_edit`.
    db.commit()

    preview = await _compile_gate(source, False)
    _land(db, resume, source, expected_hash)
    return {"hash": source_hash(source), "typst_source": source, "preview": preview}
