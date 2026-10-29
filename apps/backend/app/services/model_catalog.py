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


def _rates(input_uusd: int, output_uusd: int) -> Prices:
    """Both providers' cache rates are fixed multiples of the input rate.

    OpenAI: cached reads 0.1x, 30-minute cache writes 1.25x (12h is 2.5x, and
    is deliberately not modelled -- see usage._usage_counts). Anthropic: cache
    reads 0.1x, 5-minute cache writes 1.25x. The same two multipliers, so one
    helper covers both and a hand-typed cache rate cannot drift from the input
    rate it is a multiple of.
    """
    return Prices(
        input=input_uusd,
        cached=input_uusd // 10,
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
    # Shown under the label in the picker; a key into the message files
    # rather than prose, because this text is translated.
    blurb_key: str = ""
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

# OpenAI reasoning settings for the resume-editing loop. `summary: detailed`
# is what makes the thinking visible, which is the point: over three runs each
# against the live API, `auto` surfaced a summary in only 2/3 at low effort
# while `detailed` managed 3/3, at the same length (~430 chars) and no extra
# latency.
#
# Effort is `low` because nothing measured justified more. The tempting theory
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
        prices=_rates(1_750_000, 10_000_000),
        selectable=True,
        min_tier="guest",
        aliases=("terra", "gpt-6.1", "gpt6.1-terra"),
        blurb_key="terra",
        options=_OPENAI_REASONING,
    ),
    ModelSpec(
        id="gpt-6.1-sol",
        provider=OPENAI,
        label="GPT-6.1 Sol",
        prices=_rates(3_500_000, 17_500_000),
        selectable=True,
        aliases=("sol", "gpt6.1-sol"),
        blurb_key="sol",
        options=_OPENAI_REASONING,
    ),
    ModelSpec(
        id="gpt-6.1-luna",
        provider=OPENAI,
        label="GPT-6.1 Luna",
        prices=_rates(150_000, 900_000),
        selectable=True,
        min_tier="guest",
        aliases=("luna", "gpt6.1-luna"),
        blurb_key="luna",
        options=_OPENAI_REASONING,
    ),
    # --- Anthropic --------------------------------------------------------
    ModelSpec(
        id="claude-sonnet-5-5",
        provider=ANTHROPIC,
        label="Claude Sonnet 5.5",
        prices=_rates(2_000_000, 10_000_000),
        selectable=True,
        aliases=("sonnet", "sonnet 5.5", "claude sonnet 5.5", "claude-sonnet-5.5"),
        blurb_key="sonnet",
        options=_ANTHROPIC_THINKING,
    ),
    ModelSpec(
        id="claude-opus-5-5",
        provider=ANTHROPIC,
        label="Claude Opus 5.5",
        prices=_rates(5_000_000, 25_000_000),
        selectable=True,
        aliases=("opus", "opus 5.5", "claude opus 5.5", "claude-opus-5.5"),
        blurb_key="opus",
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
    # read_layout's vision questions only. The Decisions endpoint takes this
    # model and no other, so it is never selectable -- but its calls are
    # metered like any other, which is why it needs a price here.
    ModelSpec(
        id="gpt-6-luna",
        provider=OPENAI,
        label="GPT-6 Luna",
        prices=Prices(
            input=100_000,
            cached=10_000,
            cache_write=125_000,
            output=500_000,
        ),
        verified="2026-10-24",
    ),
)

# Which generation each model belongs to, for grouping the picker. Written out
# rather than parsed off the id: 'claude-sonnet-5-5' and 'claude-opus-5-5' are
# one family whose name appears in neither id.
FAMILY_OF = {
    "gpt-6.1-terra": "gpt-6.1",
    "gpt-6.1-sol": "gpt-6.1",
    "gpt-6.1-luna": "gpt-6.1",
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
DEFAULT_MODEL = "gpt-6.1-terra"
REFERENCE_MODEL = DEFAULT_MODEL

# Picker order. Cheapest first within a family, families newest first, which
# is also the order the public cost table reads in. Not sorted by price: the
# families are a product story ("the OpenAI ones, the Anthropic ones") and
# interleaving them by cost would scramble it.
SELECTABLE_ORDER = (
    "gpt-6.1-terra",
    "gpt-6.1-luna",
    "gpt-6.1-sol",
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
