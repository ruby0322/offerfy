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


def test_a_cache_write_is_always_one_and_a_quarter_times_input():
    """The one cache multiplier that has held on every row of both tables.

    Hand-typed cache rates are how a price table drifts from itself: the
    numbers look plausible at a glance and the error only shows up in a bill.
    This one is derived in `_rates` rather than typed, and this guards that.
    """
    for spec in model_catalog.MODELS:
        assert spec.prices.cache_write == spec.prices.input * 125 // 100, spec.id


# What each model charges to *read* a cached token, as a fraction of its input
# rate. Spelled out per model rather than asserted as one ratio, because it is
# not one ratio -- and an earlier version of this file assumed it was.
#
# That assumption was wrong by a factor of two the moment it met a real price
# list: the Claude 5.5 generation reads at 0.05x where everything before it
# read at 0.1x. Cached tokens are ~80% of a turn's input here, so the error
# over-billed Sonnet and Opus by nearly double on the input line. Checked
# against the published pricing page on 2026-10-29.
CACHE_READ_RATIO = {
    "gpt-6.1-terra": 20,
    "gpt-6.1-sol": 20,
    "gpt-5.6-terra": 10,
    "gpt-5.6-sol": 10,
    "gpt-5.6-luna": 10,
    "gpt-6-luna": 10,
    "claude-haiku-5-5": 10,
    "claude-sonnet-5-5": 20,
    "claude-opus-5-5": 20,
}


def test_every_model_states_its_own_cache_read_rate():
    """No model may be added without someone looking its read rate up."""
    assert set(CACHE_READ_RATIO) == {spec.id for spec in model_catalog.MODELS}
    for spec in model_catalog.MODELS:
        divisor = CACHE_READ_RATIO[spec.id]
        assert spec.prices.cached == spec.prices.input // divisor, spec.id


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
    # No argument means the reference, never the dearest model.
    assert usage.edit_uusd() == usage.edit_uusd(model_catalog.REFERENCE_MODEL)


def test_the_measurement_belongs_to_the_model_it_was_taken_on():
    """EDIT_UUSD is a number measured on one model, not on whichever model
    happens to be the default.

    Conflating the two was a real bug: the measurement was taken on
    gpt-5.6-terra, the default moved to gpt-6.1-terra at half the price, and
    every published "about N edits" then understated an allowance by about
    half. Only the model named here may read back the raw constant.
    """
    assert usage.edit_uusd(usage.EDIT_MEASURED_ON) == usage.EDIT_UUSD
    assert usage.EDIT_MEASURED_ON in usage.PRICES
    # And the reference is a different model, or this test proves nothing.
    assert usage.EDIT_MEASURED_ON != model_catalog.REFERENCE_MODEL


def test_a_dearer_model_costs_more_per_edit_and_buys_fewer():
    """The whole claim the public cost table makes, checked once.

    Three distinct price points rather than one either side of the reference:
    the reference is the *cheapest* selectable row, so there is nothing below
    it to compare against and an assertion written as `cheap < reference`
    would be unsatisfiable rather than wrong.
    """
    reference = usage.edit_uusd(model_catalog.REFERENCE_MODEL)
    dearer = usage.edit_uusd("gpt-6.1-terra")
    dearest = usage.edit_uusd("claude-opus-5-5")
    assert reference < dearer < dearest
    assert 1.0 < usage.cost_weight("gpt-6.1-terra") < usage.cost_weight(
        "claude-opus-5-5"
    )


def test_two_rows_at_the_same_price_weigh_the_same():
    """`gpt-6-luna` and `claude-haiku-5-5` are priced identically, column for
    column, across two providers -- so the table must quote them identically.

    Worth pinning because the reference model is one of the two: a weight that
    read 1.0 for the default and anything else for its twin would mean the
    arithmetic had picked up the model's identity somewhere it should only
    have read its rates.
    """
    assert model_catalog.BY_ID["gpt-6-luna"].prices == (
        model_catalog.BY_ID["claude-haiku-5-5"].prices
    )
    assert usage.cost_weight("gpt-6-luna") == usage.cost_weight("claude-haiku-5-5")


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
        ("guest", {"gpt-6.1-terra", "gpt-6-luna", "claude-haiku-5-5"}),
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
    luna = model_catalog.BY_ID["gpt-6-luna"]
    assert model_catalog.allowed_for(opus, "something-new") is False
    # Models open to everyone stay open, which is what keeps the product
    # working rather than dark, while the restricted ones stay shut.
    assert model_catalog.allowed_for(luna, "something-new") is True


def test_the_default_for_a_tier_is_a_model_that_tier_may_have():
    for tier in model_catalog.TIER_ORDER:
        name = model_catalog.default_for(tier)
        assert model_catalog.allowed_for(model_catalog.BY_ID[name], tier), tier
