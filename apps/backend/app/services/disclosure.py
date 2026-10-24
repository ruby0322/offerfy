"""Who received whose profile, and who still has to be told to stop.

Two append-only tables and one derived question. Nothing here updates or
deletes a row: the point of the ledger is that it can still answer "who holds
this person's profile" after the profile, the key, and even the account are
gone.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    DisclosureEvent,
    StopUseNotice,
    TalentApiKey,
    TalentProfile,
    User,
)
from app.services.consent import POLICY_VERSION, latest_event

PURPOSE = "talent_introduction"

# A recipient who already holds exactly this payload is not a new obligation,
# so an identical read inside this window appends nothing. Expiring it at all
# means a reader who keeps pulling a profile for months leaves a trail that
# shows it.
DEDUP_WINDOW = timedelta(hours=24)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def record_disclosure(
    db: Session,
    *,
    profile: TalentProfile,
    key: TalentApiKey,
    scope: str,
    route: str,
) -> DisclosureEvent | None:
    """Append that this recipient received this profile. None when deduplicated.

    The recipient's label and contact are copied in rather than looked up
    through api_key_id later, because the row has to name a human to write to
    even after the key is gone.
    """
    cutoff = datetime.now(timezone.utc) - DEDUP_WINDOW
    existing = (
        db.query(DisclosureEvent.id)
        .filter(
            DisclosureEvent.api_key_id == key.id,
            DisclosureEvent.user_id == profile.user_id,
            DisclosureEvent.scope == scope,
            DisclosureEvent.profile_content_hash == profile.content_hash,
            DisclosureEvent.created_at >= cutoff,
        )
        .first()
    )
    if existing is not None:
        return None
    event = DisclosureEvent(
        user_id=profile.user_id,
        api_key_id=key.id,
        recipient_label=key.label,
        recipient_contact=key.contact,
        purpose=PURPOSE,
        scope=scope,
        profile_content_hash=profile.content_hash,
        policy_version=POLICY_VERSION,
        route=route[:128],
    )
    db.add(event)
    return event


def record_disclosures(
    db: Session,
    *,
    profiles: Sequence[TalentProfile],
    key: TalentApiKey,
    scope: str,
    route: str,
) -> int:
    written = 0
    for profile in profiles:
        if record_disclosure(
            db, profile=profile, key=key, scope=scope, route=route
        ) is not None:
            written += 1
    db.flush()
    return written


def disclosure_count(db: Session, user: User) -> int:
    return (
        db.query(func.count(DisclosureEvent.id))
        .filter(DisclosureEvent.user_id == user.id)
        .scalar()
        or 0
    )


def last_disclosed_at(db: Session, user: User) -> datetime | None:
    value = (
        db.query(func.max(DisclosureEvent.created_at))
        .filter(DisclosureEvent.user_id == user.id)
        .scalar()
    )
    return _aware(value) if value else None


def recipients_of(db: Session, user: User) -> list[dict[str, Any]]:
    """Everyone who has ever received this person's profile."""
    rows = (
        db.query(
            DisclosureEvent.api_key_id,
            DisclosureEvent.recipient_label,
            DisclosureEvent.recipient_contact,
            func.min(DisclosureEvent.created_at),
            func.max(DisclosureEvent.created_at),
            func.count(DisclosureEvent.id),
        )
        .filter(DisclosureEvent.user_id == user.id)
        .group_by(
            DisclosureEvent.api_key_id,
            DisclosureEvent.recipient_label,
            DisclosureEvent.recipient_contact,
        )
        .all()
    )
    return [
        {
            "api_key_id": api_key_id,
            "recipient_label": label,
            "recipient_contact": contact,
            "first_disclosed_at": _aware(first),
            "last_disclosed_at": _aware(last),
            "disclosure_count": count,
        }
        for api_key_id, label, contact, first, last, count in rows
    ]


def pending_obligations(db: Session) -> list[dict[str, Any]]:
    """Everyone we still owe a stop-use notice, newest withdrawal first.

    This is the whole of "we require anyone who already has it to stop using
    it". For each person whose newest talent_introduction answer is
    'withdrawn', every distinct recipient who received the profile *before*
    that withdrawal, minus those already sent a notice since it.

    Consent is read through app.services.consent, never by querying
    ConsentEvent here, so "newest answer" keeps one definition.
    """
    subjects = {
        row[0]
        for row in db.query(DisclosureEvent.user_id)
        .filter(DisclosureEvent.user_id.isnot(None))
        .distinct()
        .all()
    }
    out: list[dict[str, Any]] = []
    for user_id in subjects:
        user = db.get(User, user_id)
        if user is None:
            continue
        event = latest_event(db, user=user, purpose=PURPOSE)
        if event is None or event.action != "withdrawn":
            continue
        withdrawn_at = _aware(event.created_at)
        rows = (
            db.query(
                DisclosureEvent.api_key_id,
                DisclosureEvent.recipient_label,
                DisclosureEvent.recipient_contact,
                func.min(DisclosureEvent.created_at),
                func.max(DisclosureEvent.created_at),
                func.count(DisclosureEvent.id),
            )
            .filter(
                DisclosureEvent.user_id == user_id,
                DisclosureEvent.created_at < event.created_at,
            )
            .group_by(
                DisclosureEvent.api_key_id,
                DisclosureEvent.recipient_label,
                DisclosureEvent.recipient_contact,
            )
            .all()
        )
        for api_key_id, label, contact, first, last, count in rows:
            told = (
                db.query(StopUseNotice.id)
                .filter(
                    StopUseNotice.user_id == user_id,
                    StopUseNotice.recipient_contact == contact,
                    StopUseNotice.created_at >= event.created_at,
                )
                .first()
            )
            if told is not None:
                continue
            out.append(
                {
                    "user_id": user_id,
                    "user_email": user.email,
                    "withdrawn_at": withdrawn_at,
                    "consent_event_id": event.id,
                    "api_key_id": api_key_id,
                    "recipient_label": label,
                    "recipient_contact": contact,
                    "first_disclosed_at": _aware(first),
                    "last_disclosed_at": _aware(last),
                    "disclosure_count": count,
                }
            )
    out.sort(key=lambda row: row["withdrawn_at"], reverse=True)
    return out


def record_stop_use_notice(
    db: Session,
    *,
    user: User,
    recipient_contact: str,
    sent_by: str,
    api_key_id: str | None = None,
    detail: str | None = None,
) -> StopUseNotice:
    """Record that an operator told one recipient to stop. Appends, never updates."""
    event = latest_event(db, user=user, purpose=PURPOSE)
    notice = StopUseNotice(
        user_id=user.id,
        api_key_id=api_key_id,
        recipient_contact=recipient_contact,
        withdrawn_consent_event_id=event.id if event is not None else None,
        channel="manual_email",
        sent_by=sent_by,
        detail=detail,
    )
    db.add(notice)
    db.flush()
    return notice


__all__ = [
    "DEDUP_WINDOW",
    "PURPOSE",
    "disclosure_count",
    "last_disclosed_at",
    "pending_obligations",
    "record_disclosure",
    "record_disclosures",
    "record_stop_use_notice",
    "recipients_of",
]
