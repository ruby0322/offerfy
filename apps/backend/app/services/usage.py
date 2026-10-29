"""Weekly AI-usage tiers, metered on what the model calls actually billed.

Three tiers. A guest is capped on a *count* of chat turns; a signed-in person
and an opted-in one are capped on *cost*. The mixed unit is deliberate -- see
docs/superpowers/specs/2026-10-24-usage-policy-design.md -- and comes down to
this: at four turns the worst case is about a dollar, so cost precision buys a
guest nothing, while "4 AI edits to try it out" needs no explanation to someone
with no account and cannot be eaten by one pathologically expensive first turn.

There is no ledger table here. `LlmCall` already holds one row per model round
on every billable path, written by the chat router and the profile builder, and
metering off it is already the house pattern -- see
routers/talent_me.REFRESH_LIMIT_PER_HOUR, whose comment gives the reason: the
row is written anyway.

Money is integer micro-USD throughout. Floats belong to the display layer only;
a quota compared with a float drifts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session_factory
from app.services import model_catalog
from app.models import (
    GuestSession,
    LlmCall,
    UsageCounter,
    UsageRefusal,
    User,
)
from app.services.consent import has_consent
from app.services.grants import granted_credits

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------

# Prices live in app.services.model_catalog, with every other fact about a
# model: its provider, its product label and whether it may be picked. They
# were a dict here before model selection existed, and splitting them from the
# catalogue would mean a model could be offered in the picker without being
# priceable by the meter -- which is a free turn, discovered from the invoice.
#
# `PRICES` stays as the name the rest of this module reads, now derived. A
# model the catalogue does not list is priced as the most expensive one it
# does: never free, because OPENAI_MODEL and CHAT_MODEL are config strings an
# operator can change, and an unknown name must not open an unmetered tier.
PRICES: dict[str, dict[str, int]] = {
    spec.id: spec.prices.as_dict() for spec in model_catalog.MODELS
}

_FALLBACK_MODEL = max(PRICES, key=lambda name: PRICES[name]["input"])

# What one round costs when the provider reported no usage at all. Measured
# mean, 2026-10-24. Deliberately not zero: OPENAI_BASE_URL may be a proxy that
# drops the usage block, and charging nothing for an unmeasurable round would
# make the whole product free to anyone who could arrange one. LlmCall's own
# docstring already says knowing the spend is unmeasurable is the measurement.
FALLBACK_ROUND_UUSD = 25_000

# The unit the allowances are denominated in. Deliberately NOT re-pegged when
# the measured cost of an edit falls: a credit is what someone was promised, and
# cutting it to track a cost reduction would quietly take the saving back off
# them instead of handing it over. Changing this changes what people get, so it
# changes only on purpose.
CREDIT_UUSD = 70_000

# What one turn actually bills. Measured off usage_counters on 2026-10-25 --
# the meter's own arithmetic over 13 real turns, not an estimate: $0.3252 for 13
# turns. Per round the mix was 2,352 fresh input, 9,375 cached, 2,349 cache
# write and 141 output = $0.0141, at 1.77 rounds a turn.
#
# It fell from ~$0.07 because 4db08d1 bounded the chat history: input per round
# went 26,038 -> 11,727 tokens. Cache writes are now the largest single line
# item at 41% of a round, which is why migration 017 had to record them.
#
# This is what converts an allowance into "about N edits". It is a *measurement*
# and moves when the product does; CREDIT_UUSD above is a *promise* and does
# not. Keeping them apart is the whole point -- collapsing them would mean every
# efficiency gain silently shrank the allowance.
#
# 36_000 rather than the measured 25_000 on purpose: 1.77 rounds a turn is a
# small, light sample, and the earlier pre-trim mean was 2.57. At 2.57 rounds
# the same per-round mix gives $0.0364. Quoting the heavier figure makes the
# published "about N" hold up when turns get more complex, and under-promising
# an allowance costs nothing.
EDIT_UUSD = 36_000

# The token mix EDIT_UUSD was measured on, per round, and the rounds a turn
# takes. Written down so that what one edit costs on a *different* model can be
# derived from that model's real rates rather than guessed at: see
# `edit_uusd`. These are the numbers in the comment above, in a form the
# arithmetic can read.
#
# They describe one turn on the reference model. They are not a budget and
# nothing is enforced against them -- the meter always bills the tokens the
# provider actually reported. Their only job is the "about N edits" figure.
EDIT_MIX = {"fresh": 2_352, "cached": 9_375, "cache_write": 2_349, "output": 141}
EDIT_ROUNDS = 2.57

# The model EDIT_UUSD was measured on, which is **not** the model the figures
# are quoted against. It has to be written down separately, and conflating the
# two was a real bug: the measurement was taken on gpt-5.6-terra, the default
# then became gpt-6.1-terra, and gpt-6.1-terra is half the price -- so every
# published "about N edits" was understating what an allowance buys by about
# half, in the safe direction but by a lot.
#
# Keep this pointing at whichever model the 36,000 was actually measured on.
# Moving the default does not move it; only taking a fresh measurement does.
EDIT_MEASURED_ON = "gpt-5.6-terra"


def edit_uusd(model: str | None = None) -> int:
    """What one AI edit costs on `model`, in micro-USD.

    EDIT_UUSD is a measurement taken on EDIT_MEASURED_ON. Every model's figure
    is that measurement scaled by the ratio of the two models' prices over the
    measured token mix -- so a model that is 5x the price reads as 5x the cost
    and a fifth of the edits, from the same rates the meter bills with.

    Scaling rather than pricing the mix outright keeps EDIT_UUSD's deliberate
    conservatism (see its comment: it quotes the heavier 2.57-round sample)
    applied evenly across the table instead of only to one row.

    An unknown model lands on the fallback price, which is the dearest row, so
    it is quoted as the fewest edits rather than the most. `None` means the
    reference model, which is what the tier table quotes.
    """
    name = (model or "").strip() or model_catalog.REFERENCE_MODEL
    measured_on = price_uusd(EDIT_MEASURED_ON, **EDIT_MIX)
    if measured_on <= 0:  # pragma: no cover -- a zero-priced measurement row
        return EDIT_UUSD
    mine = price_uusd(name, **EDIT_MIX)
    return max(1, round(EDIT_UUSD * mine / measured_on))


def cost_weight(model: str | None) -> float:
    """What one edit on `model` costs relative to the reference model.

    The number the public table shows as "1x" / "5x". Derived from the same
    arithmetic as `edit_uusd` so the two columns of that table cannot
    disagree, and rounded to two places because this is a comparison, not a
    price.
    """
    reference = edit_uusd(model_catalog.REFERENCE_MODEL)
    if reference <= 0:  # pragma: no cover
        return 1.0
    return round(edit_uusd(model) / reference, 2)


# --------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------

GUEST = "guest"
REGISTERED = "registered"
MATCHING = "matching"

MATCHING_PURPOSE = "talent_introduction"


@dataclass(frozen=True)
class Tier:
    name: str
    # Weekly cost ceiling, in credits. None means this tier is not cost-capped.
    credits: int | None
    # Weekly ceiling on chat turns. None means this tier is not count-capped.
    turns: int | None
    # There is deliberately no hourly cap on chat. Removed 2026-10-25: the
    # weekly budget is the real guard, and an hourly cap on top of it only
    # decided *when* someone could spend an allowance that was already theirs.
    # For a guest it had become a no-op anyway once turns_per_hour was raised to
    # match turns, and for an account it mostly punished a long focused session
    # -- which is the session worth having. The instance is still bounded by the
    # global and guest-pool ceilings, which is where a cost spike actually has
    # to be caught.
    #
    # The profile cap below is NOT that limit: it predates this work as
    # routers/talent_me.REFRESH_LIMIT_PER_HOUR, guards a different endpoint, and
    # was only moved here. Only a matching-tier person can have a talent
    # profile at all:
    # services.profile.build_profile refuses without a live
    # talent_introduction grant, and withdrawing deletes the profile. So the
    # guest and registered values are unreachable by construction, and 0 says
    # that rather than pretending to a limit nobody can hit.
    profile_builds_per_hour: int


TIERS: dict[str, Tier] = {
    # 8 turns, doubled from 4 on 2026-10-25. A first polish measures 8-15 turns
    # and a tailoring pass 3-6, so this is now most of a simple polish rather
    # than a third of one -- the wall still lands before a long or fussy resume
    # is done, but a short one may just make it. The stricter 4 was sized when
    # a turn was thought to cost $0.07; at the measured $0.025 eight turns bill
    # about $0.13 a week, so the per-session cost of being generous here is
    # small. What bounds cookie-reset farming is the guest pool ceiling, not
    # this number.
    #
    # The weekly count is the only thing guarding a guest now, which is what it
    # had effectively become anyway.
    GUEST: Tier(
        GUEST, credits=None, turns=8, profile_builds_per_hour=0
    ),
    # Has to cover the whole advertised job on its own -- finish polishing one
    # page, or tailor for one role. That is a legal constraint as much as a
    # product one: the matching bonus below is only lawfully a thank-you if the
    # service is genuinely usable without it. See the spec.
    REGISTERED: Tier(
        REGISTERED, credits=20, turns=None, profile_builds_per_hour=0,
    ),
    # 5/hour is what routers/talent_me.py enforced before this moved here.
    MATCHING: Tier(
        MATCHING, credits=100, turns=None, profile_builds_per_hour=5,
    ),
}

NEXT_TIER = {GUEST: REGISTERED, REGISTERED: MATCHING}

# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------

TAIPEI = ZoneInfo("Asia/Taipei")
BURST_WINDOW = timedelta(hours=1)


def window_start(now: datetime | None = None) -> datetime:
    """The most recent Monday 00:00 in Taipei, as UTC.

    Fixed rather than rolling, because a meter has to be readable: "resets
    Monday" is a sentence, and a fixed window is the only one where the bar
    moves in one direction between resets. Taipei because that is where the
    users are; a reset at 08:00 local is strictly worse than one at midnight.

    Derived from the clock on every read -- nothing is stored and no job runs
    at the boundary. zoneinfo rather than a fixed +08:00: Taipei has no DST
    today, but writing the offset down is a lie waiting to become true.
    """
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(TAIPEI)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - timedelta(days=local.weekday())).astimezone(timezone.utc)


def window_end(now: datetime | None = None) -> datetime:
    return window_start(now) + timedelta(days=7)


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


def _prices_for(model: str | None) -> dict[str, int]:
    return PRICES.get((model or "").strip()) or PRICES[_FALLBACK_MODEL]


def price_uusd(
    model: str | None, *, fresh: int, cached: int, output: int, cache_write: int
) -> int:
    """The one place token counts become money.

    Both the per-row reader and the windowed aggregate go through here, so
    enforcement, the usage endpoint and the admin dashboard cannot disagree
    about what a round cost.
    """
    rates = _prices_for(model)
    total = (
        max(fresh, 0) * rates["input"]
        + max(cached, 0) * rates["cached"]
        + max(output, 0) * rates["output"]
        + max(cache_write, 0) * rates["cache_write"]
    )
    return total // 1_000_000


def _cost_from_counts(
    model: str | None,
    input_tokens: int | None,
    cached_tokens: int | None,
    output_tokens: int | None,
    cache_write_tokens: int | None,
) -> tuple[int, bool]:
    """The shared core. One definition of what a round cost.

    Reached from a persisted row (`round_cost_uusd`, for admin and
    reconciliation) and from a live usage event (`event_cost_uusd`, for the
    meter). They must not drift, so neither does the arithmetic.
    """
    if input_tokens is None:
        return FALLBACK_ROUND_UUSD, False
    inp = max(int(input_tokens), 0)
    cached = min(max(int(cached_tokens or 0), 0), inp)
    return (
        price_uusd(
            model,
            fresh=inp - cached,
            cached=cached,
            output=int(output_tokens or 0),
            cache_write=int(cache_write_tokens or 0),
        ),
        True,
    )


def event_cost_uusd(event: dict) -> tuple[int, bool]:
    """Price a live usage event, before it is a row. Used by the meter."""
    return _cost_from_counts(
        event.get("model"),
        event.get("input_tokens"),
        event.get("cached_tokens"),
        event.get("output_tokens"),
        event.get("cache_write_tokens"),
    )


def round_cost_uusd(row: LlmCall) -> tuple[int, bool]:
    """What one `LlmCall` row billed, and whether it was measured at all.

    Two subsets to get right, and each is a double-charge if it is not:

      - `cached_tokens` is a slice of `input_tokens`, nested under
        `input_tokens_details`. Only the remainder bills at the input rate.
      - `reasoning_tokens` is a slice of `output_tokens`. It is therefore
        deliberately unread here -- adding it would charge the thinking twice.

    `cache_write_tokens` is the exception that proves the rule: it is not a
    slice of anything, so it is added.
    """
    return _cost_from_counts(
        row.model,
        row.input_tokens,
        row.cached_tokens,
        row.output_tokens,
        row.cache_write_tokens,
    )


def llm_call_cost(db: Session, *, since: datetime, **filters) -> tuple[int, int]:
    """Summed cost and unmeasured-round count over `LlmCall` rows.

    No longer what the quota reads -- `usage_counters` is. This is kept for
    **reconciliation**: comparing it against the counters is how a path that
    recorded telemetry but never went through the meter becomes visible, which
    is the failure a context-manager seam cannot rule out by construction.

    Summed **per row** through `round_cost_uusd`, deliberately, even though a
    GROUP BY would be one query instead of a scan. `price_uusd` truncates
    integer micro-USD at the end, so pricing a per-model aggregate truncates
    once where the meter truncated once per round -- a difference of up to one
    µUSD per round. That is invisible in a cost report and fatal in a
    reconciliation: it made every correctly metered subject show up as a gap,
    which was caught by running the admin page against seeded data rather than
    by any test. The counters and this must be computed the same way or the
    comparison means nothing.
    """
    query = db.query(LlmCall).filter(LlmCall.created_at >= since)
    query = _apply_subject(query, **filters)
    if query is None:
        return 0, 0
    total = 0
    unmeasured = 0
    for row in query:
        cost, measured = round_cost_uusd(row)
        total += cost
        if not measured:
            unmeasured += 1
    return total, unmeasured


def _apply_subject(
    query,
    *,
    user: User | None = None,
    guest: GuestSession | None = None,
    guests_only: bool = False,
    everyone: bool = False,
    subject: tuple[str, str] | None = None,
):
    """Narrow a query to one subject, to all guests, or to nobody.

    Returns None when there is no subject to narrow to, which the callers read
    as "no spend" rather than as "all spend" -- an unfiltered quota query would
    bill one person for the whole instance.
    """
    if everyone:
        return query
    if guests_only:
        return query.filter(LlmCall.guest_session_id.isnot(None))
    if subject is not None:
        kind, subject_id = subject
        column = LlmCall.user_id if kind == "user" else LlmCall.guest_session_id
        return query.filter(column == subject_id)
    if user is not None:
        return query.filter(LlmCall.user_id == user.id)
    if guest is not None:
        return query.filter(LlmCall.guest_session_id == guest.id)
    return None


def _row_count(
    db: Session, *, since: datetime, purpose: str = "chat", **filters
) -> int:
    """Rows of one purpose started since `since`. **Hourly limits only.**

    The weekly figures live in usage_counters, which a durable budget needs. A
    per-week counter cannot answer "how many in the last hour" though, and an
    hourly limit is an abuse guard rather than an allowance -- so it keeps
    counting rows, where best-effort is the right durability and one indexed
    COUNT is the right cost. Do not try to unify the two.

    Only the profile-build limit uses this now; chat has no hourly cap.

    `purpose='chat' AND round_index=0` is exactly one row per turn: `purpose`
    starts at 'chat' in the router and the compile-repair loop flips it to
    'repair' before restarting its own round count, while vision rows carry
    'vision'. So a turn count needs no column of its own.
    """
    query = db.query(func.count(LlmCall.id)).filter(
        LlmCall.created_at >= since,
        LlmCall.purpose == purpose,
        LlmCall.round_index == 0,
    )
    query = _apply_subject(query, **filters)
    return int(query.scalar() or 0) if query is not None else 0


# --------------------------------------------------------------------------
# Tier and balance
# --------------------------------------------------------------------------


def counter_totals(
    db: Session, *, kind: str, subject_id: str, window_start: datetime
) -> tuple[int, int, int]:
    """One subject's counters for one window: (uusd, turns, unmeasured).

    Written only by app.services.metering. A missing row is a subject that has
    spent nothing this window, which is zero rather than an error.
    """
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

    One row per active subject per week, so this is a small sum -- it replaces
    an instance-wide aggregate over every round that ran on every chat turn.
    """
    query = select(func.coalesce(func.sum(UsageCounter.uusd), 0)).where(
        UsageCounter.window_start == window_start
    )
    if kinds:
        query = query.where(UsageCounter.subject_kind.in_(kinds))
    return int(db.execute(query).scalar() or 0)


