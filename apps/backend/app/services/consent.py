"""The consent ledger.

Every router reads consent through here, never by querying ConsentEvent, so
that "what is their current answer" has exactly one definition: the newest row
for that (subject, purpose). See ConsentEvent's docstring for why the table is
append-only.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CONSENT_ACTIONS, CONSENT_PURPOSES, ConsentEvent, GuestSession, User

# The version of the policy a person is agreeing to. Must equal LEGAL_UPDATED
# in apps/frontend/components/legal/LegalDocument.tsx -- the policy text and
# its version belong together, and tests/test_consent.py fails on drift.
POLICY_VERSION = "2026-10-23"

# Purposes a signed-in person can turn on and off for themselves. 'service' is
# not here: withdrawing it means closing the account, which is the job of
# account deletion, not of a toggle.
SELF_SERVE_PURPOSES = ("talent_introduction",)


def record_consent(
    db: Session,
    *,
    user: User | None = None,
    guest: GuestSession | None = None,
    purpose: str,
    action: str,
    locale: str,
    source: str,
    detail: str | None = None,
) -> ConsentEvent:
    """Append one decision. Never updates, never deletes.

    Deliberately not wrapped in try/except the way LlmCall telemetry is in the
    chat router: telemetry may be lost, a consent record may not. Callers add
    it to the request's session so it commits with whatever it is evidence
    for -- the User row at signup, the claimed resumes at claim.
    """
    if purpose not in CONSENT_PURPOSES:
        raise ValueError(f"unknown consent purpose: {purpose!r}")
    if action not in CONSENT_ACTIONS:
        raise ValueError(f"unknown consent action: {action!r}")
    if (user is None) == (guest is None):
        raise ValueError("record_consent needs exactly one of user or guest")
    event = ConsentEvent(
        user_id=user.id if user is not None else None,
        guest_session_id=guest.id if guest is not None else None,
        purpose=purpose,
        action=action,
        policy_version=POLICY_VERSION,
        locale=locale,
        source=source,
        detail=detail,
    )
    db.add(event)
    return event


def latest_event(
    db: Session,
    *,
    user: User | None = None,
    guest: GuestSession | None = None,
    purpose: str,
) -> ConsentEvent | None:
    query = db.query(ConsentEvent).filter(ConsentEvent.purpose == purpose)
    if user is not None:
        query = query.filter(ConsentEvent.user_id == user.id)
    elif guest is not None:
        query = query.filter(ConsentEvent.guest_session_id == guest.id)
    else:
        raise ValueError("latest_event needs one of user or guest")
    # created_at alone ties when two rows land in the same transaction, which
    # the settings endpoint can do; id breaks the tie deterministically.
    return query.order_by(ConsentEvent.created_at.desc(), ConsentEvent.id.desc()).first()


def has_consent(
    db: Session,
    *,
    user: User | None = None,
    guest: GuestSession | None = None,
    purpose: str,
) -> bool:
    """True when their newest answer for this purpose was 'granted'.

    Not "a granted row exists" -- that would read a withdrawal as consent.
    """
    event = latest_event(db, user=user, guest=guest, purpose=purpose)
    return event is not None and event.action == "granted"


def consent_state(db: Session, user: User) -> dict[str, bool]:
    """Their current answer for every purpose they control themselves."""
    return {
        purpose: has_consent(db, user=user, purpose=purpose)
        for purpose in SELF_SERVE_PURPOSES
    }


# TODO: once a talent pool exists, a 'withdrawn' row for talent_introduction
# must also (a) drop them from the searchable index and (b) notify everyone who
# already received their profile -- see the DisclosureEvent table in the plan.
# Today neither exists, so appending the row is the whole of the withdrawal.
