"""One ingest pass over the Workday boards.

Separate from `ingest_catalog` because this source is incremental: a posting we
already hold is reported back as still-live and never re-fetched, which is the
only way a provider that charges a request per posting fits in a cron job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.services.jobs.boards import load_workday_boards
from app.services.jobs.ingest import SourceResult, refreshed_nothing
from app.services.jobs.scope import ScopeRules, default_scope
from app.services.jobs.spotlight import rescore_all
from app.services.jobs.store import (
    expire_missing,
    known_source_ids,
    record_run,
    touch_jobs,
    upsert_jobs,
)
from app.services.jobs.workday import WorkdayBoard, fetch_workday


@dataclass(frozen=True)
class WorkdayResult:
    sources: tuple[SourceResult, ...]
    run_id: str

    @property
    def ok(self) -> tuple[SourceResult, ...]:
        return tuple(item for item in self.sources if item.status == "ok")

    @property
    def failed(self) -> tuple[SourceResult, ...]:
        return tuple(item for item in self.sources if item.status != "ok")

    @property
    def fetched(self) -> int:
        return sum(item.fetched for item in self.sources)

    @property
    def upserted(self) -> int:
        return sum(item.upserted for item in self.sources)

    @property
    def refreshed_nothing(self) -> bool:
        # A steady-state pass legitimately upserts zero -- every posting was
        # already held -- so "refreshed nothing" here means no board answered,
        # not no new postings. Using the shared rule on upserted alone would
        # fail every quiet day.
        return not self.ok


def ingest_workday_board(
    db: Session,
    client: httpx.Client,
    board: WorkdayBoard,
    *,
    run_id: str | None = None,
    rules: ScopeRules | None = None,
) -> SourceResult:
    started = datetime.now(timezone.utc)
    prefix = f"{board.slug}:"
    try:
        known = known_source_ids(db, source="workday", prefix=prefix)
        result = fetch_workday(client, board, known_ids=known, rules=rules)
    except Exception as exc:  # noqa: BLE001 - one dead board must not end the pass
        record_run(
            db,
            run_id=run_id,
            source="workday",
            board_slug=board.slug,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet=str(exc),
        )
        db.commit()
        return SourceResult("workday", board.slug, "error", error=str(exc))

    if result is None:
        record_run(
            db,
            run_id=run_id,
            source="workday",
            board_slug=board.slug,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet="board not found",
        )
        db.commit()
        return SourceResult("workday", board.slug, "error", error="board not found")

    upserted = upsert_jobs(db, result.new)
    # Not optional: these rows were skipped for their description, not for their
    # freshness. Without this they age out of validThrough and go noindex while
    # the job is still open.
    touch_jobs(db, source="workday", source_ids=result.still_live)
    expired = expire_missing(
        db, source="workday", seen_ids=result.seen_ids, prefix=prefix
    )
    record_run(
        db,
        run_id=run_id,
        source="workday",
        board_slug=board.slug,
        started_at=started,
        status="ok",
        ok_count=result.fetched,
        upserted_count=upserted,
        expired_count=expired,
    )
    db.commit()
    return SourceResult(
        "workday", board.slug, "ok", upserted=upserted, fetched=result.fetched
    )


def ingest_workday(db: Session, client: httpx.Client) -> WorkdayResult:
    import uuid

    run_id = str(uuid.uuid4())
    rules = default_scope()
    results = [
        ingest_workday_board(db, client, board, run_id=run_id, rules=rules)
        for board in load_workday_boards()
    ]
    rescore_all(db)
    return WorkdayResult(tuple(results), run_id=run_id)


__all__ = ["WorkdayResult", "ingest_workday", "ingest_workday_board", "refreshed_nothing"]
