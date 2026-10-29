"""Picking a model: who may have what, what sticks, and what refuses.

The rules this file holds are the ones that cost money if they are wrong:

  * A guest may not pick an expensive model, because their cap counts turns
    rather than dollars (ModelSpec.min_tier).
  * A client-supplied model is validated; an unrecognised one is a 400, never
    a silent substitution.
  * A stored preference is validated on *read* too, so a tier change or a
    retired model heals instead of failing a turn.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import GuestSession, LlmCall, User
from app.services import model_catalog, model_choice, usage
from app.services.consent import record_consent
from tests.anthropic_script import ScriptedAnthropic, anthropic_message, text_block
from tests.test_chat import post_chat
from tests.test_usage import _signed_in

SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
LUNA = "gpt-6.1-luna"


@pytest.fixture
def both_providers(openai_enabled, anthropic_enabled):
    """Both credentials present, which is what the picker is designed for."""
    return True


# --------------------------------------------------------------------------
# What the table says
# --------------------------------------------------------------------------


def test_a_guest_sees_the_locked_models_rather_than_a_short_list(
    client: TestClient, both_providers
):
    """Hiding them would leave a guest unable to discover what an account buys.

    That is the one thing this table is meant to tell them, so a model they
    may not have is sent with `available: false` and a `min_tier` naming what
    unlocks it -- not omitted.
    """
    body = client.get("/v1/models").json()
    assert body["tier"] == "guest"
    rows = {row["id"]: row for row in body["models"]}
    assert set(rows) == {spec.id for spec in model_catalog.selectable()}
    assert rows[OPUS]["available"] is False
    assert rows[OPUS]["min_tier"] == "registered"
    assert rows[model_catalog.DEFAULT_MODEL]["available"] is True
    assert body["selected"] == model_catalog.DEFAULT_MODEL


def test_the_table_quotes_a_relative_cost_and_an_edit_count(
    client: TestClient, both_providers
):
    """The two columns of the public cost table, and they must not disagree.

    Both derive from `usage.edit_uusd`, so this also pins that the published
    figures come from the same arithmetic the meter bills with.
    """
    rows = {row["id"]: row for row in client.get("/v1/models").json()["models"]}
    reference = rows[model_catalog.REFERENCE_MODEL]
    assert reference["cost_weight"] == 1.0

    # Cheaper model: lower weight, more edits. Dearer: the reverse.
    assert rows[LUNA]["cost_weight"] < 1.0 < rows[OPUS]["cost_weight"]
    assert (
        rows[LUNA]["approx_edits"]["registered"]
        > reference["approx_edits"]["registered"]
        > rows[OPUS]["approx_edits"]["registered"]
    )
    # The guest tier is absent from every row: it is capped on a count of
    # turns, so its figure does not vary by model. Listing one would be a
    # number that does not mean what the column header says.
    for row in rows.values():
        assert "guest" not in row["approx_edits"]
        assert set(row["approx_edits"]) == {"registered", "matching"}
    # Matching has 5x the credits, so 5x the edits, on every model.
    for row in rows.values():
        assert row["approx_edits"]["matching"] > row["approx_edits"]["registered"]


def test_a_provisional_price_says_so(client: TestClient, both_providers):
    """Seeded prices are disclosed, not hidden behind a confident number."""
    rows = {row["id"]: row for row in client.get("/v1/models").json()["models"]}
    for name, row in rows.items():
        expected = model_catalog.BY_ID[name].verified is None
        assert row["prices_provisional"] is expected, name


def test_the_table_carries_no_per_token_price(client: TestClient, both_providers):
    """Nobody knows how many tokens their resume is, so a rate is not a number
    anyone can act on -- and publishing one invites doing the arithmetic the
    meter already does."""
    body = client.get("/v1/models").text
    for leaked in ("uusd", "per_1m", "input_rate", "cache_write"):
        assert leaked not in body.lower()


# --------------------------------------------------------------------------
# Choosing
# --------------------------------------------------------------------------


def test_a_choice_sticks_across_requests(
    client: TestClient, both_providers, db_session
):
    _signed_in(client, db_session, sub="sub-sticks")
    assert client.put("/v1/models/selection", json={"model": SONNET}).json()[
        "selected"
    ] == SONNET
    # A fresh read, with nothing in the request saying so.
    assert client.get("/v1/models").json()["selected"] == SONNET


def test_a_guest_choice_sticks_too(client: TestClient, both_providers):
    """A guest has a row for the quota already, so there is somewhere to put
    it -- and the server never has to trust a model id a browser remembered."""
    assert client.put("/v1/models/selection", json={"model": LUNA}).json()[
        "selected"
    ] == LUNA
    assert client.get("/v1/models").json()["selected"] == LUNA


def test_a_guest_cannot_pick_an_expensive_model(client: TestClient, both_providers):
    """Their cap counts turns, not dollars, so it would not notice."""
    response = client.put("/v1/models/selection", json={"model": OPUS})
    assert response.status_code == 400
    assert client.get("/v1/models").json()["selected"] == model_catalog.DEFAULT_MODEL


def test_an_account_can_pick_any_offered_model(
    client: TestClient, both_providers, db_session
):
    """Cost-capped, so the choice is self-funding: an expensive model simply
    spends the same allowance faster."""
    _signed_in(client, db_session, sub="sub-any")
    for name in (OPUS, SONNET, LUNA, "gpt-6.1-sol"):
        assert (
            client.put("/v1/models/selection", json={"model": name}).json()["selected"]
            == name
        )


def test_an_unknown_model_is_refused(client: TestClient, both_providers, db_session):
    _signed_in(client, db_session, sub="sub-unknown")
    assert (
        client.put("/v1/models/selection", json={"model": "gpt-9-imaginary"}).status_code
        == 400
    )
    # Including a model that exists in the catalogue but is not selectable --
    # a retired default is priceable, not pickable.
    assert (
        client.put("/v1/models/selection", json={"model": "gpt-5.6-terra"}).status_code
        == 400
    )


def test_a_label_a_human_would_type_is_accepted(
    client: TestClient, both_providers, db_session
):
    _signed_in(client, db_session, sub="sub-label")
    body = client.put("/v1/models/selection", json={"model": "Claude Opus 5.5"}).json()
    assert body["selected"] == OPUS


# --------------------------------------------------------------------------
# Resolution, including the healing paths
# --------------------------------------------------------------------------


def test_a_stored_model_the_tier_may_no_longer_have_falls_back(
    client: TestClient, both_providers, db_session
):
    """Someone who picked Opus with matching on, then withdrew consent.

    Their column still says Opus. Reading it as authoritative would run a
    model their tier may not have; refusing would break their next turn. It
    falls back, silently, because nobody asked for it on this request.
    """
    user = User(google_sub="sub-heal", email="heal@example.com", locale="en")
    user.chat_model = OPUS
    db_session.add(user)
    db_session.commit()

    # As a registered user, Opus is fine.
    assert (
        model_choice.selected(db_session, user, None, tier=usage.REGISTERED) == OPUS
    )
    # As a guest-tier caller it is not, so the default stands in.
    assert model_choice.selected(
        db_session, user, None, tier=usage.GUEST
    ) == model_catalog.default_for(usage.GUEST)
    # And the stored value is left alone rather than rewritten behind their
    # back: if they opt back in, their choice is still there.
    assert user.chat_model == OPUS


def test_a_retired_model_in_the_column_falls_back(
    client: TestClient, both_providers, db_session
):
    user = User(google_sub="sub-retired", email="r@example.com", locale="en")
    user.chat_model = "gpt-5.6-terra"  # priceable, no longer offered
    db_session.add(user)
    db_session.commit()
    assert model_choice.selected(
        db_session, user, None, tier=usage.REGISTERED
    ) == model_catalog.DEFAULT_MODEL


def test_a_model_whose_provider_lost_its_key_falls_back(
    client: TestClient, openai_enabled, db_session
):
    """No ANTHROPIC_API_KEY, and someone's column says Sonnet."""
    user = User(google_sub="sub-keyless", email="k@example.com", locale="en")
    user.chat_model = SONNET
    db_session.add(user)
    db_session.commit()
    assert model_choice.selected(
        db_session, user, None, tier=usage.REGISTERED
    ) == model_catalog.DEFAULT_MODEL


