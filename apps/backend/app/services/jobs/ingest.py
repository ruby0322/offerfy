from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.jobs.ashby import fetch_ashby
from app.services.jobs.boards import load_boards
from app.services.jobs.boilerplate import normalize_company, recompute_boilerplate
from app.services.jobs.enrich import enrich_jobs
from app.services.jobs.greenhouse import fetch_greenhouse
from app.services.jobs.lever import fetch_lever
from app.services.jobs.scope import ScopeRules, default_scope, title_in_scope
from app.services.jobs.smartrecruiters import fetch_smartrecruiters
from app.services.jobs.store import expire_missing, record_run, upsert_jobs
from app.services.jobs.spotlight import rescore_all
from app.models import Job
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
    upserted_ids = upsert_jobs(db, kept)
    # Enrichment is derived state, not the posting itself: a bug here must
    # never cost us the posting or the commit, only the facets row (which just
    # makes that posting invisible to a *filtered* query, not to /jobs
    # unfiltered). So the exception is swallowed and surfaced on the run row
    # instead of being allowed to propagate past this point.
    #
    # The savepoint is load-bearing, not decorative: a *database*-level failure
    # (a DataError on a too-long column, a missing job_facets table) leaves the
    # session rollback-pending, so without it the bare except would swallow the
    # message and then the very next statement -- expire_missing, below --
    # would raise PendingRollbackError straight out of ingest_board and abort
    # the whole 119-board pass. begin_nested() confines the unwind to the facet
    # writes, leaving the outer transaction usable so expire_missing,
    # record_run and the commit all still land.
    enrich_error: str | None = None
    try:
        with db.begin_nested():
            enrich_jobs(db, job_ids=upserted_ids)
    except Exception as exc:  # noqa: BLE001 - must never fail the pass
        enrich_error = f"enrich_jobs: {exc}"
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
        upserted_count=len(upserted_ids),
        expired_count=expired,
        error_snippet=enrich_error,
    )
    db.commit()
    return SourceResult(
        board.source, board.slug, "ok", upserted=len(upserted_ids), fetched=len(fetched)
    )


def refresh_boilerplate(db: Session, *, now: datetime | None = None) -> int:
    """Recompute every company's boilerplate and re-enrich the companies it moved.

    Returns the number of facet rows rewritten. Flushes, never commits.
    `recompute_boilerplate` reports NORMALIZED company names, so postings are
    matched on `normalize_company(Job.company)`, never the raw string.
    """
    changed = recompute_boilerplate(db, now=now)
    if not changed:
        return 0
    ids_by_company: dict[str, list[str]] = {}
    for job_id, company in db.execute(
        select(Job.id, Job.company).where(Job.is_active.is_(True))
    ):
        key = normalize_company(company)
        if key in changed:
            ids_by_company.setdefault(key, []).append(job_id)
    written = 0
    for ids in ids_by_company.values():
        written += enrich_jobs(db, job_ids=ids, now=now)
    return written


def ingest_catalog(db: Session, client: httpx.Client) -> CatalogResult:
    # One id for the whole pass, so ~119 per-board rows can be read back as a
    # single thing. Minted here because this is the only place that knows where
    # a pass begins and ends.
    run_id = str(uuid.uuid4())
    rules = default_scope()
    results: list[SourceResult] = []
    # Before the board loop, so each board's own enrichment already sees the
    # fresh boilerplate set.
    #
    # The re-enrichment inside refresh_boilerplate is not optional. Both
    # enrich_jobs call sites pass only newly-upserted ids, and ingest_workday
    # enriches only `new_ids` -- its existing rows are never revisited. Five of
    # the six largest employers in the catalogue are Workday boards (Micron
    # 2,731 active postings, NVIDIA 1,846, Applied Materials 1,813, Intel,
    # NXP), so without this call their existing rows would keep facets derived
    # from stale boilerplate forever, even though the description_hash gate
    # (over the stripped text) has already moved.
    #
    # Savepoint + swallow-and-record, same contract and reason as the
    # enrich_jobs call in ingest_board: a database-level failure leaves the
    # session rollback-pending, and a plain try/except would let the next
    # statement abort the 119-board pass. Costs the facets, never the pass.
    started = datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            refresh_boilerplate(db)
    except Exception as exc:  # noqa: BLE001 - must never fail the pass
        record_run(
            db,
            run_id=run_id,
            source="boilerplate",
            board_slug=None,
            started_at=started,
            status="error",
            error_count=1,
            error_snippet=f"refresh_boilerplate: {exc}",
        )
        db.commit()
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
