"""Turning a resume's public link on and off.

The link is the `ResumeShare` row, not `published_revision_id`: pinning a
revision alone creates no link at all (see `revisions.build_share_state`, which
reports `public` as "a share row exists"). Turning public pins the current draft
too, because `/v1/shares/{token}` serves the pinned revision and 404s without one.

Pinning is also the third gate of the recruiter-facing talent profile
(`profile.readable_profiles_query`), and turning public off deliberately leaves
it in place, exactly as the website's toggle does. Both the router and the agent
tools go through here so the two cannot drift apart.

Callers own the commit; this flushes.
"""

import secrets

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Resume, ResumeShare
from app.schemas import ShareState
from app.services.revisions import build_share_state, pin_current_draft


def new_share_token(db: Session) -> str:
    for _ in range(8):
        token = secrets.token_urlsafe(16)
        exists = db.query(ResumeShare.id).filter(ResumeShare.token == token).one_or_none()
        if exists is None:
            return token
    raise HTTPException(status_code=500, detail="Could not allocate share token")


def set_public(db: Session, resume: Resume, public: bool) -> ShareState:
    share = db.query(ResumeShare).filter(ResumeShare.resume_id == resume.id).one_or_none()
    if public:
        if share is None:
            share = ResumeShare(resume_id=resume.id, token=new_share_token(db))
            db.add(share)
            db.flush()
        if resume.published_revision_id is None:
            pin_current_draft(db, resume)
        return build_share_state(db, resume)
    if share is not None:
        db.delete(share)
        db.flush()
    return build_share_state(db, resume)
