"""The signed-in person's own account profile.

Deliberately `/v1/me/account` and not `/v1/me/profile`. In this codebase
"profile" already means the talent profile at `/v1/me/talent-profile` -- the
derived, employer-facing one that a model writes and a withdrawal deletes. Two
endpoints a character apart, one of which is governed by a consent and the
other of which is not, is a mistake waiting to be made in a review.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import User
from app.schemas import AccountOut, AccountUpdate
from app.services.account import AccountError, apply

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
