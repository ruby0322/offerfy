"""The quota grant ledger.

Every caller reads grants through here, never by querying QuotaGrant, so that
"what is this person's bonus right now" has exactly one definition -- the same
reason services.consent exists, and the same reason the quota can never
disagree with it. See QuotaGrant's docstring for why the table carries no
consumption of its own.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import GRANT_REASONS, QuotaGrant, User


def _window(now: datetime | None) -> tuple[datetime, datetime]:
    """The window a grant is measured against.

    Imported lazily to keep services.usage -> services.grants the only edge
    between the two; usage.balance is the caller that matters and it already
    owns the clock.
    """
    from app.services.usage import window_start

    start = window_start(now)
    return start, start + timedelta(days=7)


def _active(start: datetime, end: datetime):
    """Grants that count for the window [start, end).

    Overlap with the window, not "valid at this instant", and the reason is
    that a ceiling must never move *down* inside a window: used_uusd for that
    window cannot move back, so a grant lapsing on Wednesday would turn
    Monday's legitimate spend into debt. Overlap means a grant that expires
    mid-week still covers the week it expired in, and one issued mid-week
    covers the whole of it -- generous only in the direction that cannot
    surprise anyone.

    Revocation is the deliberate exception: it is point-in-time and bites
    immediately, because it is the lever for abuse and an admin stopping
    someone cannot be asked to wait for Monday. That does mean revoking
    mid-window can leave remaining_uusd negative for spend that was allowed
    when it happened -- which services.usage.Balance.exhausted already handles
    the same way it handles the overrun of a final turn.
    """
    return (
        QuotaGrant.revoked_at.is_(None),
        QuotaGrant.starts_at < end,
        (QuotaGrant.expires_at.is_(None)) | (QuotaGrant.expires_at > start),
    )


def active_grants(
    db: Session, *, user: User | None, now: datetime | None = None
) -> list[QuotaGrant]:
    """This user's grants counting for the current window, newest first."""
    if user is None:
        return []
    start, end = _window(now)
    return list(
        db.scalars(
            select(QuotaGrant)
            .where(QuotaGrant.user_id == user.id, *_active(start, end))
            .order_by(QuotaGrant.created_at.desc(), QuotaGrant.id.desc())
        )
    )


def granted_credits(
    db: Session, *, user: User | None, now: datetime | None = None
) -> int:
    """Credits to add to this user's tier ceiling. 0 when there are none.

    Summed in the database rather than over active_grants(), because balance()
    calls this on every pre-flight and does not need the rows.
    """
    if user is None:
        return 0
    start, end = _window(now)
    total = db.scalar(
        select(func.coalesce(func.sum(QuotaGrant.credits), 0)).where(
            QuotaGrant.user_id == user.id, *_active(start, end)
        )
    )
    return int(total or 0)


def all_grants(db: Session, *, user: User) -> list[QuotaGrant]:
    """Every grant ever issued to this user, newest first.

    For the admin detail view, which has to show the lapsed and revoked ones
    too -- a ceiling that dropped is exactly the thing someone is looking at
    this list to explain.
    """
    return list(
        db.scalars(
            select(QuotaGrant)
            .where(QuotaGrant.user_id == user.id)
            .order_by(QuotaGrant.created_at.desc(), QuotaGrant.id.desc())
        )
    )


def issue_grant(
    db: Session,
    *,
    user: User,
    credits: int,
    reason: str,
    issued_by: User | None,
    detail: str | None = None,
    starts_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> QuotaGrant:
    """Add one grant. Added to the session, not committed.

    Validated here as well as by the table's CHECK constraints: the constraints
    are the guarantee, but a 400 naming the field beats an IntegrityError at
    commit time for the admin typing it in.
    """
    if credits <= 0:
        raise ValueError("credits must be positive")
    if reason not in GRANT_REASONS:
        raise ValueError(f"unknown reason: {reason}")
    start = starts_at or datetime.now(timezone.utc)
    if expires_at is not None and expires_at <= start:
        raise ValueError("expires_at must be after starts_at")
    grant = QuotaGrant(
        user_id=user.id,
        credits=credits,
        reason=reason,
        detail=detail,
        starts_at=start,
        expires_at=expires_at,
        issued_by_user_id=None if issued_by is None else issued_by.id,
    )
    db.add(grant)
    return grant


def revoke_grant(
    db: Session, *, grant: QuotaGrant, now: datetime | None = None
) -> QuotaGrant:
    """Stamp a grant revoked. Idempotent -- an already-revoked one keeps its
    first timestamp, so re-revoking cannot rewrite when the ceiling dropped.
    """
    if grant.revoked_at is None:
        grant.revoked_at = now or datetime.now(timezone.utc)
        db.add(grant)
    return grant
