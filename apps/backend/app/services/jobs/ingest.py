from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.services.jobs.ashby import fetch_ashby
from app.services.jobs.boards import load_boards
from app.services.jobs.greenhouse import fetch_greenhouse
from app.services.jobs.lever import fetch_lever
from app.services.jobs.store import expire_missing, record_run, upsert_jobs
from app.services.jobs.spotlight import rescore_all
from app.services.jobs.taiwanjobs import fetch_taiwanjobs
from app.services.jobs.types import Board, NormalizedJob


def refreshed_nothing(*, sources_ok: int, upserted: int) -> bool:
    """Whether a pass left the catalog ageing towards expiry.

    The single rule behind both the CLI's exit code and the admin view's
    "failed" verdict, so the unit and the dashboard can never disagree.
    """
    return sources_ok == 0 or upserted == 0


@dataclass(frozen=True)
class SourceResult:
    source: str
    board_slug: str | None
    status: str
    upserted: int = 0
    error: str | None = None


@dataclass(frozen=True)
class CatalogResult:
    """What one catalog pass actually managed to do.

    Per-source failures are recorded rather than raised so one dead board does
    not abandon the rest of the catalog. The caller still needs to be able to
    tell "refreshed the catalog" from "refreshed nothing", because only the
    second one lets every posting age out of its validThrough window.
    """

    sources: tuple[SourceResult, ...]
    run_id: str

    @property
    def ok(self) -> tuple[SourceResult, ...]:
        return tuple(item for item in self.sources if item.status == "ok")

    @property
    def failed(self) -> tuple[SourceResult, ...]:
        return tuple(item for item in self.sources if item.status != "ok")

    @property
    def upserted(self) -> int:
        return sum(item.upserted for item in self.sources)

    @property
    def refreshed_nothing(self) -> bool:
        return refreshed_nothing(sources_ok=len(self.ok), upserted=self.upserted)


def ingest_board(
    db: Session, client: httpx.Client, board: Board, *, run_id: str | None = None
) -> SourceResult:
    started = datetime.now(timezone.utc)
    try:
        fetched = _fetch(client, board)
    except Exception as exc:
        record_run(
            db,
            run_id=run_id,
            source=board.source,
            board_slug=board.slug,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet=str(exc),
        )
        db.commit()
        return SourceResult(board.source, board.slug, "error", error=str(exc))
    if fetched is None:
        record_run(
            db,
            run_id=run_id,
            source=board.source,
            board_slug=board.slug,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet="board not found",
        )
        db.commit()
        return SourceResult(board.source, board.slug, "error", error="board not found")
    upserted = upsert_jobs(db, fetched)
    expired = expire_missing(
        db,
        source=board.source,
        seen_ids={job.source_id for job in fetched},
        prefix=f"{board.slug}:",
    )
    record_run(
        db,
        run_id=run_id,
        source=board.source,
        board_slug=board.slug,
        started_at=started,
        status="ok",
        ok_count=len(fetched),
        upserted_count=upserted,
        expired_count=expired,
    )
    db.commit()
    return SourceResult(board.source, board.slug, "ok", upserted=upserted)


def ingest_taiwanjobs(
    db: Session, client: httpx.Client, *, run_id: str | None = None
) -> SourceResult:
    started = datetime.now(timezone.utc)
    try:
        fetched = fetch_taiwanjobs(client)
    except Exception as exc:
        record_run(
            db,
            run_id=run_id,
            source="taiwanjobs",
            board_slug=None,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet=str(exc),
        )
        db.commit()
        return SourceResult("taiwanjobs", None, "error", error=str(exc))
    upserted = upsert_jobs(db, fetched)
    expired = 0
    if fetched:
        expired = expire_missing(
            db,
            source="taiwanjobs",
            seen_ids={job.source_id for job in fetched},
        )
    record_run(
        db,
        run_id=run_id,
        source="taiwanjobs",
        board_slug=None,
        started_at=started,
        status="ok",
        ok_count=len(fetched),
        upserted_count=upserted,
        expired_count=expired,
    )
    db.commit()
    return SourceResult("taiwanjobs", None, "ok", upserted=upserted)


def ingest_catalog(
    db: Session, client: httpx.Client, *, include_taiwanjobs: bool = True
) -> CatalogResult:
    # One id for the whole pass, so ~119 per-board rows can be read back as a
    # single thing. Minted here because this is the only place that knows where
    # a pass begins and ends.
    run_id = str(uuid.uuid4())
    results: list[SourceResult] = []
    for board in load_boards():
        results.append(ingest_board(db, client, board, run_id=run_id))
    if include_taiwanjobs:
        results.append(ingest_taiwanjobs(db, client, run_id=run_id))
    rescore_all(db)
    return CatalogResult(tuple(results), run_id=run_id)


def _fetch(client: httpx.Client, board: Board) -> list[NormalizedJob] | None:
    if board.source == "greenhouse":
        return fetch_greenhouse(client, board.slug, board.company)
    if board.source == "lever":
        return fetch_lever(client, board.slug, board.company)
    if board.source == "ashby":
        return fetch_ashby(client, board.slug, board.company)
    raise ValueError(f"unsupported board source: {board.source}")
