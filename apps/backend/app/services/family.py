from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Resume
from app.services.revisions import latest_revision, set_draft_source

TITLE_MAX = 255
LABEL_MAX = 255
CONTEXT_MAX = 8000
FORK_SOURCES = ("master", "current")


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
    fork_from: str = "master",
) -> Resume:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Title is required")
    if len(clean_title) > TITLE_MAX:
        raise ValueError("Title is too long")
    if fork_from not in FORK_SOURCES:
        raise ValueError("fork_from must be 'master' or 'current'")
    master = family_master(db, member)
    # The family stays one level deep: a tailor forked from another tailor still
    # hangs off the master, and only the copied draft differs. The revision it
    # forked from records the real origin.
    origin = member if fork_from == "current" else master
    forked = latest_revision(db, origin.id)
    tailor = Resume(
        title=clean_title,
        typst_source=origin.typst_source,
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
    set_draft_source(db, tailor, origin.typst_source)
    return tailor
