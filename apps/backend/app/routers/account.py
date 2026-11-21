"""The signed-in person's own account profile.

Deliberately `/v1/me/account` and not `/v1/me/profile`. In this codebase
"profile" already means the talent profile at `/v1/me/talent-profile` -- the
derived, employer-facing one that a model writes and a withdrawal deletes. Two
endpoints a character apart, one of which is governed by a consent and the
other of which is not, is a mistake waiting to be made in a review.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import AgentApiKey, AgentToolCall, User
from app.schemas import (
    AccountOut,
    AccountUpdate,
    AgentKeyCreate,
    AgentKeyCreated,
    AgentKeyOut,
    AgentToolCallOut,
)
from app.services.account import AccountError, apply
from app.services.agent_keys import TooManyKeys, is_live, mint_agent_key
from app.services.mcp_tools import AUDIT_DISCLOSURE_TOOLS, AUDIT_PINNED_TOOLS

router = APIRouter()


def _out(user: User) -> AccountOut:
    return AccountOut(
        email=user.email,
        picture=user.picture,
        google_name=user.name,
        username=user.username,
        display_name=user.display_name,
        headline=user.headline,
        location=user.location,
        website=user.website,
        bio=user.bio,
    )


@router.get("/v1/me/account", response_model=AccountOut)
def get_account(user: User = Depends(require_user)):
    return _out(user)


@router.patch("/v1/me/account", response_model=AccountOut)
def patch_account(
    body: AccountUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """PATCH, not PUT: the form can send one field without clearing the rest.

    `exclude_unset` is what makes that true, and it is the reason the schema's
    fields are nullable rather than defaulting to "". An explicit null and an
    explicit "" both mean "clear this one"; omitting the key leaves it alone.
    """
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _out(user)
    try:
        apply(db, user, updates)
        db.flush()
    except AccountError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_field", "field": exc.field, "code": exc.code},
        ) from exc
    except IntegrityError as exc:
        # The unique index, not the SELECT in services.account: two requests
        # claiming the same handle at once both pass the read. 409 rather than
        # 422 so the client can tell "someone beat you to it" from "that is not
        # a valid handle".
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"error": "invalid_field", "field": "username", "code": "taken"},
        ) from exc
    return _out(user)


# --------------------------------------------------------------------------
# Agent keys
#
# Every route here is `require_user` -- the cookie -- and never
# `require_agent_caller`. A holder of an `ofa_` token must not be able to mint
# itself a successor or revoke the key that is limiting it; that is a decision
# for the signed-in person at their own keyboard.
#
# And every one carries `include_in_schema=False`, like the rest of this
# feature's routes: the Next rewrite publishes /api/openapi.json, so a route in
# that document is a route described on the open web -- here, the shape of the
# credential-minting call. The flag is per-route because this router's account
# routes are listed; `tests/test_account.py` asserts no agent-key path reaches
# `app.openapi()`.
# --------------------------------------------------------------------------

MAX_LABEL_LENGTH = 255

# The log is three windows merged, newest first, deduplicated. The spec names
# publish visibility as its main purpose and "the user does not know what the
# agent did" as an anti-goal, and one flat window could not deliver that: at
# the quota's 300 calls an hour a `publish_resume` scrolled out in minutes.
# Windowed rather than a "changes only" filter because the answer has to be
# visible without the person knowing to ask for it.
#
# **What this actually guarantees**, which is less than "pinned forever":
#   * the last CALL_LOG_DISCLOSURE_LIMIT publishes, unpublishes and exports are
#     shown whatever else the assistant did afterwards -- a user's own editing
#     cannot push a publish out, which one shared window did not prevent;
#   * the last CALL_LOG_PINNED_LIMIT changes of any kind likewise, except that
#     later *changes* can displace earlier ones;
#   * the last CALL_LOG_LIMIT calls of any kind, reads included.
# The hole that remains: an assistant that publishes or exports more than
# CALL_LOG_DISCLOSURE_LIMIT times pushes the oldest of those out of the log,
# and nothing here says so on screen. Say it, do not widen the claim.

#: Rows of any kind, newest first.
CALL_LOG_LIMIT = 50
#: Rows from `mcp_tools.AUDIT_PINNED_TOOLS` (everything that writes or exposes).
CALL_LOG_PINNED_LIMIT = 20
#: Rows from `mcp_tools.AUDIT_DISCLOSURE_TOOLS` (everything that reaches someone
#: other than the user). A window of its own so high-volume editing -- which is
#: also pinned -- cannot crowd out the publish the person came to look for.
CALL_LOG_DISCLOSURE_LIMIT = 10
#: Revoked and expired keys listed alongside the live ones, most recently
#: retired first. Bounded so a long-lived account's list cannot grow forever.
RETIRED_KEYS_LISTED = 10


def _bad_request(code: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": code})


def _own_key(db: Session, user: User, key_id: str) -> AgentApiKey:
    """404, not 403, for a key that is not yours: whether someone else holds a
    given id is not something to confirm."""
    key = (
        db.query(AgentApiKey)
        .filter(AgentApiKey.id == key_id, AgentApiKey.user_id == user.id)
        .one_or_none()
    )
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return key


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes and Postgres aware ones, while a value
    just assigned in this session is aware either way. Comparing a mix raises,
    so normalise before ordering on them."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _retired_at(key: AgentApiKey) -> datetime:
    """When a key stopped working, for ordering the retired ones.

    The **earliest** of its stamps, not the latest: a key revoked today with an
    expiry thirty days out stopped working today, and ordering it by that
    expiry would sort it ahead of every key retired since -- which is what
    decides whose log falls off the end of the bounded list above.

    Deliberately no "has this stamp taken effect yet" filter. The earliest
    stamp is always one that has: anything earlier than an effective stamp is
    itself effective, and a key whose stamps are *all* in the future is live,
    so `is_live` has already kept it out of this function. Such a filter was
    written and then removed -- under `min` it is a branch no test can reach.
    """
    stamps = [_utc(t) for t in (key.revoked_at, key.expires_at) if t is not None]
    return min(stamps) if stamps else _utc(key.created_at)


@router.get(
    "/v1/me/agent-keys", response_model=list[AgentKeyOut], include_in_schema=False
)
def list_agent_keys(db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Live keys oldest first, then the most recently retired ones.

    Not `live_keys` alone. Revoking is what a person does on *suspecting*
    misuse, which is exactly when they want to read that key's log -- and while
    this listed live keys only, revoking made the key disappear and took its
    log with it. `revoked_at` is in the payload so the card can mark a key
    retired rather than offering to revoke it again.
    """
    rows = (
        db.query(AgentApiKey)
        .filter(AgentApiKey.user_id == user.id)
        .order_by(AgentApiKey.created_at, AgentApiKey.id)
        .all()
    )
    live = [key for key in rows if is_live(key)]
    retired = sorted(
        (key for key in rows if not is_live(key)), key=_retired_at, reverse=True
    )
    return live + retired[:RETIRED_KEYS_LISTED]


