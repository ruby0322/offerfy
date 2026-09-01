from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Job, JobIngestRun
from app.services.jobs.types import NormalizedJob


def upsert_jobs(
    db: Session,
    jobs: list[NormalizedJob],
    *,
    now: datetime | None = None,
) -> int:
    if not jobs:
        return 0
    stamp = now or datetime.now(timezone.utc)
    count = 0
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
        count += 1
    return count


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
    ok_count: int = 0,
    error_count: int = 0,
    upserted_count: int = 0,
    expired_count: int = 0,
    error_snippet: str | None = None,
) -> JobIngestRun:
    run = JobIngestRun(
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
