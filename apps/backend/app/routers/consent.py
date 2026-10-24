from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_user
from app.models import User
from app.schemas import ConsentState, ConsentUpdate
from app.services.consent import (
    POLICY_VERSION,
    SELF_SERVE_PURPOSES,
    consent_state,
    record_consent,
)
from app.services.profile import delete_profile_for_user

router = APIRouter()


def _state(db: Session, user: User) -> ConsentState:
    return ConsentState(consents=consent_state(db, user), policy_version=POLICY_VERSION)


@router.get("/v1/me/consents", response_model=ConsentState)
def get_consents(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _state(db, user)


@router.put("/v1/me/consents/{purpose}", response_model=ConsentState)
def put_consent(
    purpose: str,
    body: ConsentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Append their decision for one purpose.

    PUT with a boolean body mirrors PUT /v1/resumes/{id}/share. It reads like
    a setter but it appends: the ledger keeps every answer they have ever given.
    """
    if purpose not in SELF_SERVE_PURPOSES:
        raise HTTPException(status_code=404, detail="Unknown consent purpose")
    record_consent(
        db,
        user=user,
        purpose=purpose,
        action="granted" if body.granted else "withdrawn",
        locale=user.locale,
        source="settings",
    )
    if not body.granted and purpose == "talent_introduction":
        # The first half of what the privacy policy promises a withdrawal does.
        # The second half -- requiring whoever already has it to stop using it
        # -- is derived from disclosure_events and discharged by an operator;
        # see app.services.disclosure.pending_obligations.
        delete_profile_for_user(db, user)
    db.flush()
    return _state(db, user)
