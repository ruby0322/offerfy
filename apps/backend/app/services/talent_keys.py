"""Credentials for the talent read API.

Issued by an operator and by nobody else. No route mints one except the admin
endpoint, there is no OAuth server, and there is no self-serve door --
apps/frontend/lib/agent-markdown.ts tells every agent that reads it exactly
that, and those statements have to stay true.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import TalentApiKey

# Makes a leaked credential greppable in a log, and lets the request dependency
# reject anything that is not one of ours without touching the database.
TALENT_KEY_PREFIX = "ofk_"


def new_talent_key() -> str:
    return TALENT_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_talent_key(raw: str) -> str:
    """Unsalted SHA-256, following GuestSession.key_hash.

    Correct here for the same reason it is correct there: the secret is 256
    bits from secrets.token_urlsafe, not a password someone chose, so key
    stretching buys nothing against guessing and lookup stays a single indexed
    equality. Do not replace this with bcrypt -- it would break the lookup
    without making the token any harder to guess.

    Deliberately not sharing hash_guest_key: two different kinds of secret
    should not share a hash namespace.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_live(key: TalentApiKey, *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    if key.revoked_at is not None:
        return False
    if key.expires_at is not None and _aware(key.expires_at) <= moment:
        return False
    return True


def mint_talent_key(
    db: Session,
    *,
    label: str,
    contact: str,
    created_by: str,
    expires_in_days: int | None = None,
) -> tuple[TalentApiKey, str]:
    """Create one credential. The raw token is returned once and never stored."""
    raw = new_talent_key()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days
        else None
    )
    key = TalentApiKey(
        key_hash=hash_talent_key(raw),
        label=label.strip()[:255],
        contact=contact.strip()[:320],
        created_by=created_by[:320],
        expires_at=expires_at,
    )
    db.add(key)
    db.flush()
    return key, raw


def key_for_token(db: Session, raw: str) -> TalentApiKey | None:
    """The live key this token names, or None. Never says which reason."""
    if not raw.startswith(TALENT_KEY_PREFIX):
        return None
    key = (
        db.query(TalentApiKey)
        .filter(TalentApiKey.key_hash == hash_talent_key(raw))
        .one_or_none()
    )
    if key is None or not is_live(key):
        return None
    return key
