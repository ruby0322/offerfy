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
from app.services.agent_keys import TooManyKeys, live_keys, mint_agent_key

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
# --------------------------------------------------------------------------

MAX_LABEL_LENGTH = 255
CALL_LOG_LIMIT = 50


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


@router.get("/v1/me/agent-keys", response_model=list[AgentKeyOut])
def list_agent_keys(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return live_keys(db, user)


@router.post("/v1/me/agent-keys", response_model=AgentKeyCreated)
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


@router.delete("/v1/me/agent-keys/{key_id}", status_code=204)
def revoke_agent_key(
    key_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    key = _own_key(db, user, key_id)
    if key.revoked_at is not None:
        # Already gone: say what the list says.
        raise HTTPException(status_code=404, detail="Key not found")
    key.revoked_at = datetime.now(timezone.utc)
    db.flush()
    return Response(status_code=204)


@router.get("/v1/me/agent-keys/{key_id}/calls", response_model=list[AgentToolCallOut])
def agent_key_calls(
    key_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    key = _own_key(db, user, key_id)
    return (
        db.query(AgentToolCall)
        .filter(AgentToolCall.key_id == key.id, AgentToolCall.user_id == user.id)
        .order_by(AgentToolCall.created_at.desc(), AgentToolCall.id.desc())
        .limit(CALL_LOG_LIMIT)
        .all()
    )
