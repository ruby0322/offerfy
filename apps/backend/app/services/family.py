from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.revisions import latest_revision, set_draft_source

TITLE_MAX = 255
LABEL_MAX = 255
CONTEXT_MAX = 8000


def family_master(db: Session, resume: Resume) -> Resume:
    if resume.kind == "tailor" and resume.parent_resume_id:
        master = db.get(Resume, resume.parent_resume_id)
        if master is not None:
            return master
    return resume


def family_tailors(db: Session, master: Resume) -> list[Resume]:
    return (
        db.query(Resume)
        .filter(Resume.parent_resume_id == master.id)
        .order_by(Resume.created_at.asc(), Resume.id.asc())
        .all()
    )


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped[:limit] or None


def create_tailor(
    db: Session,
    member: Resume,
    *,
    title: str | None,
    target_label: str | None = None,
    target_context: str | None = None,
) -> Resume:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Title is required")
    if len(clean_title) > TITLE_MAX:
        raise ValueError("Title is too long")
    master = family_master(db, member)
    forked = latest_revision(db, master.id)
    tailor = Resume(
        title=clean_title,
        typst_source=master.typst_source,
        source="create",
        locale=master.locale,
        user_id=master.user_id,
        guest_session_id=master.guest_session_id,
        kind="tailor",
        parent_resume_id=master.id,
        forked_from_revision_id=forked.id if forked else None,
        target_label=_clip(target_label, LABEL_MAX),
        target_context=_clip(target_context, CONTEXT_MAX),
    )
    db.add(tailor)
    db.flush()
    set_draft_source(db, tailor, master.typst_source)
    return tailor
