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
from app.services.jobs.scope import ScopeRules, default_scope, title_in_scope
from app.services.jobs.smartrecruiters import fetch_smartrecruiters
from app.services.jobs.store import expire_missing, record_run, upsert_jobs
from app.services.jobs.spotlight import rescore_all
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
    # What the board returned before the scope rules; fetched - upserted is how
    # many it had that we do not carry.
    fetched: int = 0
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
    def fetched(self) -> int:
        return sum(item.fetched for item in self.sources)

    @property
    def refreshed_nothing(self) -> bool:
        return refreshed_nothing(sources_ok=len(self.ok), upserted=self.upserted)


def ingest_board(
    db: Session,
    client: httpx.Client,
    board: Board,
    *,
    run_id: str | None = None,
    rules: ScopeRules | None = None,
) -> SourceResult:
    started = datetime.now(timezone.utc)
    try:
        fetched = _fetch(client, board, rules)
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
    # Scope before seen_ids, deliberately: a posting we no longer carry must drop
    # out of seen_ids so expire_missing deactivates it on this very pass. Filtering
    # afterwards would leave every out-of-scope page we ever ingested indexed
    # forever. smartrecruiters has already scoped itself -- it has to, to avoid a
    # detail request per posting -- so this is a no-op for it.
    kept = [job for job in fetched if title_in_scope(job.title, rules)]
    upserted = upsert_jobs(db, kept)
    expired = expire_missing(
        db,
        source=board.source,
        seen_ids={job.source_id for job in kept},
        prefix=f"{board.slug}:",
    )
    record_run(
        db,
        run_id=run_id,
        source=board.source,
        board_slug=board.slug,
        started_at=started,
        status="ok",
        # Pre-filter count on purpose: ok_count - upserted_count is how many the
        # scope rules dropped, which /admin/jobs surfaces without a new column.
        ok_count=len(fetched),
        upserted_count=upserted,
        expired_count=expired,
    )
    db.commit()
    return SourceResult(
        board.source, board.slug, "ok", upserted=upserted, fetched=len(fetched)
    )


def ingest_catalog(db: Session, client: httpx.Client) -> CatalogResult:
    # One id for the whole pass, so ~119 per-board rows can be read back as a
    # single thing. Minted here because this is the only place that knows where
    # a pass begins and ends.
    run_id = str(uuid.uuid4())
    rules = default_scope()
    results: list[SourceResult] = []
    for board in load_boards():
        results.append(ingest_board(db, client, board, run_id=run_id, rules=rules))
    rescore_all(db)
    return CatalogResult(tuple(results), run_id=run_id)


def _fetch(
    client: httpx.Client, board: Board, rules: ScopeRules | None = None
) -> list[NormalizedJob] | None:
    if board.source == "greenhouse":
        return fetch_greenhouse(client, board.slug, board.company)
    if board.source == "lever":
        return fetch_lever(client, board.slug, board.company)
    if board.source == "ashby":
        return fetch_ashby(client, board.slug, board.company)
    if board.source == "smartrecruiters":
        return fetch_smartrecruiters(client, board.slug, board.company, rules=rules)
    raise ValueError(f"unsupported board source: {board.source}")