@router.post(
    "/v1/me/agent-keys", response_model=AgentKeyCreated, include_in_schema=False
)
def create_agent_key(
    body: AgentKeyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """The only response that ever carries the raw token."""
    label = body.label.strip()
    # Checked here because the service stores whatever it is given, and an
    # expiry of 0 would silently mean "never" while a negative one mints a key
    # that is dead on arrival.
    if not label or len(label) > MAX_LABEL_LENGTH:
        raise _bad_request("invalid_label")
    if body.expires_in_days is not None and body.expires_in_days <= 0:
        raise _bad_request("invalid_expiry")
    try:
        key, raw = mint_agent_key(
            db, user=user, label=label, expires_in_days=body.expires_in_days
        )
    except TooManyKeys as exc:
        raise _bad_request("too_many_keys") from exc
    return AgentKeyCreated(
        id=key.id,
        label=key.label,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        token=raw,
    )


@router.delete(
    "/v1/me/agent-keys/{key_id}", status_code=204, include_in_schema=False
)
def revoke_agent_key(
    key_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    key = _own_key(db, user, key_id)
    if key.revoked_at is not None:
        # Already revoked: the same answer as a key that is not yours, and the
        # same detail, which is what the card reads to tell "it was already
        # gone" from a 404 that came from somewhere else. (The list does now
        # show revoked keys, marked, but it offers no Revoke button for one --
        # reaching here means a stale tab, not a visible control.)
        raise HTTPException(status_code=404, detail="Key not found")
    key.revoked_at = datetime.now(timezone.utc)
    db.flush()
    return Response(status_code=204)


@router.get(
    "/v1/me/agent-keys/{key_id}/calls",
    response_model=list[AgentToolCallOut],
    include_in_schema=False,
)
def agent_key_calls(
    key_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    """Three windows merged newest first and deduplicated: recent calls of any
    kind, recent changes, and recent disclosures. The block above
    CALL_LOG_LIMIT says what that does and does not guarantee."""
    key = _own_key(db, user, key_id)
    rows = (
        db.query(AgentToolCall)
        .filter(AgentToolCall.key_id == key.id, AgentToolCall.user_id == user.id)
        .order_by(AgentToolCall.created_at.desc(), AgentToolCall.id.desc())
    )

    def window(tools, limit):
        q = rows if tools is None else rows.filter(AgentToolCall.tool.in_(sorted(tools)))
        return q.limit(limit).all()

    merged = {
        row.id: row
        for row in (
            window(None, CALL_LOG_LIMIT)
            + window(AUDIT_PINNED_TOOLS, CALL_LOG_PINNED_LIMIT)
            + window(AUDIT_DISCLOSURE_TOOLS, CALL_LOG_DISCLOSURE_LIMIT)
        )
    }
    return sorted(
        merged.values(), key=lambda row: (row.created_at, row.id), reverse=True
    )