def reconcile(
    db: Session, *, window_start: datetime
) -> list[tuple[str, str, int, int]]:
    """Counters against priced `LlmCall` rows, per subject, for one window.

    Returns (subject_kind, subject_id, counter_uusd, llm_call_uusd) wherever
    the two disagree. A gap means either telemetry was lost -- `_persist_usage`
    is best-effort by design -- or something billed without going through the
    meter. The second is the failure the context-manager seam cannot rule out
    by construction, so it has to be observable instead. Surfaced on the admin
    overview.

    **A third cause, and the one that is not a bug: a price changed.** The
    counters hold what was billed at the time; this re-prices every row at
    today's rates, because `PRICES` is read live from the catalogue and
    `LlmCall` stores token counts rather than money. So editing a model's
    price retroactively moves the right-hand number and invents a gap for
    every window that already has rows on that model. Measured: correcting
    claude-opus-5-5 from the seeded $5/$25 to the published $4/$20 moved one
    round from 34,653 to 25,848 uUSD, an 8,805 uUSD phantom gap.

    This matters because a false positive here teaches people to ignore the
    one signal that catches real unmetered spend. Before correcting a price,
    expect gaps on the current window and say so; after a reset they clear on
    their own. The real fix is to store the priced cost on the row at write
    time so history is priced once -- a column and a migration, deliberately
    not done here because it changes what `LlmCall` is for.
    """
    gaps: list[tuple[str, str, int, int]] = []
    rows = db.execute(
        select(UsageCounter.subject_kind, UsageCounter.subject_id, UsageCounter.uusd)
        .where(UsageCounter.window_start == window_start)
    ).all()
    seen: set[tuple[str, str]] = set()
    for kind, subject_id, counted in rows:
        seen.add((kind, subject_id))
        if kind == "network":
            # A network subject is billed alongside its guest session, so its
            # counter has no LlmCall rows of its own to compare against.
            continue
        billed, _ = llm_call_cost(db, since=window_start, subject=(kind, subject_id))
        if billed != int(counted):
            gaps.append((kind, subject_id, int(counted), billed))
    return gaps


