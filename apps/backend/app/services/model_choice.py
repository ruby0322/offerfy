"""Which model this caller gets, and which ones they are allowed to pick.

Three questions, kept apart on purpose:

  * `offered(tier)` -- what the picker shows. Catalogue rows that are
    selectable, whose provider has a credential, and which this tier may have.
  * `selected(db, ...)` -- what this caller's next turn will run on. Their
    stored preference if it is still offered, otherwise the tier default.
  * `choose(db, ...)` -- record a new preference, refusing anything not
    offered.

The stored preference is a column on `users` and on `guest_sessions`
(migration 022), not a cookie or a client-side setting. A guest is a real
subject here -- they have a row because the quota already needs one -- so the
choice survives a reload and a new tab for them too, and the server never has
to trust a model id a client remembered.

**A preference is validated on read, not only on write.** Someone who picked
Opus as a matching-tier member and then withdrew consent still has
`claude-opus-5-5` in their column; `selected` drops it and falls back rather
than running a model their tier may no longer have. The same read heals a
preference pointing at a model that was retired, or at a provider whose key
was removed from the environment.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GuestSession, User
from app.services import model_catalog
from app.services.llm import provider_configured
from app.services.model_catalog import ModelSpec

logger = logging.getLogger(__name__)


def offered(tier: str) -> list[ModelSpec]:
    """The models this tier may pick, in published order.

    Filtered on three things, and the credential check is the one that is easy
    to forget: a deployment with no `ANTHROPIC_API_KEY` must not offer the
    Anthropic rows, or picking one produces a 502 that looks like an outage
    rather than a configuration gap.
    """
    return [
        spec
        for spec in model_catalog.selectable()
        if model_catalog.allowed_for(spec, tier) and provider_configured(spec)
    ]


def default_model(tier: str) -> str:
    """What a caller with no preference runs on.

    An operator pin wins, because that is the point of a pin -- including
    when it names a model outside the catalogue, which is how a proxy serving
    its own ids is reached. A pin the *tier* may not have is ignored rather
    than enforced: `min_tier` exists to stop a guest spending an account's
    money, and a pin must not be a way around it.
    """
    pinned = get_settings().pinned_chat_model()
    if pinned:
        spec = model_catalog.resolve(pinned)
        if spec is None or model_catalog.allowed_for(spec, tier):
            return pinned
    choices = offered(tier)
    catalogue_default = model_catalog.default_for(tier)
    if any(spec.id == catalogue_default for spec in choices):
        return catalogue_default
    if choices:
        # The catalogue default's provider has no key. The cheapest model that
        # does is a better answer than a model that cannot run.
        return min(choices, key=lambda spec: spec.prices.input).id
    # Nothing is offerable -- no provider is configured at all. The id is
    # still returned so the caller has something to report; the turn itself
    # raises 503 from the adapter.
    return catalogue_default


def _stored(user: User | None, guest: GuestSession | None) -> str | None:
    if user is not None:
        return (user.chat_model or "").strip() or None
    if guest is not None:
        return (guest.chat_model or "").strip() or None
    return None


def selected(
    db: Session,
    user: User | None,
    guest: GuestSession | None,
    *,
    tier: str,
    requested: str | None = None,
) -> str:
    """The model this turn runs on.

    Precedence: what the request asked for, then what was stored, then the
    tier default. A `requested` model that is not offered raises 400 -- a
    client naming a model it may not have is a bug or an attempt, and
    silently substituting a different one would charge someone for a model
    they did not ask for and show them an answer attributed to the wrong one.

    A *stored* model that is no longer offered is silently replaced, because
    nobody asked for it on this request: see the module docstring.
    """
    if requested is not None and (requested or "").strip():
        return require_offered(requested, tier).id
    stored = _stored(user, guest)
    if stored:
        spec = model_catalog.resolve(stored)
        if spec is not None and any(
            row.id == spec.id for row in offered(tier)
        ):
            return spec.id
        logger.info("stored chat model no longer offered tier=%s", tier)
    return default_model(tier)


def require_offered(name: str, tier: str) -> ModelSpec:
    """A client-supplied model name, validated. Raises 400 if it is not ours.

    Deliberately strict where `model_catalog.spec_or_passthrough` is lenient.
    A config string is an operator's deliberate override and may name anything
    a proxy serves; a request field is untrusted input, and accepting an
    arbitrary id there would turn the picker into a free-text field pointed at
    any model the credential can reach -- at any price.
    """
    spec = model_catalog.resolve(name)
    if spec is None or not any(row.id == spec.id for row in offered(tier)):
        raise HTTPException(status_code=400, detail="Unknown model")
    return spec


def choose(
    db: Session,
    user: User | None,
    guest: GuestSession | None,
    *,
    tier: str,
    name: str,
) -> str:
    """Record a preference for whoever is calling. Returns the stored id.

    Committed here rather than left to the caller: both call sites (the picker
    endpoint and a chat turn that carried an explicit model) want the choice
    durable before the turn runs, and a chat turn's request session goes on to
    be rolled back by the telemetry writer on a bad round.
    """
    spec = require_offered(name, tier)
    if user is not None:
        user.chat_model = spec.id
    elif guest is not None:
        guest.chat_model = spec.id
    else:
        # No subject to remember against -- a first-time visitor who has not
        # been given a guest session. The model is still honoured for this
        # turn; it simply is not stored.
        return spec.id
    db.commit()
    return spec.id
