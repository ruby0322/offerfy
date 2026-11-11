from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Job, JobIngestRun
from app.services.jobs.types import NormalizedJob

# How long after we last confirmed a posting exists we are still willing to
# vouch for it. Keep in step with JOB_FRESHNESS_DAYS in
# apps/frontend/lib/seo.ts, which derives JobPosting.validThrough from the same
# window and noindexes anything past it.
JOB_FRESHNESS_DAYS = 30


def freshness_cutoff(now: datetime | None = None) -> datetime:
    """Oldest last_seen_at we will still advertise to a crawler.

    is_active is not a substitute: it only flips when an ingest run sees the
    posting gone, so a stalled ingest keeps every row active indefinitely while
    its markup expires underneath it.
    """
    return (now or datetime.now(timezone.utc)) - timedelta(days=JOB_FRESHNESS_DAYS)


def upsert_jobs(
    db: Session,
    jobs: list[NormalizedJob],
    *,
    now: datetime | None = None,
) -> list[str]:
    """Insert-or-update each job, returning the `Job.id` of every row touched.

    Returns ids rather than a bare count so a caller can hand them straight to
    `enrich.enrich_jobs(job_ids=...)` without a second, per-posting query to
    look them back up. `Job.id`'s default (`_uuid`) is only applied by the ORM
    at INSERT time, so a brand new row's id is not actually set until this
    flushes -- which is why the ids are read only after the one `db.flush()`
    below, not inside the loop. `len(result)` is the old count for callers
    that only ever wanted that.

    Flushes but does not commit, the same contract `enrich.enrich_jobs`
    follows: the caller still owns the transaction and its rollback.
    """
    if not jobs:
        return []
    stamp = now or datetime.now(timezone.utc)
    rows: list[Job] = []
    for item in jobs:
        row = (
            db.query(Job)
            .filter(Job.source == item.source, Job.source_id == item.source_id)
            .one_or_none()
        )
        if row is None:
            row = Job(
                source=item.source,
                source_id=item.source_id,
                company=item.company,
                title=item.title,
                first_seen_at=stamp,
            )
            db.add(row)
        row.company = item.company
        row.title = item.title
        row.location = item.location
        row.remote = item.remote
        row.apply_url = item.apply_url
        row.source_url = item.source_url
        row.description_text = item.description_text
        row.description_html = item.description_html
        row.posted_at = item.posted_at
        row.last_seen_at = stamp
        row.is_active = True
        rows.append(row)
    db.flush()
    return [row.id for row in rows]


def touch_jobs(
    db: Session,
    *,
    source: str,
    source_ids: set[str],
    now: datetime | None = None,
) -> int:
    """Mark known postings as still live without rewriting their content.

    For a source where the description costs its own request (workday), we skip
    that request for a posting we already hold. But skipping it must not mean
    skipping the row: last_seen_at is what keeps a posting inside its
    validThrough window, so a row we never touch goes noindex in 30 days while
    the job is still open. This bumps the freshness clock and nothing else.
    """
    if not source_ids:
        return 0
    stamp = now or datetime.now(timezone.utc)
    rows = (
        db.query(Job)
        .filter(Job.source == source, Job.source_id.in_(source_ids))
        .all()
    )
    for row in rows:
        row.last_seen_at = stamp
        row.is_active = True
    return len(rows)


def known_source_ids(db: Session, *, source: str, prefix: str) -> set[str]:
    """source_ids we already hold for one board, so a caller can skip re-fetching
    their descriptions."""
    rows = (
        db.query(Job.source_id)
        .filter(Job.source == source, Job.source_id.startswith(prefix))
        .all()
    )
    return {row[0] for row in rows}


def expire_missing(
    db: Session,
    *,
    source: str,
    seen_ids: set[str],
    prefix: str | None = None,
) -> int:
    """Mark active jobs not in seen_ids as inactive. No-op when fetch failed (caller skips)."""
    query = db.query(Job).filter(Job.source == source, Job.is_active.is_(True))
    if prefix:
        query = query.filter(Job.source_id.startswith(prefix))
    expired = 0
    for row in query.all():
        if row.source_id not in seen_ids:
            row.is_active = False
            expired += 1
    return expired


def record_run(
    db: Session,
    *,
    source: str,
    board_slug: str | None,
    started_at: datetime,
    status: str,
    run_id: str | None = None,
    ok_count: int = 0,
    error_count: int = 0,
    upserted_count: int = 0,
    expired_count: int = 0,
    error_snippet: str | None = None,
) -> JobIngestRun:
    run = JobIngestRun(
        run_id=run_id,
        source=source,
        board_slug=board_slug,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        status=status,
        ok_count=ok_count,
        error_count=error_count,
        upserted_count=upserted_count,
        expired_count=expired_count,
        error_snippet=(error_snippet or "")[:2000] or None,
    )
    db.add(run)
    return run