def tier_of(db: Session, user: User | None, guest: GuestSession | None) -> str:
    """Which tier this caller is in, right now.

    Read through app.services.consent rather than by querying ConsentEvent, so
    that "what is their current answer" keeps exactly one definition -- and so
    that the quota can never disagree with the ledger about a withdrawal.
    Nothing about the quota is written back to that ledger; tier is derived.
    """
    if user is None:
        return GUEST
    if has_consent(db, user=user, purpose=MATCHING_PURPOSE):
        return MATCHING
    return REGISTERED


@dataclass(frozen=True)
class Balance:
    tier: str
    # Both ends of the window, from one reading of the clock. Derived here
    # rather than by a caller calling window_start() again: the two calls
    # straddle a Monday boundary roughly once a week, and a meter reporting a
    # period that ends before it starts is the kind of bug nobody looks for.
    period_start: datetime
    reset_at: datetime
    # Cost side. None on a tier that is not cost-capped.
    limit_uusd: int | None
    used_uusd: int
    remaining_uusd: int | None
    # The split behind limit_uusd, so a reader can say "20 + 50" rather than an
    # unexplained 70. tier_credits is the published number from TIERS;
    # granted_credits is the sum of this person's active grants. Both are None
    # and 0 respectively on a tier that is not cost-capped.
    tier_credits: int | None
    granted_credits: int
    # Count side. None on a tier that is not count-capped.
    turn_limit: int | None
    turns_used: int
    turns_remaining: int | None
    # Honesty about the meter itself.
    unmeasured_rounds: int

    @property
    def limit_credits(self) -> float | None:
        if self.limit_uusd is None:
            return None
        return round(self.limit_uusd / CREDIT_UUSD, 1)

    @property
    def used_credits(self) -> float:
        return round(self.used_uusd / CREDIT_UUSD, 2)

    @property
    def remaining_credits(self) -> float | None:
        if self.remaining_uusd is None:
            return None
        return round(self.remaining_uusd / CREDIT_UUSD, 1)

    @property
    def exhausted(self) -> bool:
        """Nothing left to start a turn with.

        A turn that has started is always allowed to finish, so the cost side
        reads `<= 0` rather than "enough for the next turn": the overrun of the
        last turn is carried as debt into this window instead of being clawed
        back or hidden.
        """
        if self.turns_remaining is not None and self.turns_remaining <= 0:
            return True
        if self.remaining_uusd is not None and self.remaining_uusd <= 0:
            return True
        return False


