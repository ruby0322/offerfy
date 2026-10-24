"""The talent profile: building one, and getting rid of one.

A profile is derived state. It exists only while its owner's newest answer for
talent_introduction is 'granted', and it holds no contact details -- see
scrub_contacts, and the absence of any field for one in
app.schemas.TalentProfilePayload.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import TalentProfile, User


def profile_for_user(db: Session, user: User) -> TalentProfile | None:
    return (
        db.query(TalentProfile).filter(TalentProfile.user_id == user.id).one_or_none()
    )


def delete_profile_for_user(db: Session, user: User) -> bool:
    """Stop holding the derived copy. True when there was one.

    Called on withdrawal, and when the source pointer moves. The read path
    checks live consent too, so this is defence in depth -- but it is also
    data minimisation, which a consent check is not: we promised to stop
    passing the profile on, and the cheapest way to keep that promise is to
    not have it.

    Deliberately does not touch disclosure_events. The record of who already
    received the profile has to outlive the profile, which is why that table
    has no foreign key to this row.
    """
    profile = profile_for_user(db, user)
    if profile is None:
        return False
    db.delete(profile)
    db.flush()
    return True
