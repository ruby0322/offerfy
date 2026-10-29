"""Every model this product can call, in one table.

This is the only place a model id, its provider, its price and its product
label are written down. Everything else derives:

  * `app.services.llm.providers` picks an adapter from `ModelSpec.provider`.
  * `app.services.usage` prices a round from `ModelSpec.prices`.
  * `GET /v1/models` publishes the selectable rows, and the /usage, /pricing
    and /plan pages render that.
  * The editor's picker offers exactly the rows marked `selectable`.

Adding a model is therefore one entry here plus nothing else, and a model
cannot exist for the picker but not for the meter -- which is the failure this
module is shaped to prevent. `tests/test_model_catalog.py` holds that line:
every selectable row needs a price, a label and a provider with an adapter.

**Prices are money and must be verified against the provider's own page before
a row ships.** `verified` records the date that was last done; a row whose
prices were seeded from a neighbouring generation says so, and
`tests/test_model_catalog.py::test_every_selectable_model_states_a_price_date`
makes the omission loud rather than silent. Under-pricing a model bills less
than it costs and is paid for out of the product; over-pricing bills someone
for tokens they did not spend. Neither is recoverable after the fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

OPENAI = "openai"
ANTHROPIC = "anthropic"

PROVIDERS = (OPENAI, ANTHROPIC)


# --------------------------------------------------------------------------
# One model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Prices:
    """micro-USD per 1M tokens.

    Four rates rather than the usual two, because the measured token mix of a
    resume edit is dominated by neither input nor output:

      * `cached` is a discount on a slice of input (both providers: ~0.1x).
      * `cache_write` is **not** a discount and not a slice of anything -- it
        is an extra charge on top of the read, at 1.25x the uncached input
        rate, and it measured 20-45% of a turn. Dropping it under-bills every
        turn this product serves. See usage.round_cost_uusd.
    """

    input: int
    cached: int
    cache_write: int
    output: int

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "cached": self.cached,
            "cache_write": self.cache_write,
            "output": self.output,
        }


def _rates(input_uusd: int, output_uusd: int, cached_uusd: int) -> Prices:
    """One rate derived, one spelled out, and the split is the lesson.

    The **cache write** multiplier is genuinely stable: 1.25x the input rate on
    every row of both providers' published tables (OpenAI's 30-minute TTL,
    Anthropic's 5-minute; the 12h/1h writes are 2.5x and 2x and are
    deliberately not modelled -- see usage._usage_counts for why guessing the
    longer TTL is the wrong direction to err in). Deriving it means a
    hand-typed figure cannot drift from the rate it is a multiple of.

    The **cache read** rate is not stable and must be passed in. It was 0.1x
    of input for years, which this helper used to assume -- and the assumption
    was wrong by a factor of two the moment it met a real price list: the
    Claude 5.5 generation reads cached tokens at 0.05x (Sonnet $2 -> $0.10,
    Opus $4 -> $0.20) while Haiku 5.5 is still 0.1x ($0.10 -> $0.01). Cached
    reads are ~80% of a turn's input here, so halving that rate is not a
    rounding error, and a helper that quietly papered over it would have
    over-billed two of the five models people can pick.
    """
    return Prices(
        input=input_uusd,
        cached=cached_uusd,
        cache_write=input_uusd * 125 // 100,
        output=output_uusd,
    )


@dataclass(frozen=True)
class ModelSpec:
    # The id sent to the provider, and the id stored on `llm_calls.model`.
    id: str
    provider: str
    # The product label, shown in the picker and on the public table. Not
    # localised: these are proper nouns.
    label: str
    prices: Prices
    # Offered in the picker and published on /v1/models. False keeps a model
    # priceable without offering it -- a retired default whose `llm_calls`
    # rows still have to cost something, or an internal-only model.
    selectable: bool = False
    # The cheapest tier allowed to pick this. 'guest' means everyone.
    #
    # This is the one rule that is about money rather than taste. A guest is
    # capped on a *count* of turns, not on cost (app.services.usage), so an
    # expensive model multiplies what a guest can spend by the ratio of its
    # price to the reference model's -- the cap would not notice. An account
    # is capped on cost, so there the choice is self-funding and every model
    # is open. See `allowed_for`.
    min_tier: str = "registered"
    # Alternative spellings accepted from config and from a client. The id
    # itself and its space-separated form are matched without being listed.
    aliases: tuple[str, ...] = ()
    # When these prices were last checked against the provider's own pricing
    # page, ISO date. None means seeded from a neighbouring generation and not
    # yet confirmed -- which `GET /v1/models` reports as `prices_provisional`
    # so nobody has to read this file to find out.
    verified: str | None = None
    # Provider-specific knobs for the adapter: reasoning effort, thinking
    # display, the server-side web-search tool version. Read only by the
    # adapter named in `provider`, so an OpenAI key here is meaningless to the
    # Anthropic one and vice versa.
    options: dict = field(default_factory=dict)

    @property
    def family(self) -> str:
        """The generation, for grouping the picker. 'gpt-6.1', 'claude-5.5'."""
        return FAMILY_OF.get(self.id, self.id)


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

# OpenAI reasoning settings for the resume-editing loop.
#
# **`summary` does work on GPT-6.1, but only on some turns.** It was measured
# on gpt-5.6, where `detailed` surfaced a visible summary 3/3 against `auto`'s
# 2/3 at the same ~430 chars. A measurement on 2026-10-29 then found
# gpt-6.1-terra returning `summary: []` with no
# `response.reasoning_summary_text.delta` at all, and concluded the generation
# never shows its work.
#
# **That conclusion was too strong and is corrected here.** It was taken on a
# one-sentence question, and it reproduces exactly in that condition -- but on
# a real resume-editing turn through `llm.turn`, terra streams ~500-600 chars
# of summary. Re-measured 2026-10-29 over 8 live turns each on two task
# shapes:
#
#     gpt-6.1-terra     3/8 turns carried a summary  (2/4 on a bullet rewrite)
#     claude-haiku-5-5  5/8                          (4/4 on a bullet rewrite)
#     gpt-6-luna        1/8                          (1/4)
#
# So whether the panel fills is a property of the *turn*, not of the model.
# Every selectable row came back silent on all three structural-edit passes,
# including both Claude rows; terra also billed reasoning tokens with no
# summary in 2 of 4 rewrite passes, which is the real residue of the original
# finding -- it sometimes thinks without narrating.
#
# The product consequence is therefore not "pick the model that narrates".
# There is no such model. It is that the editor must read honestly when a turn
# produces no summary, whichever model is running -- see
# `apps/frontend/components/editor/EditorChatPanel.tsx`.
#
# `summary` is left in rather than removed: it is accepted without error, it
# costs nothing to send, it demonstrably still fires on 6.1, and it is what an
# older model or a proxy serving one would need.
#
# Effort is `low` because nothing measured justified more, and it still does
# real work on 6.1 -- the reasoning tokens above are billed either way, so
# this is the lever that keeps them few. The tempting theory
# was that deliberation would pay for itself by avoiding the edit retries the
# turn loop already handles. It does not: on a real two-change edit request
# both `low` and `medium` spent the entire tool-round budget (6/6 each), so
# effort bought no efficiency, and in two runs apiece `medium` once gave up
# with "I wasn't able to complete the update safely" where `low` finished both
# times. Same visible thinking, fewer reasoning tokens. Revisit with a larger
# sample before raising it.
_OPENAI_REASONING = {"reasoning": {"effort": "low", "summary": "detailed"}}

# Anthropic's equivalent. `adaptive` is the only on-mode on this generation --
# `budget_tokens` is rejected with a 400 -- and `display: "summarized"` is the
# opt-in that returns readable thinking at all: the default is `omitted`,
# which streams thinking blocks with empty text and would show the user a long
# pause where the OpenAI models show their reasoning. `effort` is `low` for
# the same measured reason as above; it lives in `output_config`, not at the
# top level.
_ANTHROPIC_THINKING = {
    "thinking": {"type": "adaptive", "display": "summarized"},
    "output_config": {"effort": "low"},
}

MODELS: tuple[ModelSpec, ...] = (
    # --- OpenAI, the current generation -----------------------------------
    ModelSpec(
        id="gpt-6.1-terra",
        provider=OPENAI,
        label="GPT-6.1 Terra",
        # $1 / $0.05 cached / $1.25 write / $5 out per MTok, short-context
        # band. The long-context band is exactly 2x on every column; the
        # threshold is well above a bounded round here, for the same reason
        # and with the same caveat as claude-haiku-5-5 below.
        prices=_rates(1_000_000, 5_000_000, 50_000),
        selectable=True,
        min_tier="guest",
        aliases=("terra", "gpt-6.1", "gpt6.1-terra"),
        verified="2026-10-29",
        options=_OPENAI_REASONING,
    ),
    ModelSpec(
        id="gpt-6.1-sol",
        provider=OPENAI,
        label="GPT-6.1 Sol",
        # $2 / $0.10 cached / $2.50 write / $10 out per MTok, short-context.
        prices=_rates(2_000_000, 10_000_000, 100_000),
        selectable=True,
        aliases=("sol", "gpt6.1-sol"),
        verified="2026-10-29",
        options=_OPENAI_REASONING,
    ),
    # --- Anthropic --------------------------------------------------------
    ModelSpec(
        id="claude-sonnet-5-5",
        provider=ANTHROPIC,
        label="Claude Sonnet 5.5",
        # $2 / $0.10 cached / $2.50 write / $10 out per MTok.
        prices=_rates(2_000_000, 10_000_000, 100_000),
        selectable=True,
        aliases=("sonnet", "sonnet 5.5", "claude sonnet 5.5", "claude-sonnet-5.5"),
        verified="2026-10-29",
        options=_ANTHROPIC_THINKING,
    ),
    ModelSpec(
        id="claude-opus-5-5",
        provider=ANTHROPIC,
        label="Claude Opus 5.5",
        # $4 / $0.20 cached / $5 write / $20 out per MTok. Note this is
        # *cheaper* than Claude Opus 5 ($5/$25), which is what it was seeded
        # from -- a newer model in the same tier came down in price, so the
        # seeded figure over-billed it by 25%.
        prices=_rates(4_000_000, 20_000_000, 200_000),
        selectable=True,
        aliases=("opus", "opus 5.5", "claude opus 5.5", "claude-opus-5.5"),
        verified="2026-10-29",
        options=_ANTHROPIC_THINKING,
    ),
    ModelSpec(
        id="claude-haiku-5-5",
        provider=ANTHROPIC,
        label="Claude Haiku 5.5",
        # $0.10 / $0.01 cached / $0.125 write / $0.50 out per MTok, which is
        # the cheapest row in the catalogue -- below gpt-6.1-luna.
        #
        # **These are the rates for prompts up to 100,000 tokens.** Above that
        # the whole request jumps to a 5x band ($0.50 / $0.05 / $0.625 /
        # $2.50), and billing the cheap band for an expensive request would
        # under-charge by 5x. What keeps us in the cheap band is
        # app.services.chat_window, which bounds the history sent to the
        # provider: a measured round is ~12k tokens and the observed worst
        # case is well under 100k. That makes this price a *dependency* on the
        # window staying bounded -- if that bound is ever raised, this row has
        # to be re-checked or split in two.
        prices=_rates(100_000, 500_000, 10_000),
        selectable=True,
        min_tier="guest",
        aliases=("haiku", "haiku 5.5", "claude haiku 5.5", "claude-haiku-5.5"),
        verified="2026-10-29",
        options=_ANTHROPIC_THINKING,
    ),
    # --- Priceable but not offered ----------------------------------------
    #
    # Retired defaults. Not selectable, but `llm_calls` rows carrying these
    # ids are still read by the admin cost report and by the reconciliation,
    # and a row priced through the unknown-model fallback would be priced as
    # the most expensive model in the table rather than as what it actually
    # billed. Prices verified 2026-10-24 against the provider's pricing page;
    # terra's input and output both fell 20% on 2026-07-30, so any
    # $2.50/$15.00 figure found elsewhere is pre-cut.
    ModelSpec(
        id="gpt-5.6-terra",
        provider=OPENAI,
        label="GPT-5.6 Terra",
        prices=Prices(
            input=2_000_000,
            cached=200_000,
            cache_write=2_500_000,
            output=12_000_000,
        ),
        aliases=("gpt-5.6",),
        verified="2026-10-24",
        options=_OPENAI_REASONING,
    ),
    ModelSpec(
        id="gpt-5.6-sol",
        provider=OPENAI,
        label="GPT-5.6 Sol",
        prices=Prices(
            input=4_000_000,
            cached=400_000,
            cache_write=5_000_000,
            output=20_000_000,
        ),
        verified="2026-10-24",
        options=_OPENAI_REASONING,
    ),
    ModelSpec(
        id="gpt-5.6-luna",
        provider=OPENAI,
        label="GPT-5.6 Luna",
        prices=Prices(
            input=200_000,
            cached=20_000,
            cache_write=250_000,
            output=1_200_000,
        ),
        verified="2026-10-24",
        options=_OPENAI_REASONING,
    ),
    # Two jobs. It is the only model the Decisions endpoint takes, so
    # read_layout's vision questions run on it whatever the caller picked --
    # and it is also the cheap OpenAI model in the picker.
    #
    # It is in the picker because `gpt-6.1-luna` is not a real model: the 6.1
    # generation is Sol and Terra only, confirmed against the provider's own
    # model list on 2026-10-29. This is the cheapest OpenAI model that exists,
    # so it takes the place the imagined one was holding.
    ModelSpec(
        id="gpt-6-luna",
        provider=OPENAI,
        label="GPT-6 Luna",
        # $0.10 / $0.01 cached / $0.125 write / $0.50 out per MTok. Re-checked
        # against the pricing page on 2026-10-29, unchanged since 10-24.
        prices=_rates(100_000, 500_000, 10_000),
        selectable=True,
        min_tier="guest",
        aliases=("luna", "gpt-6 luna", "gpt6-luna"),
        verified="2026-10-29",
        options=_OPENAI_REASONING,
    ),
)

# Which generation each model belongs to, for grouping the picker. Written out
# rather than parsed off the id: 'claude-sonnet-5-5' and 'claude-opus-5-5' are
# one family whose name appears in neither id.
FAMILY_OF = {
    "gpt-6.1-terra": "gpt-6.1",
    "gpt-6.1-sol": "gpt-6.1",
    "claude-haiku-5-5": "claude-5.5",
    "claude-sonnet-5-5": "claude-5.5",
    "claude-opus-5-5": "claude-5.5",
    "gpt-5.6-terra": "gpt-5.6",
    "gpt-5.6-sol": "gpt-5.6",
    "gpt-5.6-luna": "gpt-5.6",
    "gpt-6-luna": "gpt-6",
}

BY_ID: dict[str, ModelSpec] = {spec.id: spec for spec in MODELS}

# The model a turn runs on when nobody has chosen one, and the model every
# other model's cost is quoted relative to. Both are deliberately the same
# row: "1x" on the public table has to mean "what the default costs", or the
# column is unreadable.
#
# **Chosen on measured edit quality and measured cost, 2026-10-29**, against
# `gpt-6.1-terra` (the previous default) and `gpt-6-luna`: 8 live turns each
# through `llm.turn.aiter_chat_edit` on the same resume, two task shapes --
# an open-ended bullet rewrite and a precise structural edit.
#
#   * **Precise edits are a tie.** All three produced byte-identical correct
#     source on all three structural passes. No errors and no failed edits
#     anywhere, on any row.
#   * **Open-ended rewriting is not.** Haiku led bullets with the outcome and
#     a real verb ("Accelerated", "Hardened", "Established"); terra hedged
#     into "Targeted faster payments API performance" in 3 of 4 passes, which
#     is a *weaker* bullet than the one it replaced and the opposite of what
#     was asked. Haiku over-claimed once in 4 ("in production"), caught it
#     itself, fixed it in a second edit and told the user -- which terra's
#     hedging avoids by never claiming anything.
#   * **Cost: haiku is ~5x cheaper on a measured turn** ($0.0012 vs $0.0062
#     mean, priced through usage.event_cost_uusd), and 9.3x cheaper on the
#     published per-edit figure, which assumes a heavier token mix. Either
#     number stretches the instance ceilings in `app.config` by most of an
#     order of magnitude, and those ceilings are the launch-day constraint.
#   * It is also the fastest (4.0s vs 10.2s mean on the structural task) and
#     the fewest rounds (3.0 vs 4.7); terra does that edit in two passes.
#
# **What did *not* decide it: the editor's thinking panel.** The premise this
# change was opened to settle -- that the GPT-6.1 rows never fill it -- does
# not survive a real editing turn. See `_OPENAI_REASONING`.
DEFAULT_MODEL = "claude-haiku-5-5"
REFERENCE_MODEL = DEFAULT_MODEL

# Picker order. Cheapest first within a family, families newest first, which
# is also the order the public cost table reads in. Not sorted by price: the
# families are a product story ("the OpenAI ones, the Anthropic ones") and
# interleaving them by cost would scramble it.
SELECTABLE_ORDER = (
    "gpt-6.1-terra",
    "gpt-6-luna",
    "gpt-6.1-sol",
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
)


def _alias_index() -> dict[str, str]:
    """Every accepted spelling, mapped to a model id.

    Built once from the catalogue rather than hand-maintained beside it, so a
    new row cannot arrive with its own aliases missing. Each id contributes
    itself, its space-separated form (what a product label looks like typed)
    and whatever it declares.
    """
    index: dict[str, str] = {}
    for spec in MODELS:
        keys = {spec.id, spec.id.replace("-", " "), spec.label.lower(), *spec.aliases}
        for key in keys:
            index.setdefault(_normal(key), spec.id)
    return index


def _normal(name: str) -> str:
    return re.sub(r"[\s_]+", " ", (name or "").strip().lower())


ALIASES = _alias_index()


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------


def resolve(name: str | None) -> ModelSpec | None:
    """A spelling to a catalogue row, or None for one we do not publish.

    None rather than a fallback on purpose: the callers want different things
    from an unknown name. A config value that is not in the catalogue is a
    deliberate override and is passed through to the provider verbatim (see
    `spec_or_passthrough`); a *client*-supplied name that is not in the
    catalogue is rejected, because otherwise the picker would be a free-text
    field anyone could point at an arbitrary model.
    """
    key = _normal(name or "")
    if not key:
        return None
    direct = ALIASES.get(key) or ALIASES.get(key.replace(" ", "-"))
    return BY_ID.get(direct) if direct else None


def spec_or_passthrough(name: str | None) -> ModelSpec:
    """A spec for any name, inventing one for an id we do not know.

    This is the config path. OPENAI_BASE_URL may point at a proxy serving a
    model under its own name, and that has always worked -- so an unknown
    `CHAT_MODEL` still runs, on the provider its id looks like, at the
    most expensive price in the catalogue.

    Priced as the dearest row rather than the default's price, and never as
    free: an unrecognised name must not be a way to open a cheaper or an
    unmetered tier, and these are config strings an operator can change.
    """
    spec = resolve(name)
    if spec is not None:
        return spec
    raw = (name or "").strip()
    if not raw:
        return BY_ID[DEFAULT_MODEL]
    dearest = max(MODELS, key=lambda item: item.prices.input)
    return ModelSpec(
        id=raw,
        provider=provider_of_id(raw),
        label=raw,
        prices=dearest.prices,
        selectable=False,
    )


def provider_of_id(model_id: str) -> str:
    """Guess a provider for an id outside the catalogue.

    Only reached for a proxy model id. An Anthropic id is recognisable
    ('claude-...'); everything else is assumed to speak the Responses API,
    which is what every proxy this product has ever been pointed at does.
    """
    return ANTHROPIC if _normal(model_id).startswith("claude") else OPENAI


def selectable() -> list[ModelSpec]:
    """The rows the picker offers, in published order.

    Order comes from SELECTABLE_ORDER, and anything marked selectable but
    missing from it is appended rather than dropped -- a forgotten entry in
    one tuple must not silently remove a model from the product.
    """
    ordered = [BY_ID[name] for name in SELECTABLE_ORDER if BY_ID[name].selectable]
    listed = {spec.id for spec in ordered}
    ordered.extend(
        spec for spec in MODELS if spec.selectable and spec.id not in listed
    )
    return ordered


# Tier names, cheapest first. A copy of the order in app.services.usage, kept
# here so this module does not import it -- usage imports *this* one for its
# prices, and the cycle would be real. `tests/test_model_catalog.py` asserts
# the two agree.
TIER_ORDER = ("guest", "registered", "matching")


def allowed_for(spec: ModelSpec, tier: str) -> bool:
    """Whether `tier` may pick `spec`. See ModelSpec.min_tier for the why."""
    try:
        return TIER_ORDER.index(tier) >= TIER_ORDER.index(spec.min_tier)
    except ValueError:
        # A tier we do not know is treated as the most restricted one, so a
        # renamed tier fails closed rather than opening every model.
        return spec.min_tier == TIER_ORDER[0]


def default_for(tier: str) -> str:
    """The model a turn runs on when `tier` has chosen nothing.

    The published default where that tier may have it, and the cheapest model
    it may have otherwise -- never a model it is not allowed to pick, which
    would make the default itself a refusal.
    """
    spec = BY_ID[DEFAULT_MODEL]
    if allowed_for(spec, tier):
        return spec.id
    affordable = [row for row in selectable() if allowed_for(row, tier)]
    if not affordable:
        return spec.id
    return min(affordable, key=lambda row: row.prices.input).id