def test_an_operator_pin_outside_the_catalogue_is_honoured(
    client: TestClient, openai_enabled, db_session, monkeypatch
):
    """OPENAI_BASE_URL may be a proxy serving its own ids, and that has always
    worked. It is priced as the dearest row, so it is not a cheap tier."""
    from app.config import get_settings

    monkeypatch.setenv("CHAT_MODEL", "my-proxy-model")
    get_settings.cache_clear()
    try:
        assert model_choice.default_model(usage.REGISTERED) == "my-proxy-model"
        spec = model_catalog.spec_or_passthrough("my-proxy-model")
        dearest = max(usage.PRICES, key=lambda name: usage.PRICES[name]["input"])
        assert spec.prices.as_dict() == usage.PRICES[dearest]
    finally:
        get_settings.cache_clear()


def test_an_operator_pin_a_guest_may_not_have_is_ignored_for_them(
    client: TestClient, both_providers, monkeypatch
):
    """`min_tier` stops a guest spending an account's money. A pin must not be
    a way around it."""
    from app.config import get_settings

    monkeypatch.setenv("CHAT_MODEL", OPUS)
    get_settings.cache_clear()
    try:
        assert model_choice.default_model(usage.REGISTERED) == OPUS
        assert model_choice.default_model(usage.GUEST) != OPUS
        assert model_catalog.allowed_for(
            model_catalog.BY_ID[model_choice.default_model(usage.GUEST)], usage.GUEST
        )
    finally:
        get_settings.cache_clear()


