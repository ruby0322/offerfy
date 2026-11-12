"""Read-only health of the master resume behind matching.

Pure composition over the resume_facts readers. It never rebuilds and never
calls a provider: a page visit must not spend someone's credits. The dashboard
offers the metered refresh when `needs_rebuild` is true.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models import Resume, User
from app.services.resume_facts import (
    default_source,
    facts_for_user,
    facts_state,
    source_resume,
)


@dataclass(frozen=True)
class ResumeHealth:
    has_master: bool
    published: bool
    facts_state: Literal["missing", "stale", "fresh"]
    matching_revision_current: bool
    needs_rebuild: bool


def _master(db: Session, user: User) -> Resume | None:
    """The resume matching reads, resolved as resume_facts.bundle resolves it
    (a set pointer is honoured, else default_source), so this card and the
    staleness rule never disagree about which resume is meant. A person with
    masters but none published still has a master; show the first."""
    resume = source_resume(db, user)
    if resume is None and not user.profile_resume_id:
        resume = default_source(db, user)
    if resume is None and not user.profile_resume_id:
        resume = (
            db.query(Resume)
            .filter(Resume.user_id == user.id, Resume.kind == "master")
            .order_by(Resume.id)
            .first()
        )
    return resume


def health(db: Session, *, user: User) -> ResumeHealth:
    resume = _master(db, user)
    state = facts_state(db, user)
    if resume is None:
        # Nothing to rebuild from: not a problem the card should demand fixing.
        return ResumeHealth(False, False, state, False, False)
    facts = facts_for_user(db, user)
    published = resume.published_revision_id is not None
    current = (
        facts is not None
        and published
        and facts.source_revision_id == resume.published_revision_id
    )
    return ResumeHealth(
        has_master=True,
        published=published,
        facts_state=state,
        matching_revision_current=current,
        needs_rebuild=state != "fresh" or not current,
    )
