from __future__ import annotations

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


def ingest_board(db: Session, client: httpx.Client, board: Board) -> None:
    started = datetime.now(timezone.utc)
    try:
        fetched = _fetch(client, board)
    except Exception as exc:
        record_run(
            db,
            source=board.source,
            board_slug=board.slug,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet=str(exc),
        )
        db.commit()
        return
    if fetched is None:
        record_run(
            db,
            source=board.source,
            board_slug=board.slug,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet="board not found",
        )
        db.commit()
        return
    upserted = upsert_jobs(db, fetched)
    expired = expire_missing(
        db,
        source=board.source,
        seen_ids={job.source_id for job in fetched},
        prefix=f"{board.slug}:",
    )
    record_run(
        db,
        source=board.source,
        board_slug=board.slug,
        started_at=started,
        status="ok",
        ok_count=len(fetched),
        upserted_count=upserted,
        expired_count=expired,
    )
    db.commit()


def ingest_taiwanjobs(db: Session, client: httpx.Client) -> None:
    started = datetime.now(timezone.utc)
    try:
        fetched = fetch_taiwanjobs(client)
    except Exception as exc:
        record_run(
            db,
            source="taiwanjobs",
            board_slug=None,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet=str(exc),
        )
        db.commit()
        return
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
        source="taiwanjobs",
        board_slug=None,
        started_at=started,
        status="ok",
        ok_count=len(fetched),
        upserted_count=upserted,
        expired_count=expired,
    )
    db.commit()


def ingest_catalog(db: Session, client: httpx.Client, *, include_taiwanjobs: bool = True) -> None:
    for board in load_boards():
        ingest_board(db, client, board)
    if include_taiwanjobs:
        ingest_taiwanjobs(db, client)
    rescore_all(db)


def _fetch(client: httpx.Client, board: Board) -> list[NormalizedJob] | None:
    if board.source == "greenhouse":
        return fetch_greenhouse(client, board.slug, board.company)
    if board.source == "lever":
        return fetch_lever(client, board.slug, board.company)
    if board.source == "ashby":
        return fetch_ashby(client, board.slug, board.company)
    raise ValueError(f"unsupported board source: {board.source}")