def balance(
    db: Session,
    user: User | None,
    guest: GuestSession | None,
    *,
    now: datetime | None = None,
) -> Balance:
    tier_name = tier_of(db, user, guest)
    tier = TIERS[tier_name]
    start = window_start(now)

    # From usage_counters, not from aggregating LlmCall. LlmCall's writes are
    # best-effort by design, so a quota reading them would turn a lost
    # telemetry row into a free turn -- and deriving on read cost 9-11 queries
    # per pre-flight, one of them an instance-wide scan.
    if user is not None:
        kind, subject_id = "user", user.id
    elif guest is not None:
        kind, subject_id = "guest", guest.id
    else:
        # No subject at all: a first-time visitor asking what they get. Zero
        # spent rather than everyone's spend.
        kind, subject_id = "guest", ""
    used_uusd, turns_used, unmeasured = counter_totals(
        db, kind=kind, subject_id=subject_id, window_start=start
    )

    # A grant raises the ceiling; it never sets one. None stays None, because a
    # grant on a tier that is not cost-capped is arithmetically a no-op -- that
    # is the guest tier, and grants are user-only anyway, so the two rules agree
    # by construction rather than by coincidence. See models.QuotaGrant.
    bonus = granted_credits(db, user=user, now=now)
    limit_uusd = (
        (tier.credits + bonus) * CREDIT_UUSD if tier.credits is not None else None
    )
    return Balance(
        tier=tier_name,
        period_start=start,
        reset_at=start + timedelta(days=7),
        limit_uusd=limit_uusd,
        used_uusd=used_uusd,
        remaining_uusd=None if limit_uusd is None else limit_uusd - used_uusd,
        tier_credits=tier.credits,
        granted_credits=bonus,
        turn_limit=tier.turns,
        turns_used=turns_used,
        turns_remaining=None if tier.turns is None else tier.turns - turns_used,
        unmeasured_rounds=unmeasured,
    )