def test_the_legacy_openai_model_setting_is_still_read(monkeypatch):
    """`.env.production` on the host sets OPENAI_MODEL. A deploy that silently
    reverted to the catalogue default would change what every turn costs."""
    from app.config import get_settings

    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("CHAT_MODEL", "")
    get_settings.cache_clear()
    try:
        assert get_settings().pinned_chat_model() == "gpt-5.6-terra"
        # And CHAT_MODEL wins where both are set.
        monkeypatch.setenv("CHAT_MODEL", OPUS)
        get_settings.cache_clear()
        assert get_settings().pinned_chat_model() == OPUS
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------
# A chat turn
# --------------------------------------------------------------------------


def test_a_turn_runs_on_the_stored_choice(
    client: TestClient, both_providers, db_session, monkeypatch
):
    _signed_in(client, db_session, sub="sub-turn")
    client.put("/v1/models/selection", json={"model": SONNET})
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic([anthropic_message(blocks=[text_block("ok")])])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    post_chat(client, created["id"], json={"message": "hi"})
    assert scripted.requests[0]["model"] == SONNET


def test_an_explicit_model_on_the_request_wins_and_is_remembered(
    client: TestClient, both_providers, db_session, monkeypatch
):
    """Otherwise the picker on the next page load would disagree with the
    model the turn actually ran on."""
    _signed_in(client, db_session, sub="sub-explicit")
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    scripted = ScriptedAnthropic([anthropic_message(blocks=[text_block("ok")])])
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)

    post_chat(client, created["id"], json={"message": "hi", "model": OPUS})
    assert scripted.requests[0]["model"] == OPUS
    assert client.get("/v1/models").json()["selected"] == OPUS


def test_an_explicit_model_the_caller_may_not_have_is_a_plain_400(
    client: TestClient, both_providers
):
    """Before the stream opens, so the refusal is ordinary JSON rather than an
    error frame on a stream that then has to end."""
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    response = client.post(
        f"/v1/resumes/{created['id']}/chat", json={"message": "hi", "model": OPUS}
    )
    assert response.status_code == 400
    assert "event-stream" not in (response.headers.get("content-type") or "")


def test_the_meter_is_quoted_against_the_chosen_model(
    client: TestClient, both_providers, db_session
):
    """The same balance is worth far fewer edits on an expensive model. A
    figure that did not move when someone switched would be a lie."""
    user = _signed_in(client, db_session, sub="sub-meter")
    assert user is not None

    client.put("/v1/models/selection", json={"model": LUNA})
    cheap = client.get("/v1/usage").json()
    client.put("/v1/models/selection", json={"model": OPUS})
    dear = client.get("/v1/usage").json()

    assert cheap["model"] == LUNA and dear["model"] == OPUS
    assert cheap["approx_edits_left"] > dear["approx_edits_left"]
    # Same allowance, different reach.
    assert cheap["limit_credits"] == dear["limit_credits"]


def test_a_persons_own_history_supersedes_the_list_price(
    client: TestClient, both_providers, db_session
):
    """Once there is a trailing mean, what they actually spent is a better
    predictor than any model's published rate -- and it already carries
    whichever models they spent it on."""
    user = _signed_in(client, db_session, sub="sub-history")
    bal = usage.balance(db_session, user, None)
    from dataclasses import replace

    # Three turns of history at a known mean.
    spent = replace(bal, turns_used=4, used_uusd=4 * 50_000, remaining_uusd=1_000_000)
    for model in (LUNA, OPUS):
        assert (
            usage.approx_edits_left(db_session, user, None, spent, model=model)
            == 1_000_000 // 50_000
        )


def test_matching_consent_unlocks_nothing_extra_but_stretches_further(
    client: TestClient, both_providers, db_session
):
    """Every model is already open to a registered account. The matching tier
    buys credits, not capability, and the table has to say that rather than
    implying a locked row."""
    user = _signed_in(client, db_session, sub="sub-matching")
    record_consent(
        db_session,
        user=user,
        purpose=usage.MATCHING_PURPOSE,
        action="granted",
        locale="en",
        source="test",
    )
    db_session.commit()

    body = client.get("/v1/models").json()
    assert body["tier"] == "matching"
    assert all(row["available"] for row in body["models"])


def test_choosing_does_not_mint_a_guest_session_on_a_mere_read(
    client: TestClient, both_providers, db_session
):
    """Reading the table is a page load; choosing is a deliberate action.

    Same rule /v1/usage follows -- a crawler or a first-time visitor asking
    what the models cost must not create a row.
    """
    before = db_session.query(GuestSession).count()
    client.get("/v1/models")
    assert db_session.query(GuestSession).count() == before
    client.put("/v1/models/selection", json={"model": LUNA})
    assert db_session.query(GuestSession).count() == before + 1


def test_no_llm_call_rows_are_written_by_reading_the_table(
    client: TestClient, both_providers, db_session
):
    """It is a catalogue, not a model call."""
    client.get("/v1/models")
    assert db_session.query(LlmCall).count() == 0
