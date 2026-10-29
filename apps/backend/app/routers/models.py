"""The model catalogue, and this caller's pick of it.

One endpoint serves both the editor's picker and the public cost table on
/usage, /pricing and /plan. That is the same argument `/v1/usage/tiers` makes
for the allowances: a published figure that has drifted from the enforced one
is the worst bug those pages could have, and the editor offering a model the
public table prices differently is the same bug wearing a different hat.

`owner_context(..., ensure=False)` rather than `require_user`, like
/v1/usage: the people on the smallest allowance are exactly the ones who most
need to see what a model costs them, and a first-time visitor with no cookie
gets the guest tier's answer rather than a 401. `ensure=False` so that merely
asking does not mint a guest session.
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, owner_context
from app.models import User
from app.models import GuestSession
from app.schemas import ModelOut, ModelSelection, ModelTableOut
from app.services import model_catalog, model_choice
from app.services.llm import provider_configured
from app.services.usage import CREDIT_UUSD, TIERS, cost_weight, edit_uusd, tier_of

router = APIRouter()


def _approx_edits(model_id: str) -> dict[str, int]:
    """How many edits each cost-capped tier's allowance buys on this model.

    The guest tier is absent: it is capped on a count of turns, so its number
    is the same whichever model runs -- which is exactly why guests are limited
    to the cheaper models (ModelSpec.min_tier) and why this dict must not
    pretend otherwise by listing a figure for them.

    Rounded to the nearest 5 above 20, for the reason /v1/usage/tiers gives:
    "about 39" is not an "about", and the figure is a blend over people whose
    turns differ by nearly 2x. Below 20 it is left exact, because rounding
    "12" to "10" on an expensive model understates by a fifth and those are
    the numbers someone is actually weighing.
    """
    per_edit = edit_uusd(model_id)
    out: dict[str, int] = {}
    for name, tier in TIERS.items():
        if tier.credits is None:
            continue
        exact = tier.credits * CREDIT_UUSD / max(per_edit, 1)
        out[name] = int(round(exact)) if exact < 20 else max(5, int(round(exact / 5) * 5))
    return out


def _row(spec: model_catalog.ModelSpec, *, available: bool) -> ModelOut:
    return ModelOut(
        id=spec.id,
        label=spec.label,
        provider=spec.provider,
        family=spec.family,
        cost_weight=cost_weight(spec.id),
        approx_edits=_approx_edits(spec.id),
        min_tier=spec.min_tier,
        available=available,
        prices_provisional=spec.verified is None,
    )


def _table(
    db: Session, user: User | None, guest: GuestSession | None, tier: str
) -> ModelTableOut:
    """The whole table for one caller. Built in one place, read by both routes.

    Rows whose provider has no credential are omitted entirely rather than
    sent as unavailable: `min_tier` is a product rule a reader can act on by
    signing in, while a missing API key is an operator's business and showing
    a locked row for it would promise something no upgrade delivers.

    Rows this *tier* may not have are sent with `available: false`, so the
    picker can show them locked with a reason. Hiding them would leave someone
    unable to discover that an account unlocks the better models, which is the
    one thing this table is meant to tell a guest.
    """
    offerable = {spec.id for spec in model_choice.offered(tier)}
    return ModelTableOut(
        models=[
            _row(spec, available=spec.id in offerable)
            for spec in model_catalog.selectable()
            if provider_configured(spec)
        ],
        selected=model_choice.selected(db, user, guest, tier=tier),
        default=model_choice.default_model(tier),
        reference=model_catalog.REFERENCE_MODEL,
        tier=tier,
    )


@router.get("/v1/models", response_model=ModelTableOut)
def read_models(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Every selectable model, with what it costs and whether you may have it."""
    user, guest = owner_context(request, response, db, user, ensure=False)
    return _table(db, user, guest, tier_of(db, user, guest))


@router.put("/v1/models/selection", response_model=ModelTableOut)
def set_model(
    payload: ModelSelection,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Record a pick. 400 on a model this caller may not have.

    `ensure=True` here, unlike the read: choosing is the point at which a
    guest needs a row to remember the choice against, and minting the session
    on a deliberate action rather than on a page load is the rule
    `owner_context` exists to express.

    Returns the whole table rather than an acknowledgement, so the picker's
    view of what is selected always comes from the server that stored it.
    """
    user, guest = owner_context(request, response, db, user, ensure=True)
    tier = tier_of(db, user, guest)
    model_choice.choose(db, user, guest, tier=tier, name=payload.model)
    return _table(db, user, guest, tier)