def approx_edits_left(
    db: Session,
    user: User | None,
    guest: GuestSession | None,
    bal: Balance,
    *,
    model: str | None = None,
    now: datetime | None = None,
) -> int | None:
    """How many more edits the remaining balance is worth, for this person.

    Divided by their *own* trailing mean turn cost rather than by the global
    constant, so the fairness is visible: someone writing short prompts against
    a short resume really does get more edits out of the same allowance. Falls
    back to the reference edit before they have enough history to average.

    `model` is the one they are about to spend it on, and it only matters
    before that history exists -- a fresh account looking at Opus has to be
    told it buys a handful of edits rather than the default model's fifty. Once
    there is a trailing mean it supersedes the guess, including the model: what
    they actually spent is a better predictor than any list price, and it
    already carries whichever models they spent it on.

    The trailing mean is therefore *not* re-scaled by a model switch. That is
    deliberate but imperfect: someone who spent a week on Luna and switches to
    Opus is quoted too many edits until the mean catches up. Rescaling would
    need per-model counters, which the weekly counter table does not keep, and
    the figure is labelled "about" in every place it is shown.
    """
    if bal.turns_remaining is not None:
        return max(bal.turns_remaining, 0)
    if bal.remaining_uusd is None:
        return None
    # The measured cost of a turn on the model in question, not CREDIT_UUSD:
    # before someone has history we have to guess what *their* turn will cost,
    # and that measurement is the guess. Using the credit here understated by
    # ~3x once the history trim landed.
    per_turn = edit_uusd(model)
    if bal.turns_used >= 3 and bal.used_uusd > 0:
        per_turn = max(bal.used_uusd // bal.turns_used, 1)
    return max(bal.remaining_uusd, 0) // per_turn


# --------------------------------------------------------------------------
# Enforcement
# --------------------------------------------------------------------------


def limits_enabled() -> bool:
    """Production by default, overridable so staging can exercise this.

    Mirrors rate_limit.guest_rate_limits_enabled() rather than replacing it:
    that one still guards PDF exports, which are CPU and not tokens and so do
    not belong in a cost meter.
    """
    settings = get_settings()
    override = settings.usage_limits_enabled
    if override is not None:
        return bool(override)
    return settings.app_env == "production"


def _ceiling_uusd(raw: int | None) -> int | None:
    return raw if raw and raw > 0 else None


def _record_refusal(
    bal: Balance, limit: str, purpose: str, subject: tuple[str, str] | None
) -> None:
    """Append one refusal. Must never be the reason a refusal fails to happen.

    On its own short-lived session, and not for the meter's reason -- for a
    sharper one. Raising an HTTPException makes get_db roll the request session
    back, so a row inserted on it would be discarded exactly when we most want
    it. Wrapped in try/except because this is telemetry: a lost row costs a
    data point, while a raised exception here would turn a refusal into a 500.
    """
    if subject is None:
        return
    try:
        session = get_session_factory()()
        try:
            session.add(
                UsageRefusal(
                    subject_kind=subject[0],
                    subject_id=subject[1],
                    tier=bal.tier,
                    limit_kind=limit,
                    purpose=purpose,
                    used_uusd=max(bal.used_uusd, 0),
                    turns_used=max(bal.turns_used, 0),
                )
            )
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.warning("refusal telemetry failed", exc_info=True)


def _refusal(
    limit: str,
    bal: Balance,
    *,
    detail: str,
    purpose: str = "chat",
    subject: tuple[str, str] | None = None,
    **extra,
) -> HTTPException:
    """A 429 that says which limit, when it clears, and what the next tier gives.

    Deliberately not a bare "Too many requests". For a guest this refusal *is*
    the signup moment and for a signed-in person it is the matching moment; a
    message with nothing in it wastes both. Raised before the SSE stream opens,
    so the client gets ordinary JSON rather than an error frame.
    """
    _record_refusal(bal, limit, purpose, subject)
    next_name = NEXT_TIER.get(bal.tier)
    next_tier = TIERS[next_name] if next_name else None
    payload: dict = {
        "error": "usage_limit",
        "limit": limit,
        "detail": detail,
        "tier": bal.tier,
        "reset_at": bal.reset_at.isoformat(),
        "used_credits": bal.used_credits,
        "limit_credits": bal.limit_credits,
        "turns_used": bal.turns_used,
        "turn_limit": bal.turn_limit,
        "next_tier": (
            None
            if next_tier is None
            else {
                "name": next_tier.name,
                "credits": next_tier.credits,
                "turns": next_tier.turns,
            }
        ),
        **extra,
    }
    return HTTPException(status_code=429, detail=payload)


# Per-purpose hourly limit and the noun a refusal uses for it. A purpose absent
# from here has no hourly limit at all, which is the case for 'chat'.
_BURST = {
    "profile": (lambda tier: tier.profile_builds_per_hour, "profile rebuilds"),
}


def enforce_usage(
    db: Session,
    user: User | None,
    guest: GuestSession | None,
    *,
    purpose: str = "chat",
    now: datetime | None = None,
) -> Balance:
    """Decide whether one unit of billable work may start, before it does.

    Nothing between rounds, ever: abandoning a turn halfway leaves a
    half-applied edit, and abandoning the compile-repair loop leaves a resume
    that does not compile. A turn that is allowed to start is allowed to
    finish, and whatever it overruns by is carried as debt into this window.

    Unlike the telemetry in the chat router this is not wrapped in try/except.
    Losing a metric is acceptable; silently losing the limit is not.
    """
    bal = balance(db, user, guest, now=now)
    if not limits_enabled():
        return bal

    tier = TIERS[bal.tier]
    start = window_start(now)
    # Who a refusal would be recorded against. Resolved once so every raise
    # below records the same subject.
    if user is not None:
        subject: tuple[str, str] | None = ("user", user.id)
    elif guest is not None:
        subject = ("guest", guest.id)
    else:
        subject = None
    refuse = {"purpose": purpose, "subject": subject}

    if bal.turns_remaining is not None and bal.turns_remaining <= 0:
        raise _refusal(
            "weekly_turns",
            bal,
            detail=f"Weekly limit of {bal.turn_limit} AI edits reached",
            **refuse,
        )
    if bal.remaining_uusd is not None and bal.remaining_uusd <= 0:
        raise _refusal(
            "weekly_credits",
            bal,
            detail=f"Weekly limit of {bal.limit_credits:g} credits reached",
            **refuse,
        )

    # Only for purposes that have one. Chat does not -- see the comment on
    # Tier -- so a chat turn goes straight from the weekly check to the
    # ceilings.
    hourly = _BURST.get(purpose)
    if hourly is not None:
        limit_of, noun = hourly
        burst_limit = limit_of(tier)
        burst_since = (now or datetime.now(timezone.utc)) - BURST_WINDOW
        burst = _row_count(
            db, since=burst_since, purpose=purpose, user=user, guest=guest
        )
        if burst >= burst_limit:
            raise _refusal(
                "burst",
                bal,
                detail=f"More than {burst_limit} {noun} in an hour",
                retry_after_seconds=int(BURST_WINDOW.total_seconds()),
                **refuse,
            )

    settings = get_settings()
    global_ceiling = _ceiling_uusd(settings.global_weekly_ceiling_uusd)
    if global_ceiling is not None:
        spent = window_totals(db, window_start=start)
        if spent >= global_ceiling:
            raise _refusal(
                "global_ceiling",
                bal,
                detail=(
                    "Offerfy has reached its AI budget for this week. "
                    "Editing, preview, export and ATS checks are unaffected."
                ),
                **refuse,
            )

    # Guests only, and the last check on purpose: it converts rather than
    # refusing. A cookie is free to clear, so the per-session allowance above
    # is a speed bump; this is the wall, and it degrades into the signup path
    # instead of into an error.
    guest_ceiling = _ceiling_uusd(settings.guest_weekly_ceiling_uusd)
    if bal.tier == GUEST and guest_ceiling is not None:
        spent = window_totals(db, window_start=start, kinds=("guest", "network"))
        if spent >= guest_ceiling:
            raise _refusal(
                "guest_pool",
                bal,
                detail="Sign in to keep editing with AI this week",
                sign_in_required=True,
                **refuse,
            )
    return bal


def quota_event(bal: Balance, edits_left: int | None) -> dict:
    """The `quota` SSE frame: the person's own allowance, and nothing else.

    Deliberately not the existing `usage` event, which the chat router persists
    and never relays because telemetry is not the client's business. That
    decision stands. This carries no token counts, no model name and no dollar
    figures -- only what someone is entitled to ask about.

    `approx_edits_left` is now model-dependent -- the same balance is worth
    five edits on the dearest model and fifty on the cheapest -- but the model
    is deliberately still not sent. The client set it and knows it; adding it
    here would put a provider's model id in a frame whose whole point is that
    it carries nothing of ours, and tests/test_usage.py holds that line.
    `GET /v1/usage` names the model, because that response is fetched on its
    own with no such history.
    """
    return {
        "type": "quota",
        "tier": bal.tier,
        "reset_at": bal.reset_at.isoformat(),
        "used_credits": bal.used_credits,
        "limit_credits": bal.limit_credits,
        "turns_used": bal.turns_used,
        "turn_limit": bal.turn_limit,
        "approx_edits_left": edits_left,
    }


def refusals_by(
    db: Session, *, since: datetime
) -> list[tuple[str, str, int]]:
    """(tier, limit_kind, count) since `since`. The number the tiers are tuned from.

    Read against the number of claims over the same days -- consent_events with
    source='claim' -- to see whether the guest wall converts. That comparison
    is deliberately an aggregate rather than a per-person funnel: /v1/auth/claim
    nulls resumes.guest_session_id and records consent against the user alone,
    so there is no stored link from a guest session to the account it became,
    and rebuilding one would mean adding personal-data linkage for an analytics
    question.
    """
    rows = db.execute(
        select(
            UsageRefusal.tier,
            UsageRefusal.limit_kind,
            func.count(UsageRefusal.id),
        )
        .where(UsageRefusal.created_at >= since)
        .group_by(UsageRefusal.tier, UsageRefusal.limit_kind)
        .order_by(func.count(UsageRefusal.id).desc())
    ).all()
    return [(tier, kind, int(count)) for tier, kind, count in rows]


REFUSAL_RETENTION = timedelta(days=90)


def prune_refusals(db: Session, *, now: datetime | None = None) -> int:
    """Delete refusals past the retention the privacy policy states.

    Not scheduled by anything yet -- it needs a systemd *user* timer, the way
    the catalog ingest has one (AGENTS.md section 8). Until that is installed
    the policy's 90 days is a promise with no mechanism, which is worth saying
    out loud rather than leaving implied.
    """
    cutoff = (now or datetime.now(timezone.utc)) - REFUSAL_RETENTION
    deleted = (
        db.query(UsageRefusal).filter(UsageRefusal.created_at < cutoff).delete()
    )
    db.commit()
    return int(deleted)
