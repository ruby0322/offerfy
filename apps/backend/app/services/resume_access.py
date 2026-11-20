"""Which resumes a signed-in user may reach, and how one is created for them.

This is the user-only half of what `routers/resumes.py` does for a browser
caller. The router also serves guest sessions; this module never does. The MCP
tools call it, and so does the router's signed-in branch, so the two cannot
drift on what "my resumes" means.

`owned_by_user` is deliberately NOT `deps.load_resume_for_owner`. That one
accepts a guest as the owner, which is right for a browser holding a guest
cookie and wrong for an agent key, which has no guest identity and must never
reach a guest's row. A missing row, another user's row and a guest-owned row all
raise the same 404 with the same detail: a 403 would confirm the row exists
(`routers/talent.py::_not_found` settled that for this codebase).
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Resume, User
from app.services.revisions import set_draft_source


def resumes_of(db: Session, user: User) -> list[Resume]:
    """The user's own resumes. Never falls back to a guest session."""
    return db.query(Resume).filter(Resume.user_id == user.id).all()


def owned_by_user(db: Session, user: User, resume_id: str) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


def create_for_user(
    db: Session,
    user: User,
    *,
    title: str,
    locale: str,
    source: str,
    typst_source: str,
    upload_s3_key: str | None = None,
    import_status: str = "idle",
) -> Resume:
    """Add a resume owned by `user`: `user_id` set, `guest_session_id` NULL."""
    resume = Resume(
        title=title,
        locale=locale,
        source=source,
        typst_source=typst_source,
        upload_s3_key=upload_s3_key,
        import_status=import_status,
        user_id=user.id,
        guest_session_id=None,
    )
    db.add(resume)
    db.flush()
    set_draft_source(db, resume, typst_source)
    return resume
