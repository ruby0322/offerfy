"""Invariants of the model catalogue.

These are the cheap checks that stop an expensive mistake. The catalogue is the
only place a model's price, provider and label are written down, and every one
of the failures below is a way for a model to be *offered* without being
fully wired -- which, for the price half, means a free turn discovered from the
invoice.
"""

from __future__ import annotations

import pytest

from app.services import model_catalog, usage
from app.services.llm import providers


def test_every_selectable_model_is_priceable():
    """The one that matters. An unpriced model is a free turn."""
    for spec in model_catalog.selectable():
        assert spec.id in usage.PRICES, spec.id
        rates = usage.PRICES[spec.id]
        for key in ("input", "cached", "cache_write", "output"):
            assert rates[key] > 0, f"{spec.id}.{key}"


def test_every_model_has_an_adapter():
    for spec in model_catalog.MODELS:
        assert spec.provider in model_catalog.PROVIDERS, spec.id
        assert providers.for_provider(spec.provider) is not None, spec.id
        # Not just registered -- resolvable from the spec, which is the call
        # the turn loop actually makes.
        assert providers.for_spec(spec) is not None, spec.id


def test_cache_rates_are_the_published_multiples_of_the_input_rate():
    """0.1x to read, 1.25x to write, on both providers.

    Hand-typed cache rates are how a price table drifts from itself: the
    numbers look plausible at a glance and the error only shows up in a bill.
    """
    for spec in model_catalog.MODELS:
        rates = spec.prices
        assert rates.cached == rates.input // 10, spec.id
        assert rates.cache_write == rates.input * 125 // 100, spec.id


def test_output_costs_more_than_input():
    """A sanity check on a hand-entered table, not a law of nature.

    Every model either vendor publishes charges more for output than for
    input. A row that does not is far more likely to be two numbers swapped
    than a genuinely novel price, and swapping them under-bills every turn.
    """
    for spec in model_catalog.MODELS:
        assert spec.prices.output > spec.prices.input, spec.id


def test_every_selectable_model_states_a_price_date():
    """Prices seeded from a neighbouring generation must say so.

    Not a failure, a disclosure: `verified=None` is reported to the client as
    `prices_provisional` and rendered as a footnote. This test only insists
    the catalogue is explicit either way, so an unverified row cannot pass for
    a confirmed one.
    """
    for spec in model_catalog.MODELS:
        assert spec.verified is None or len(spec.verified) == 10, spec.id


def test_the_default_and_the_reference_model_are_selectable_by_everyone():
    """Both are load-bearing and neither may be a model a guest cannot have.

    The default has to run for the smallest tier or the default is itself a
    refusal; the reference has to be priced for everyone or "1x" on the public
    table means nothing to the reader most likely to be reading it.
    """
    for name in (model_catalog.DEFAULT_MODEL, model_catalog.REFERENCE_MODEL):
        spec = model_catalog.BY_ID[name]
        assert spec.selectable, name
        assert spec.min_tier == "guest", name


def test_the_reference_model_weighs_exactly_one():
    assert usage.cost_weight(model_catalog.REFERENCE_MODEL) == 1.0
    assert usage.edit_uusd(model_catalog.REFERENCE_MODEL) == usage.EDIT_UUSD
    # And no argument means the reference, so the pre-picker figure is
    # unchanged rather than silently re-based on the dearest model.
    assert usage.edit_uusd() == usage.EDIT_UUSD


def test_a_dearer_model_costs_more_per_edit_and_buys_fewer():
    """The whole claim the public cost table makes, checked once."""
    cheap = usage.edit_uusd("gpt-6.1-luna")
    reference = usage.edit_uusd(model_catalog.REFERENCE_MODEL)
    dear = usage.edit_uusd("claude-opus-5-5")
    assert cheap < reference < dear
    assert usage.cost_weight("gpt-6.1-luna") < 1.0 < usage.cost_weight("claude-opus-5-5")


def test_an_unknown_model_is_quoted_as_the_fewest_edits():
    """The passthrough path must not be a cheap tier in the published table
    any more than it is in the meter."""
    dearest = max(usage.PRICES, key=lambda name: usage.PRICES[name]["input"])
    assert usage.edit_uusd("not-a-model") == usage.edit_uusd(dearest)


def test_selectable_order_lists_every_offered_model():
    """A model left out of the order tuple is appended, never dropped."""
    ordered = [spec.id for spec in model_catalog.selectable()]
    declared = {spec.id for spec in model_catalog.MODELS if spec.selectable}
    assert set(ordered) == declared
    assert len(ordered) == len(declared), "a model is listed twice"


def test_the_tier_order_matches_the_usage_module():
    """`model_catalog` keeps its own copy to avoid an import cycle. They must
    agree, or `min_tier` compares against a ladder nobody else is using."""
    assert model_catalog.TIER_ORDER == (usage.GUEST, usage.REGISTERED, usage.MATCHING)


@pytest.mark.parametrize(
    "tier,expected",
    [
        ("guest", {"gpt-6.1-terra", "gpt-6.1-luna"}),
        ("registered", None),
        ("matching", None),
    ],
)
def test_a_guest_only_gets_the_cheaper_models(tier, expected):
    """Because a guest is capped on a *count* of turns, not on cost.

    An expensive model would multiply what a guest can spend by the ratio of
    its price to the default's, and the cap would not notice -- see
    ModelSpec.min_tier. An account is capped on cost, so there every model is
    self-funding and all of them are open.
    """
    allowed = {
        spec.id
        for spec in model_catalog.selectable()
        if model_catalog.allowed_for(spec, tier)
    }
    if expected is None:
        assert allowed == {spec.id for spec in model_catalog.selectable()}
    else:
        assert allowed == expected


def test_a_tier_we_do_not_know_fails_closed():
    """A renamed tier must not open every model."""
    opus = model_catalog.BY_ID["claude-opus-5-5"]
    luna = model_catalog.BY_ID["gpt-6.1-luna"]
    assert model_catalog.allowed_for(opus, "something-new") is False
    # Models open to everyone stay open, which is what keeps the product
    # working rather than dark, while the restricted ones stay shut.
    assert model_catalog.allowed_for(luna, "something-new") is True


def test_the_default_for_a_tier_is_a_model_that_tier_may_have():
    for tier in model_catalog.TIER_ORDER:
        name = model_catalog.default_for(tier)
        assert model_catalog.allowed_for(model_catalog.BY_ID[name], tier), tier
