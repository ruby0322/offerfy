from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Resume, ResumeRevision, ResumeShare
from app.schemas import ShareState

COALESCE_WINDOW = timedelta(seconds=120)


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def latest_revision(db: Session, resume_id: str) -> ResumeRevision | None:
    return (
        db.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == resume_id)
        .order_by(ResumeRevision.created_at.desc(), ResumeRevision.id.desc())
        .first()
    )


def set_draft_source(
    db: Session,
    resume: Resume,
    source: str,
    *,
    now: datetime | None = None,
) -> ResumeRevision:
    when = now or datetime.now(timezone.utc)
    resume.typst_source = source
    resume.updated_at = when
    digest = source_hash(source)
    latest = latest_revision(db, resume.id)
    if latest is not None and latest.content_hash == digest:
        return latest
    if (
        latest is not None
        and latest.id != resume.published_revision_id
        and (when - _aware(latest.created_at)) < COALESCE_WINDOW
    ):
        latest.typst_source = source
        latest.content_hash = digest
        latest.created_at = when
        return latest
    row = ResumeRevision(
        resume_id=resume.id,
        typst_source=source,
        content_hash=digest,
        created_at=when,
    )
    db.add(row)
    db.flush()
    return row


def published_revision(db: Session, resume: Resume) -> ResumeRevision | None:
    if resume.published_revision_id is None:
        return None
    return db.get(ResumeRevision, resume.published_revision_id)


def unpublished_changes(db: Session, resume: Resume) -> bool:
    published = published_revision(db, resume)
    if published is None:
        return True
    return source_hash(resume.typst_source) != published.content_hash


def published_source(db: Session, resume: Resume) -> str | None:
    published = published_revision(db, resume)
    if published is None:
        return None
    return published.typst_source


def pin_current_draft(
    db: Session,
    resume: Resume,
    *,
    now: datetime | None = None,
) -> ResumeRevision:
    row = set_draft_source(db, resume, resume.typst_source, now=now)
    resume.published_revision_id = row.id
    db.flush()
    return row


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    dt = _aware(dt)
    return dt.isoformat()


def build_share_state(db: Session, resume: Resume) -> ShareState:
    share = db.query(ResumeShare).filter(ResumeShare.resume_id == resume.id).one_or_none()
    published = published_revision(db, resume)
    return ShareState(
        public=share is not None,
        token=share.token if share is not None else None,
        published_revision_id=resume.published_revision_id,
        published_at=_iso(published.created_at) if published is not None else None,
        unpublished_changes=unpublished_changes(db, resume),
    )


def backfill_existing_resumes(db: Session) -> None:
    now = datetime.now(timezone.utc)
    for resume in db.query(Resume).all():
        if latest_revision(db, resume.id) is None:
            row = ResumeRevision(
                resume_id=resume.id,
                typst_source=resume.typst_source,
                content_hash=source_hash(resume.typst_source),
                created_at=_aware(resume.created_at) if resume.created_at else now,
            )
            db.add(row)
            db.flush()
    db.flush()
    for share in db.query(ResumeShare).all():
        resume = db.get(Resume, share.resume_id)
        if resume is None or resume.published_revision_id:
            continue
        latest = latest_revision(db, resume.id)
        if latest is not None:
            resume.published_revision_id = latest.id
    db.flush()
