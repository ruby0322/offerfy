"""Per-user credentials for the MCP surface.

A signed-in user mints one of these for their own AI assistant; whoever holds
it acts as that user and nobody else. This is not the operator-issued
TalentApiKey (see app.services.talent_keys): different table, different
prefix, different holder.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AgentApiKey, User

# Greppable in a log, and lets the request dependency reject anything that is
# not one of ours without touching the database. Never "ofk_": that prefix
# belongs to the talent credential, and a token of one kind must not be
# mistakable for the other.
AGENT_KEY_PREFIX = "ofa_"

MAX_LIVE_KEYS = 5


class TooManyKeys(ValueError):
    """The user already holds MAX_LIVE_KEYS live keys.

    A ValueError subclass so existing callers that catch ValueError still work,
    but callers that mean "the cap" catch this one: a different ValueError out
    of mint_agent_key must not be reported to a person as a key-limit problem.
    """


def new_agent_key() -> str:
    return AGENT_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_agent_key(raw: str) -> str:
    """Unsalted SHA-256 of the raw token.

    Correct for a token like this: the secret is 256 bits from
    secrets.token_urlsafe, not a password someone chose, so key stretching
    buys nothing against guessing, and lookup stays a single indexed equality
    on key_hash. Do not replace this with bcrypt -- salting makes the hash
    differ per row, so the token could no longer be looked up by its hash,
    and it would not make a 256-bit secret any harder to guess.

    Deliberately not calling hash_talent_key even though the bytes would be
    identical today: each kind of secret owns its own hash function, so a
    change to one (a pepper, a new algorithm) cannot silently re-key the other.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; Postgres hands back aware ones.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_live(key: AgentApiKey, *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    if key.revoked_at is not None:
        return False
    if key.expires_at is not None and _aware(key.expires_at) <= moment:
        return False
    return True


def live_keys(db: Session, user: User) -> list[AgentApiKey]:
    """This user's keys that still authenticate, oldest first."""
    rows = (
        db.query(AgentApiKey)
        .filter(AgentApiKey.user_id == user.id, AgentApiKey.revoked_at.is_(None))
        .order_by(AgentApiKey.created_at, AgentApiKey.id)
        .all()
    )
    # Expiry is checked in Python so it shares one definition with is_live.
    return [k for k in rows if is_live(k)]


def mint_agent_key(
    db: Session,
    *,
    user: User,
    label: str,
    expires_in_days: int | None = None,
) -> tuple[AgentApiKey, str]:
    """Create one credential. The raw token is returned once and never stored.

    Raises TooManyKeys when the user already holds
    MAX_LIVE_KEYS live ones; revoke one first.
    """
    if len(live_keys(db, user)) >= MAX_LIVE_KEYS:
        raise TooManyKeys("Too many keys")
    raw = new_agent_key()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days
        else None
    )
    key = AgentApiKey(
        user_id=user.id,
        key_hash=hash_agent_key(raw),
        label=label.strip()[:255],
        expires_at=expires_at,
    )
    db.add(key)
    db.flush()
    return key, raw


def key_for_token(db: Session, raw: str) -> AgentApiKey | None:
    """The live key this token names, or None. Never says which reason."""
    if not raw.startswith(AGENT_KEY_PREFIX):
        return None
    key = (
        db.query(AgentApiKey)
        .filter(AgentApiKey.key_hash == hash_agent_key(raw))
        .one_or_none()
    )
    if key is None or not is_live(key):
        return None
    return key
