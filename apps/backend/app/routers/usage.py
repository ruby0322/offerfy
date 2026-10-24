"""What this caller has left of their weekly AI allowance."""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, owner_context
from app.models import User
from app.schemas import UsageOut, UsageTierOut, UsageTiersOut
from app.services.usage import (
    CREDIT_UUSD,
    EDIT_UUSD,
    GUEST,
    TIERS,
    approx_edits_left,
    balance,
    limits_enabled,
    window_end,
)

router = APIRouter()


@router.get("/v1/usage", response_model=UsageOut)
def read_usage(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Deliberately not `/v1/me/usage`.

    Every `/v1/me/*` route in this codebase means "signed in", and this one has
    to answer for a guest -- the people with the smallest allowance are exactly
    the ones who most need to see it. So `owner_context` with `ensure=False`
    rather than `require_user`: a first-time visitor with no cookie yet gets
    the guest tier with nothing used, which is the true answer to what they
    get, rather than a 401. `ensure=False` so that merely asking does not mint
    a guest session.
    """
    user, guest = owner_context(request, response, db, user, ensure=False)
    bal = balance(db, user, guest)
    tier = TIERS[bal.tier]
    return UsageOut(
        tier=bal.tier,
        enforced=limits_enabled(),
        reset_at=bal.reset_at.isoformat(),
        limit_credits=bal.limit_credits,
        used_credits=bal.used_credits,
        remaining_credits=bal.remaining_credits,
        turn_limit=bal.turn_limit,
        turns_used=bal.turns_used,
        turns_remaining=bal.turns_remaining,
        turns_per_hour=tier.turns_per_hour,
        approx_edits_left=approx_edits_left(db, user, guest, bal),
        exhausted=bal.exhausted,
        unmeasured_rounds=bal.unmeasured_rounds,
    )


# Order is the argument the public page makes: what you get with no account,
# then what an account adds, then what opting into matching adds.
_TIER_ORDER = ("guest", "registered", "matching")


@router.get("/v1/usage/tiers", response_model=UsageTiersOut)
def read_tiers():
    """Every published tier. No subject, no cookie, no database.

    The public /usage page reads this instead of holding its own copy of the
    numbers, so the published allowance cannot drift from the enforced one.
    tests/test_usage.py asserts this endpoint equals services.usage.TIERS.
    """
    tiers = []
    for name in _TIER_ORDER:
        tier = TIERS[name]
        # What the allowance is worth in edits, at the measured cost of one.
        # Deliberately not `tier.credits`: that was only ever equal by accident
        # of the original peg, and once the history trim cut a turn to a third
        # of its old cost it understated by ~3x -- the page said "about 20"
        # where the budget bought about 55.
        #
        # Rounded to the nearest 5, because "about 39" is not an "about" and
        # the number is a blend over people whose turns differ by nearly 2x.
        # The editor shows each person their own trailing rate instead; a
        # public page has no person to be specific about.
        if tier.turns is not None:
            approx = tier.turns
        else:
            exact = (tier.credits or 0) * CREDIT_UUSD / EDIT_UUSD
            approx = max(5, int(round(exact / 5) * 5))
        tiers.append(
            UsageTierOut(
                name=name,
                credits=tier.credits,
                turns=tier.turns,
                turns_per_hour=tier.turns_per_hour,
                approx_edits=approx,
                # Already true before any of this existed: POST
                # /v1/resumes/{id}/publish and PUT /v1/resumes/{id}/share both
                # take require_user.
                can_publish=name != GUEST,
            )
        )
    return UsageTiersOut(
        tiers=tiers, reset_at=window_end().isoformat(), enforced=limits_enabled()
    )
