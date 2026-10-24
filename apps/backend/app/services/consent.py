"""The consent ledger.

Every router reads consent through here, never by querying ConsentEvent, so
that "what is their current answer" has exactly one definition: the newest row
for that (subject, purpose). See ConsentEvent's docstring for why the table is
append-only.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.models import CONSENT_ACTIONS, CONSENT_PURPOSES, ConsentEvent, GuestSession, User

# The version of the policy a person is agreeing to. Must equal LEGAL_UPDATED
# in apps/frontend/components/legal/LegalDocument.tsx -- the policy text and
# its version belong together, and tests/test_consent.py fails on drift.
POLICY_VERSION = "2026-10-24"

# Purposes a signed-in person can turn on and off for themselves. 'service' is
# not here: withdrawing it means closing the account, which is the job of
# account deletion, not of a toggle.
SELF_SERVE_PURPOSES = ("talent_introduction",)


# How "newest" is ordered, for every reader in this module.
#
# created_at alone ties when two rows land in the same transaction, which the
# settings endpoint can do. id does not break that tie meaningfully -- _uuid()
# is random, so ordering by it is deterministic but arbitrary, and an arbitrary
# winner means a withdrawal can lose to a grant it arrived with. So a tie is
# resolved towards 'withdrawn': when we cannot tell which answer came last, the
# answer is no. id stays last so the row chosen is still stable.
_LATEST_FIRST = (
    ConsentEvent.created_at.desc(),
    case((ConsentEvent.action == "withdrawn", 0), else_=1).asc(),
    ConsentEvent.id.desc(),
)


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
    return query.order_by(*_LATEST_FIRST).first()


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


def has_consent_many(
    db: Session, user_ids: Iterable[str], *, purpose: str
) -> set[str]:
    """Which of these users' newest answer for this purpose was 'granted'.

    One query instead of N, ordered by exactly the _LATEST_FIRST that
    latest_event uses, then reduced to the first row per user in Python.

    Deliberately not a SQL `GROUP BY user_id HAVING max(created_at)` join: that
    cannot express the tie rule above, so it would sometimes read a withdrawal
    as a grant. Handing a withdrawn person to an employer is the one failure
    here that cannot be taken back, so the slower definition is the only one.
    """
    ids = list(user_ids)
    if not ids:
        return set()
    rows = (
        db.query(ConsentEvent)
        .filter(ConsentEvent.purpose == purpose, ConsentEvent.user_id.in_(ids))
        .order_by(*_LATEST_FIRST)
        .all()
    )
    seen: set[str] = set()
    granted: set[str] = set()
    for row in rows:
        if row.user_id is None or row.user_id in seen:
            continue
        seen.add(row.user_id)
        if row.action == "granted":
            granted.add(row.user_id)
    return granted


def consent_state(db: Session, user: User) -> dict[str, bool]:
    """Their current answer for every purpose they control themselves."""
    return {
        purpose: has_consent(db, user=user, purpose=purpose)
        for purpose in SELF_SERVE_PURPOSES
    }


# A 'withdrawn' row for talent_introduction is no longer the whole of the
# withdrawal. Both halves of the obligation now have a mechanism:
#
#   (a) drop them from the searchable index -- routers/consent.py deletes the
#       talent_profiles row, and app.services.profile.readable_profiles_query
#       requires live consent through has_consent_many on every read, so
#       neither path can serve a withdrawn person.
#   (b) notify everyone who already received their profile --
#       app.services.disclosure.pending_obligations derives who they are from
#       disclosure_events, and record_stop_use_notice records the discharge.
#
# Nothing here calls either one: this module answers what someone agreed to and
# nothing else.
