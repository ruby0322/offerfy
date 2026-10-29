"""The weekly usage tiers.

Three groups: the cost arithmetic (pure, no HTTP), the window, and enforcement
end to end. The two attribution regression tests are the point of migration
014 and are the easiest thing here to silently undo.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import GuestSession, LlmCall, Resume, User
from app.services import usage
from app.services.consent import record_consent
from app.services.metering import Meter, system_guard
from tests.openai_script import ScriptedOpenAI, llm_message, llm_usage
from tests.test_chat import post_chat


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _signed_in(client, db_session, sub="sub-usage"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _resume(db_session, *, user=None, guest=None):
    resume = Resume(
        title="R",
        typst_source="= R",
        source="create",
        user_id=user.id if user else None,
        guest_session_id=guest.id if guest else None,
    )
    db_session.add(resume)
    db_session.commit()
    return resume


def _spend(
    db_session,
    *,
    user=None,
    guest=None,
    resume=None,
    turns=1,
    purpose="chat",
    input_tokens=20_000,
    cached_tokens=0,
    output_tokens=0,
    cache_write_tokens=0,
    when=None,
):
    """Spend through the real meter, so these tests exercise the real path.

    Deliberately not a direct INSERT into usage_counters: that would let the
    pricing, the turn detection and the window bucketing drift away from what
    production does without a single test noticing. `Meter.record` is sync, so
    it works here without an event loop.

    The LlmCall row is written too, because that is what a real round does --
    telemetry and counter are two writes with two durabilities -- and the
    reconciliation tests need both sides.
    """
    guard = system_guard(
        user=user, guest=guest, purpose=purpose, reason="test fixture", now=when
    )
    meter = Meter(guard)
    for _ in range(turns):
        db_session.add(
            LlmCall(
                resume_id=resume.id if resume else None,
                user_id=user.id if user else None,
                guest_session_id=guest.id if guest else None,
                purpose=purpose,
                model="gpt-5.6-terra",
                round_index=0,
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                cache_write_tokens=cache_write_tokens,
                created_at=when or datetime.now(timezone.utc),
            )
        )
        db_session.commit()
        meter.record(
            {
                "model": "gpt-5.6-terra",
                "purpose": purpose,
                "round_index": 0,
                "input_tokens": input_tokens,
                "cached_tokens": cached_tokens,
                "output_tokens": output_tokens,
                "cache_write_tokens": cache_write_tokens,
            }
        )


def _row(**kwargs):
    defaults = dict(
        model="gpt-5.6-terra",
        input_tokens=None,
        cached_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        cache_write_tokens=None,
    )
    return LlmCall(purpose="chat", round_index=0, **{**defaults, **kwargs})


# --------------------------------------------------------------------------
# cost arithmetic -- pure, no database
# --------------------------------------------------------------------------


def test_a_cached_token_is_a_discount_not_an_extra_charge():
    """cached_tokens is a slice of input_tokens, so it must not bill twice.

    Same total input, all of it cached, has to come out an order of magnitude
    cheaper -- not the same, and not more.
    """
    uncached, _ = usage.round_cost_uusd(_row(input_tokens=1_000_000))
    cached, _ = usage.round_cost_uusd(
        _row(input_tokens=1_000_000, cached_tokens=1_000_000)
    )
    assert uncached == 2_000_000       # 1M tokens at $2.00/1M
    assert cached == 200_000           # the same tokens at $0.20/1M
    assert cached * 10 == uncached


def test_reasoning_tokens_are_not_added_to_output():
    """reasoning_tokens is a slice of output_tokens. Adding it charges twice."""
    plain, _ = usage.round_cost_uusd(_row(input_tokens=0, output_tokens=1_000))
    thinking, _ = usage.round_cost_uusd(
        _row(input_tokens=0, output_tokens=1_000, reasoning_tokens=900)
    )
    assert plain == thinking == 12_000  # 1k tokens at $12.00/1M


def test_a_cache_write_is_its_own_charge_on_top():
    """The one token count that is NOT a subset of another, so it does add.

    Billed at 1.25x the uncached input rate, which is the whole reason the
    column had to exist.
    """
    without, _ = usage.round_cost_uusd(_row(input_tokens=1_000_000))
    with_write, _ = usage.round_cost_uusd(
        _row(input_tokens=1_000_000, cache_write_tokens=1_000_000)
    )
    assert with_write - without == 2_500_000
    assert with_write - without == int(without * 1.25)


def test_an_unmeasurable_round_is_charged_the_fallback_and_flagged():
    """Not free. A proxy that drops `usage` must not make the product free."""
    cost, measured = usage.round_cost_uusd(_row(input_tokens=None))
    assert cost == usage.FALLBACK_ROUND_UUSD > 0
    assert measured is False


def test_an_unknown_model_is_charged_at_the_most_expensive_known_rate():
    """OPENAI_MODEL is a config string. An unrecognised one is not a free tier."""
    known = {
        name: usage.round_cost_uusd(_row(model=name, input_tokens=1_000_000))[0]
        for name in usage.PRICES
    }
    unknown, _ = usage.round_cost_uusd(
        _row(model="some-model-we-have-never-heard-of", input_tokens=1_000_000)
    )
    assert unknown == max(known.values())


def test_more_cached_tokens_than_input_tokens_does_not_go_negative():
    cost, _ = usage.round_cost_uusd(_row(input_tokens=10, cached_tokens=10_000))
    assert cost >= 0


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------


def test_the_window_starts_monday_midnight_in_taipei():
    # Wednesday 2026-10-21, 03:00 UTC == 11:00 Taipei.
    now = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
    start = usage.window_start(now)
    assert start.astimezone(usage.TAIPEI).strftime("%Y-%m-%d %H:%M") == "2026-10-19 00:00"
    assert start.astimezone(usage.TAIPEI).weekday() == 0


def test_the_window_rolls_over_at_the_taipei_boundary_not_the_utc_one():
    """Sunday 23:00 Taipei is Sunday 15:00 UTC -- still last week.

    An hour later it is Monday in Taipei while still Sunday in UTC, and that
    is the moment the allowance refills.
    """
    before = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)   # Sun 23:00 Taipei
    after = datetime(2026, 10, 25, 16, 30, tzinfo=timezone.utc)   # Mon 00:30 Taipei
    assert usage.window_start(before) < usage.window_start(after)
    assert usage.window_start(after) == usage.window_end(before)


# --------------------------------------------------------------------------
# tiers
# --------------------------------------------------------------------------


def test_tier_follows_the_consent_ledger(client: TestClient, db_session):
    guest = GuestSession(key_hash="g" * 64)
    db_session.add(guest)
    db_session.commit()
    assert usage.tier_of(db_session, None, guest) == usage.GUEST

    user = _signed_in(client, db_session)
    assert usage.tier_of(db_session, user, None) == usage.REGISTERED

    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action="granted",
        locale="en",
        source="settings",
    )
    db_session.commit()
    assert usage.tier_of(db_session, user, None) == usage.MATCHING

    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action="withdrawn",
        locale="en",
        source="settings",
    )
    db_session.commit()
    assert usage.tier_of(db_session, user, None) == usage.REGISTERED


def test_the_matching_tier_is_five_times_the_signed_in_one():
    """The ratio is a product promise, so it is pinned rather than implied."""
    registered = usage.TIERS[usage.REGISTERED].credits
    matching = usage.TIERS[usage.MATCHING].credits
    assert matching == registered * 5


def test_the_signed_in_tier_is_enough_to_finish_the_advertised_job():
    """Not a style assertion -- it is the constraint that keeps the matching
    bonus lawfully a thank-you rather than a toll. A full polish measures 8-15
    edits, so the tier that needs no opt-in has to clear the top of that."""
    assert usage.TIERS[usage.REGISTERED].credits >= 15


def test_withdrawing_matching_does_not_claw_back_the_current_window(
    client: TestClient, db_session, enforced
):
    """A withdrawal that invalidates the week is a penalty for withdrawing."""
    user = _signed_in(client, db_session)
    for action in ("granted",):
        record_consent(
            db_session,
            user=user,
            purpose="talent_introduction",
            action=action,
            locale="en",
            source="settings",
        )
    db_session.commit()

    # Spend past the signed-in ceiling but inside the matching one.
    spend_uusd = usage.TIERS[usage.REGISTERED].credits * usage.CREDIT_UUSD * 2
    _spend(db_session, user=user, turns=1, input_tokens=spend_uusd // 2)
    assert usage.balance(db_session, user, None).tier == usage.MATCHING

    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action="withdrawn",
        locale="en",
        source="settings",
    )
    db_session.commit()

    bal = usage.balance(db_session, user, None)
    # The tier drops, so the balance now reads overdrawn -- but nothing was
    # deleted, nothing was refunded, and the reset is still the same Monday.
    assert bal.tier == usage.REGISTERED
    assert bal.used_uusd > 0
    assert bal.reset_at == usage.window_end()


# --------------------------------------------------------------------------
# attribution -- the two holes migration 014 exists to close
# --------------------------------------------------------------------------


def test_deleting_the_resume_does_not_clear_the_spend(
    client: TestClient, db_session, enforced
):
    """resume_id is ON DELETE SET NULL and deleting a resume is one click.

    Spend joined through it would be clearable on demand.
    """
    user = _signed_in(client, db_session)
    resume = _resume(db_session, user=user)
    _spend(db_session, user=user, resume=resume, turns=3)
    before = usage.balance(db_session, user, None).used_uusd
    assert before > 0

    assert client.delete(f"/v1/resumes/{resume.id}").status_code == 204
    db_session.expire_all()

    after = usage.balance(db_session, user, None)
    assert after.used_uusd == before
    assert after.turns_used == 3


def test_claiming_does_not_bill_the_new_account_for_the_guest_trial(
    client: TestClient, db_session, enforced
):
    """/v1/auth/claim rewrites resumes.user_id.

    Joined through the resume, every round the guest already spent would
    retroactively land on the account they just signed up with -- charging a
    new signup for the trial it converted from.
    """
    guest = GuestSession(key_hash="h" * 64)
    db_session.add(guest)
    db_session.commit()
    resume = _resume(db_session, guest=guest)
    _spend(db_session, guest=guest, resume=resume, turns=4)

    user = _signed_in(client, db_session, sub="sub-claimer")
    resume.user_id = user.id
    resume.guest_session_id = None
    db_session.commit()

    fresh = usage.balance(db_session, user, None)
    assert fresh.used_uusd == 0
    assert fresh.turns_used == 0
    # And the guest's own spend is still on the guest.
    assert usage.balance(db_session, None, guest).turns_used == 4


# --------------------------------------------------------------------------
# enforcement
# --------------------------------------------------------------------------


def _script(usage_block=None):
    return ScriptedOpenAI(
        [llm_message(content="Done.", usage=usage_block or llm_usage(input_tokens=100))]
    )


def test_a_guest_is_refused_on_the_fifth_turn_with_somewhere_to_go(
    client: TestClient, db_session, monkeypatch, openai_enabled, enforced
):
    guest = GuestSession(key_hash="i" * 64)
    db_session.add(guest)
    db_session.commit()
    resume = _resume(db_session, guest=guest)
    client.cookies.set("offerfy_guest", "irrelevant")

    limit = usage.TIERS[usage.GUEST].turns
    _spend(db_session, guest=guest, resume=resume, turns=limit)

    # The cookie value above is not the guest's, so drive the balance directly
    # and assert the refusal payload rather than the transport.
    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, None, guest)
    payload = caught.value.detail
    assert caught.value.status_code == 429
    assert payload["limit"] == "weekly_turns"
    assert payload["tier"] == usage.GUEST
    assert payload["turn_limit"] == limit
    # The refusal is the signup moment. It has to name what signing in gives.
    assert payload["next_tier"]["name"] == usage.REGISTERED
    assert payload["next_tier"]["credits"] == usage.TIERS[usage.REGISTERED].credits
    assert payload["reset_at"] == usage.window_end().isoformat()


def test_a_signed_in_person_is_refused_once_the_credits_are_gone(
    client: TestClient, db_session, enforced
):
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    # One fat round that spends the whole weekly ceiling.
    _spend(db_session, user=user, turns=1, input_tokens=tier.credits * usage.CREDIT_UUSD)

    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, user, None)
    payload = caught.value.detail
    assert payload["limit"] == "weekly_credits"
    assert payload["next_tier"]["name"] == usage.MATCHING


def test_an_overrun_becomes_debt_rather_than_a_refund(
    client: TestClient, db_session, enforced
):
    """A turn that starts is allowed to finish, however much it costs.

    What it overruns by is carried, so the balance goes negative and the *next*
    turn is the one that is refused.
    """
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    _spend(
        db_session,
        user=user,
        turns=1,
        input_tokens=tier.credits * usage.CREDIT_UUSD * 3,
    )
    bal = usage.balance(db_session, user, None)
    assert bal.remaining_uusd is not None and bal.remaining_uusd < 0
    assert bal.exhausted
    with pytest.raises(Exception):
        usage.enforce_usage(db_session, user, None)


def test_the_window_reset_clears_the_debt(client: TestClient, db_session, enforced):
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    _spend(
        db_session,
        user=user,
        turns=2,
        input_tokens=tier.credits * usage.CREDIT_UUSD,
        when=usage.window_start() - timedelta(minutes=5),
    )
    bal = usage.balance(db_session, user, None)
    assert bal.used_uusd == 0
    assert not bal.exhausted


def test_a_long_session_is_not_refused_for_being_fast(
    client: TestClient, db_session, enforced
):
    """There is no hourly cap on chat. Removed 2026-10-25.

    An hourly cap on top of a weekly budget only decided *when* someone could
    spend an allowance that was already theirs, and what it mostly caught was a
    long focused session -- the session worth having. The weekly budget is the
    guard; the instance is bounded by the ceilings.
    """
    user = _signed_in(client, db_session)
    # Far more turns in the last hour than any previous hourly cap allowed,
    # kept cheap so the weekly budget is nowhere near spent.
    _spend(db_session, user=user, turns=60, input_tokens=10)
    assert usage.balance(db_session, user, None).remaining_uusd > 0
    # Must not raise.
    bal = usage.enforce_usage(db_session, user, None)
    assert not bal.exhausted
    assert _refusals(db_session) == []


def test_the_profile_build_limit_is_still_hourly(
    client: TestClient, db_session, enforced
):
    """The one hourly limit that stays, because it is not the one that was
    removed: it predates this work as REFRESH_LIMIT_PER_HOUR and guards a
    different endpoint."""
    user = _signed_in(client, db_session, sub="sub-profile-hourly")
    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action="granted",
        locale="en",
        source="settings",
    )
    db_session.commit()
    tier = usage.TIERS[usage.MATCHING]
    assert tier.profile_builds_per_hour > 0
    _spend(
        db_session,
        user=user,
        turns=tier.profile_builds_per_hour,
        purpose="profile",
        input_tokens=10,
    )
    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, user, None, purpose="profile")
    assert caught.value.detail["limit"] == "burst"
    # And chat is unaffected by having spent the profile allowance.
    usage.enforce_usage(db_session, user, None, purpose="chat")


def test_the_guest_pool_ceiling_converts_rather_than_refusing(
    client: TestClient, db_session, monkeypatch, enforced
):
    """A cookie is free to clear, so the per-session count is a speed bump.

    The pool ceiling is the wall, and it has to degrade into the signup path
    instead of into an error.
    """
    monkeypatch.setenv("GUEST_WEEKLY_CEILING_UUSD", "1000")
    get_settings.cache_clear()
    spender = GuestSession(key_hash="j" * 64)
    newcomer = GuestSession(key_hash="k" * 64)
    db_session.add_all([spender, newcomer])
    db_session.commit()
    _spend(db_session, guest=spender, turns=1, input_tokens=1_000_000)

    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, None, newcomer)
    payload = caught.value.detail
    assert payload["limit"] == "guest_pool"
    assert payload["sign_in_required"] is True


def test_the_global_ceiling_says_what_is_still_free(
    client: TestClient, db_session, monkeypatch, enforced
):
    monkeypatch.setenv("GLOBAL_WEEKLY_CEILING_UUSD", "1000")
    get_settings.cache_clear()
    user = _signed_in(client, db_session)
    # Small enough that this person's own weekly ceiling is nowhere near --
    # otherwise the personal limit fires first and this proves nothing.
    _spend(db_session, user=user, turns=1, input_tokens=1_000)
    assert usage.balance(db_session, user, None).remaining_uusd > 0

    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, user, None)
    payload = caught.value.detail
    assert payload["limit"] == "global_ceiling"
    # Editing and exporting are CPU, not tokens, and stay up. Saying so is the
    # difference between a budget cap and an outage.
    assert "export" in payload["detail"]


def test_development_does_not_enforce(client: TestClient, db_session):
    """APP_ENV=development in conftest, and no override."""
    user = _signed_in(client, db_session)
    _spend(
        db_session,
        user=user,
        turns=50,
        input_tokens=usage.TIERS[usage.REGISTERED].credits * usage.CREDIT_UUSD,
    )
    assert usage.limits_enabled() is False
    # Returns a balance instead of raising.
    assert usage.enforce_usage(db_session, user, None).exhausted


# --------------------------------------------------------------------------
# the quota frame and the read endpoint
# --------------------------------------------------------------------------


def test_the_quota_frame_carries_no_telemetry(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    """The counterpart of `usage`, which is persisted and never relayed.

    Asserted as an absence: if token counts, a model name or a dollar figure
    ever appear here, the comment in chat.py about telemetry not being the
    client's business has quietly stopped being true.
    """
    scripted = _script(
        llm_usage(
            input_tokens=20_000,
            cached_tokens=16_000,
            cache_write_tokens=4_000,
            output_tokens=300,
            reasoning_tokens=90,
        )
    )
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    resume = client.post("/v1/resumes", json={}).json()
    result = post_chat(client, resume["id"], json={"message": "hi"})
    assert result["status"] == 200

    quotas = [event for event in result["events"] if event.get("type") == "quota"]
    assert len(quotas) == 1
    frame = quotas[0]
    assert set(frame) == {
        "type",
        "tier",
        "reset_at",
        "used_credits",
        "limit_credits",
        "turns_used",
        "turn_limit",
        "approx_edits_left",
    }
    body = str(frame)
    for leaked in ("token", "uusd", "gpt-", "model", "usd", "cost"):
        assert leaked not in body.lower()
    assert frame["tier"] == usage.GUEST
    assert frame["turns_used"] == 1

    # And the cache write it was told about was recorded, not dropped.
    row = db_session.query(LlmCall).filter(LlmCall.purpose == "chat").one()
    assert row.cache_write_tokens == 4_000
    assert row.guest_session_id is not None
    assert row.user_id is None


def test_the_quota_frame_arrives_before_done(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    """`done` stays the last frame of a successful turn."""
    scripted = _script()
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda *a, **k: scripted)
    resume = client.post("/v1/resumes", json={}).json()
    result = post_chat(client, resume["id"], json={"message": "hi"})
    types = [event.get("type") for event in result["events"]]
    assert types[-1] == "done"
    assert types.index("quota") == len(types) - 2


def test_usage_endpoint_answers_for_a_guest_with_no_cookie(client: TestClient):
    """The people with the smallest allowance most need to see it, so this is
    /v1/usage and not /v1/me/usage -- and asking must not mint a session."""
    body = client.get("/v1/usage").json()
    assert body["tier"] == usage.GUEST
    assert body["turn_limit"] == usage.TIERS[usage.GUEST].turns
    assert body["turns_used"] == 0
    assert body["turns_remaining"] == usage.TIERS[usage.GUEST].turns
    assert body["limit_credits"] is None       # guests are capped on a count
    assert body["approx_edits_left"] == usage.TIERS[usage.GUEST].turns
    assert body["exhausted"] is False
    assert "offerfy_guest" not in client.cookies


def test_usage_endpoint_names_both_ends_of_the_window(client: TestClient):
    """period_start and reset_at come from one reading of the clock.

    Both derive from `Balance`, not from a second window_start() call, so they
    cannot straddle a Monday boundary and report a period that ends before it
    begins -- which is the failure a plan page showing "27 Oct - 2 Nov" would
    make visible and nobody would think to test for.
    """
    body = client.get("/v1/usage").json()
    assert body["period_start"] == usage.window_start().isoformat()
    assert body["reset_at"] == usage.window_end().isoformat()
    start = datetime.fromisoformat(body["period_start"])
    end = datetime.fromisoformat(body["reset_at"])
    assert end - start == timedelta(days=7)


def test_usage_endpoint_reports_credits_for_a_signed_in_person(
    client: TestClient, db_session
):
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    # 70k input tokens at $2.00/1M is 140_000 uusd -- two reference edits per
    # turn, so five turns spend half the weekly ceiling.
    _spend(db_session, user=user, turns=5, input_tokens=70_000)

    body = client.get("/v1/usage").json()
    assert body["tier"] == usage.REGISTERED
    assert body["limit_credits"] == float(tier.credits)
    assert body["turn_limit"] is None          # account tiers are capped on cost
    assert body["turns_used"] == 5
    assert 0 < body["used_credits"] < tier.credits
    assert body["remaining_credits"] == pytest.approx(
        tier.credits - body["used_credits"], abs=0.2
    )
    # Divided by *their* trailing cost per turn, not by the reference edit.
    # These turns cost two reference edits each, so 10 credits of headroom is
    # 5 of their edits -- and saying "10 edits left" would be a lie they would
    # catch on the sixth.
    assert body["used_credits"] == pytest.approx(10.0, abs=0.1)
    assert body["approx_edits_left"] == 5
    assert body["approx_edits_left"] < body["remaining_credits"]


def test_cheap_turns_buy_more_edits_than_the_reference_rate(
    client: TestClient, db_session
):
    """The other direction, which is the whole argument for metering on cost.

    Someone writing short prompts against a short resume really does get more
    out of the same allowance, and the number they are shown has to say so.
    """
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    # 7k input tokens is 14_000 uusd -- a fifth of a reference edit.
    _spend(db_session, user=user, turns=5, input_tokens=7_000)

    body = client.get("/v1/usage").json()
    assert body["used_credits"] == pytest.approx(1.0, abs=0.1)
    assert body["approx_edits_left"] > tier.credits
    assert body["approx_edits_left"] > body["remaining_credits"]


def test_unmeasured_rounds_are_reported_rather_than_hidden(
    client: TestClient, db_session
):
    user = _signed_in(client, db_session)
    _spend(db_session, user=user, turns=2, input_tokens=None)
    body = client.get("/v1/usage").json()
    assert body["unmeasured_rounds"] == 2
    assert body["used_credits"] > 0


# --------------------------------------------------------------------------
# the public tier table
# --------------------------------------------------------------------------


def test_published_tiers_equal_the_enforced_ones(client: TestClient):
    """The pin. A published allowance that has drifted from the enforced one is
    the worst bug the public page could have.

    The page fetches this endpoint rather than holding a copy, so drift is
    structurally impossible there -- but this still asserts the endpoint itself
    reports usage.TIERS rather than a second set of numbers.
    """
    body = client.get("/v1/usage/tiers").json()
    assert [row["name"] for row in body["tiers"]] == ["guest", "registered", "matching"]
    for row in body["tiers"]:
        tier = usage.TIERS[row["name"]]
        assert row["credits"] == tier.credits
        assert row["turns"] == tier.turns
        # Exactly one side of a tier is capped.
        assert (row["credits"] is None) != (row["turns"] is None)
    assert body["reset_at"] == usage.window_end().isoformat()


def test_only_an_account_can_publish_and_the_page_says_so(client: TestClient):
    """Already true before any of this: publish and share take require_user."""
    rows = {row["name"]: row for row in client.get("/v1/usage/tiers").json()["tiers"]}
    assert rows["guest"]["can_publish"] is False
    assert rows["registered"]["can_publish"] is True
    assert rows["matching"]["can_publish"] is True


def test_the_tier_table_does_not_vary_by_caller(client: TestClient, db_session):
    """A crawler, a signed-out visitor and a signed-in one see the same table.

    It is a price list, not a statement about anyone, so it must be cacheable
    and must not mint a guest session just by being read.
    """
    anonymous = client.get("/v1/usage/tiers").json()
    assert anonymous["tiers"]
    assert "offerfy_guest" not in client.cookies

    _signed_in(client, db_session, sub="sub-tiers")
    assert client.get("/v1/usage/tiers").json()["tiers"] == anonymous["tiers"]


# --------------------------------------------------------------------------
# refusal records (Task 14)
# --------------------------------------------------------------------------


def _refusals(db_session):
    from app.models import UsageRefusal

    return db_session.query(UsageRefusal).order_by(UsageRefusal.created_at).all()


def test_a_refusal_is_recorded_with_the_limit_that_caused_it(
    client: TestClient, db_session, enforced
):
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    _spend(db_session, user=user, turns=1, input_tokens=tier.credits * usage.CREDIT_UUSD)

    with pytest.raises(Exception):
        usage.enforce_usage(db_session, user, None)

    rows = _refusals(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert (row.subject_kind, row.subject_id) == ("user", user.id)
    assert row.limit_kind == "weekly_credits"
    assert row.tier == usage.REGISTERED
    assert row.purpose == "chat"
    # What they had spent when refused, so the row reads without joining back
    # to a counter that may since have moved or been pruned.
    assert row.used_uusd > 0


def test_the_guest_wall_and_the_burst_cap_are_distinguishable(
    client: TestClient, db_session, enforced
):
    """Each of the five limits implies a different fix, so which one fired has
    to be recorded rather than inferred."""
    guest = GuestSession(key_hash="r" * 64)
    db_session.add(guest)
    db_session.commit()
    _spend(db_session, guest=guest, turns=usage.TIERS[usage.GUEST].turns, input_tokens=10)
    with pytest.raises(Exception):
        usage.enforce_usage(db_session, None, guest)

    user = _signed_in(client, db_session, sub="sub-credits")
    tier = usage.TIERS[usage.REGISTERED]
    _spend(db_session, user=user, turns=1, input_tokens=tier.credits * usage.CREDIT_UUSD)
    with pytest.raises(Exception):
        usage.enforce_usage(db_session, user, None)

    kinds = {(r.subject_kind, r.limit_kind) for r in _refusals(db_session)}
    assert kinds == {("guest", "weekly_turns"), ("user", "weekly_credits")}


def test_a_refusal_row_survives_the_rejected_request(
    client: TestClient, db_session, monkeypatch, openai_enabled, enforced
):
    """The sharp reason this gets its own session.

    Raising an HTTPException makes get_db roll the request session back, so a
    row written on it would be discarded exactly when we most want it.
    """
    guest_resume = client.post("/v1/resumes", json={}).json()
    guest = db_session.query(GuestSession).one()
    _spend(db_session, guest=guest, turns=usage.TIERS[usage.GUEST].turns, input_tokens=10)

    result = post_chat(client, guest_resume["id"], json={"message": "hi"})
    assert result["status"] == 429
    assert result["json"]["detail"]["limit"] == "weekly_turns"
    # Rolled back with the request if it had been written on the same session.
    assert len(_refusals(db_session)) == 1


def test_broken_refusal_telemetry_still_refuses(
    client: TestClient, db_session, monkeypatch, enforced
):
    """A lost data point is acceptable; a 500 instead of a 429 is not."""
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    _spend(db_session, user=user, turns=1, input_tokens=tier.credits * usage.CREDIT_UUSD)
    monkeypatch.setattr(
        "app.services.usage.get_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, user, None)
    assert caught.value.status_code == 429
    assert _refusals(db_session) == []


def test_refusals_by_groups_for_tuning(client: TestClient, db_session, enforced):
    user = _signed_in(client, db_session)
    tier = usage.TIERS[usage.REGISTERED]
    _spend(db_session, user=user, turns=1, input_tokens=tier.credits * usage.CREDIT_UUSD)
    for _ in range(3):
        with pytest.raises(Exception):
            usage.enforce_usage(db_session, user, None)
    assert usage.refusals_by(db_session, since=usage.window_start()) == [
        (usage.REGISTERED, "weekly_credits", 3)
    ]


def test_prune_respects_the_retention_the_policy_states(
    client: TestClient, db_session, enforced
):
    """90 days, as the privacy policy says. Nothing schedules this yet."""
    from app.models import UsageRefusal

    old = datetime.now(timezone.utc) - usage.REFUSAL_RETENTION - timedelta(days=1)
    db_session.add_all(
        [
            UsageRefusal(
                subject_kind="user", subject_id="u", tier="registered",
                limit_kind="burst", purpose="chat", created_at=old,
            ),
            UsageRefusal(
                subject_kind="user", subject_id="u", tier="registered",
                limit_kind="burst", purpose="chat",
            ),
        ]
    )
    db_session.commit()
    assert usage.prune_refusals(db_session) == 1
    assert len(_refusals(db_session)) == 1


def test_published_edits_come_from_the_measured_cost_not_the_credit(
    client: TestClient,
):
    """The two constants mean different things and must not be conflated.

    CREDIT_UUSD is what someone was promised; EDIT_UUSD is what a turn
    measurably bills. They were equal only by accident of the original peg, and
    once the history trim cut a turn to a third of its old cost, publishing
    `credits` as the edit count understated by ~3x -- the page said "about 20"
    where the budget bought about 55.

    Quoted against the *reference* model via `edit_uusd()`, not the raw
    EDIT_UUSD: that constant belongs to the model it was measured on, which is
    a different and dearer one. See usage.EDIT_MEASURED_ON.
    """
    rows = {r["name"]: r for r in client.get("/v1/usage/tiers").json()["tiers"]}

    # A guest is capped on a count, so the count is the answer exactly.
    assert rows["guest"]["approx_edits"] == usage.TIERS[usage.GUEST].turns

    for name in ("registered", "matching"):
        tier = usage.TIERS[name]
        exact = tier.credits * usage.CREDIT_UUSD / usage.edit_uusd()
        published = rows[name]["approx_edits"]
        # Rounded to the nearest 5 and never zero.
        assert published % 5 == 0 and published >= 5
        assert abs(published - exact) <= 2.5
        # The bug this guards: it must not simply echo the credit count.
        assert published > tier.credits, (
            f"{name}: published {published} edits for {tier.credits} credits -- "
            "approx_edits has fallen back to echoing credits"
        )


def test_a_guest_gets_eight_edits_and_can_spend_them_in_one_sitting(
    client: TestClient, db_session, enforced
):
    """Doubled from 4 on 2026-10-25, with no hourly cap to bind first."""
    guest = GuestSession(key_hash="w" * 64)
    db_session.add(guest)
    db_session.commit()
    tier = usage.TIERS[usage.GUEST]
    assert tier.turns == 8

    _spend(db_session, guest=guest, turns=tier.turns, input_tokens=10)
    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, None, guest)
    # The weekly count is what refuses, not the burst guard.
    assert caught.value.detail["limit"] == "weekly_turns"


def test_the_edits_left_estimate_falls_back_to_a_measured_turn(
    client: TestClient, db_session
):
    """Before someone has history we must guess what *their* turn costs, and
    the measured cost of a turn is the guess. Using the credit understated by
    ~3x once the trim landed."""
    user = _signed_in(client, db_session, sub="sub-fallback")
    body = client.get("/v1/usage").json()
    tier = usage.TIERS[usage.REGISTERED]
    expected = tier.credits * usage.CREDIT_UUSD // usage.edit_uusd()
    assert body["approx_edits_left"] == expected > tier.credits
    assert user is not None
