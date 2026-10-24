"""The one seam every billable path goes through.

Two steps, deliberately separate:

    guard = preflight(db, user=user, guest=guest, purpose="chat")  # may raise 429
    async with metered(guard) as meter:
        ...
        meter.record(usage_event)

`preflight` decides, `metered` records. They are two calls rather than one
because the chat path cannot have them in one place: enforcement has to happen
before the StreamingResponse is constructed, so that a refusal is ordinary JSON
rather than an error frame on a stream that then has to end, while recording
happens per round inside the generator. Everywhere else the two lines sit
together and read as one thing.

Holding a `Guard` is evidence the check passed -- it is the only thing `metered`
accepts, and the only ways to get one are `preflight` (which enforces) or
`system_guard` (which does not, and demands a written reason).

**What this shape gives up.** A new caller can skip the wrap entirely, exactly
as app/jobs/refresh_profiles.py did before this module existed, and the failure
is silent: free turns, discovered from the invoice. A required meter argument
threaded down to the provider call would have made that impossible, and was
rejected on the author's call for locality and churn -- see the spec's rejected
list. Two things replace the guarantee, and neither is optional:

  1. tests/test_metering.py::test_billable_call_sites_are_wrapped -- an
     inventory of every call site of every billable function. It fails when one
     appears. Editing it to pass without wrapping the call is the one shortcut
     that defeats this whole design.
  2. Reconciliation against LlmCall (app.services.usage.reconcile). A path that
     recorded telemetry but no counter shows up as a gap, which is how a missed
     wrap is caught in production when a grep could not see it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session_factory
from app.models import GuestSession, UsageCounter, User
from app.services import usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Guard:
    """Permission to spend, plus who to bill.

    `subjects` is a list because a guest is billed twice: once against their
    cookie session and once against the coarse network key that stops clearing
    the cookie from minting a fresh allowance. Both counters move by the same
    amount; `balance` takes the minimum of what each has left.
    """

    subjects: tuple[tuple[str, str], ...]  # (subject_kind, subject_id)
    purpose: str
    window_start: datetime
    # Why enforcement was skipped, for a system guard. None on a real one.
    exempt_reason: str | None = None

    @property
    def enforced(self) -> bool:
        return self.exempt_reason is None


def _subjects_for(
    user: User | None, guest: GuestSession | None, *, network_key: str | None = None
) -> tuple[tuple[str, str], ...]:
    if user is not None:
        return (("user", user.id),)
    subjects: list[tuple[str, str]] = []
    if guest is not None:
        subjects.append(("guest", guest.id))
    if network_key:
        subjects.append(("network", network_key))
    return tuple(subjects)


def preflight(
    db: Session,
    *,
    user: User | None,
    guest: GuestSession | None,
    purpose: str,
    network_key: str | None = None,
    now: datetime | None = None,
) -> Guard:
    """Decide whether one unit of billable work may start.

    Raises the structured 429 from app.services.usage. Not wrapped in
    try/except: losing a metric is acceptable, silently losing the limit is
    not.
    """
    usage.enforce_usage(db, user, guest, purpose=purpose, now=now)
    return Guard(
        subjects=_subjects_for(user, guest, network_key=network_key),
        purpose=purpose,
        window_start=usage.window_start(now),
    )


def system_guard(
    *,
    user: User | None = None,
    guest: GuestSession | None = None,
    purpose: str,
    reason: str,
    now: datetime | None = None,
) -> Guard:
    """Record without enforcing. For work no HTTP caller is waiting on.

    The nightly profile refresh needs this: it spends real money on a user's
    behalf and must not 429 itself. `reason` is required and free text so that
    an exemption is a named thing a reviewer sees, rather than an omission
    nobody notices.

    Deliberately NOT an `enforce=False` flag on `preflight`. A boolean that
    turns the limit off is indistinguishable at a glance from forgetting to
    call it at all, which is the exact failure this module exists to make
    visible.
    """
    if not (reason or "").strip():
        raise ValueError("system_guard needs a written reason")
    return Guard(
        subjects=_subjects_for(user, guest),
        purpose=purpose,
        window_start=usage.window_start(now),
        exempt_reason=reason.strip(),
    )


def _upsert(session: Session, *, kind: str, subject_id: str, window_start: datetime,
            uusd: int, turns: int, unmeasured: int) -> None:
    """One atomic increment.

    ON CONFLICT DO UPDATE rather than read-modify-write: enforcement used to
    read then decide with no lock, so two concurrent turns could each see the
    last credit. The dialect-specific `insert` is the only way to express this
    in SQLAlchemy; both backends in use support it.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert

    statement = insert(UsageCounter).values(
        subject_kind=kind,
        subject_id=subject_id,
        window_start=window_start,
        uusd=uusd,
        turns=turns,
        unmeasured=unmeasured,
        updated_at=datetime.now(timezone.utc),
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["subject_kind", "subject_id", "window_start"],
            set_={
                "uusd": UsageCounter.uusd + uusd,
                "turns": UsageCounter.turns + turns,
                "unmeasured": UsageCounter.unmeasured + unmeasured,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    )


class Meter:
    """Records what one guarded piece of work billed.

    Constructed only by `metered`. Each `record` commits on its **own**
    short-lived session, not the request's: a failed increment must not roll
    back the chat messages and draft source the router has already written on
    the request session -- the insert this replaces already had to rollback
    carefully for exactly that reason. Committing per round is also more
    durable for metering than batching at the end, because a turn that dies
    halfway has still recorded the rounds it paid for.

    A failed increment logs at ERROR and lets the work continue. The tokens are
    already spent by then, so failing the response would lose the person's work
    *and* the money. That round goes unbilled, and the LlmCall reconciliation
    is what surfaces it.
    """

    def __init__(self, guard: Guard):
        self._guard = guard
        self._factory = get_session_factory()
        self.recorded_uusd = 0
        self.recorded_rounds = 0

    def record(self, event: dict) -> None:
        try:
            cost, measured = usage.event_cost_uusd(event)
            # purpose='chat' at round_index=0 is exactly one row per chat turn,
            # which is what caps a guest -- so the count has to be as durable
            # as the cost.
            is_turn_start = (
                (event.get("purpose") or self._guard.purpose) == "chat"
                and int(event.get("round_index") or 0) == 0
            )
            session = self._factory()
            try:
                for kind, subject_id in self._guard.subjects:
                    _upsert(
                        session,
                        kind=kind,
                        subject_id=subject_id,
                        window_start=self._guard.window_start,
                        uusd=cost,
                        turns=1 if is_turn_start else 0,
                        unmeasured=0 if measured else 1,
                    )
                session.commit()
            finally:
                session.close()
            self.recorded_uusd += cost
            self.recorded_rounds += 1
        except Exception:
            # Money, so ERROR rather than WARNING. Unbilled from here; the
            # reconciliation against LlmCall is what makes it visible.
            logger.error("usage counter increment failed", exc_info=True)


@asynccontextmanager
async def metered(guard: Guard) -> AsyncIterator[Meter]:
    """Wrap one billable piece of work.

    Takes a Guard and nothing else, so that reaching this point without having
    gone through `preflight` or `system_guard` is not expressible.

    There is no teardown: every `record` has already committed by the time this
    exits. The context manager exists to mark the billable region, which is
    what the inventory test looks for.
    """
    meter = Meter(guard)
    if guard.exempt_reason:
        logger.info(
            "metered work exempt from enforcement: purpose=%s reason=%s",
            guard.purpose,
            guard.exempt_reason,
        )
    yield meter


def counter_totals(
    db: Session, *, kind: str, subject_id: str, window_start: datetime
) -> tuple[int, int, int]:
    """One subject's counters for one window. (uusd, turns, unmeasured)."""
    row = db.execute(
        select(UsageCounter.uusd, UsageCounter.turns, UsageCounter.unmeasured).where(
            UsageCounter.subject_kind == kind,
            UsageCounter.subject_id == subject_id,
            UsageCounter.window_start == window_start,
        )
    ).first()
    return (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)


def window_totals(
    db: Session, *, window_start: datetime, kinds: tuple[str, ...] | None = None
) -> int:
    """Summed spend across subjects for one window, for the ceilings.

    One row per active subject per week, so this is a small sum rather than the
    instance-wide scan over every round that it replaces.
    """
    query = select(func.coalesce(func.sum(UsageCounter.uusd), 0)).where(
        UsageCounter.window_start == window_start
    )
    if kinds:
        query = query.where(UsageCounter.subject_kind.in_(kinds))
    return int(db.execute(query).scalar() or 0)
