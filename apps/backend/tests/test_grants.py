"""Per-person quota grants.

Four groups: the additive arithmetic, the window rule (the one thing here that
is easy to get subtly wrong), revocation, and the surfaces -- /v1/usage and the
admin endpoints.

The window rule is the point of most of this file. A ceiling must never move
*down* inside a window, because used_uusd for that window cannot move back, so
a grant lapsing on Wednesday would turn Monday's legitimate spend into debt.
Revocation is the deliberate exception. Both directions are tested, because
either one flipping silently is a billing bug nobody would notice.

Foreign keys are off in the test session (see conftest), so the ON DELETE
CASCADE onto users is not exercised here -- migration 021 carries it and it
needs a Postgres check.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import QuotaGrant, User
from app.services import usage
from app.services.consent import record_consent
from app.services.grants import (
    active_grants,
    all_grants,
    granted_credits,
    issue_grant,
    revoke_grant,
)
from tests.test_usage import _spend


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _user(db_session, sub="sub-grant"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def _admin(client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    admin = User(google_sub="sub-ops-grants", email="ops@example.com", locale="en")
    db_session.add(admin)
    db_session.commit()
    client.cookies.set(
        SESSION_COOKIE, _sign(admin.id, get_settings().auth_token_secret)
    )
    return admin


def _grant(db_session, user, credits=50, **kw):
    grant = issue_grant(
        db_session,
        user=user,
        credits=credits,
        reason=kw.pop("reason", "manual"),
        issued_by=kw.pop("issued_by", None),
        **kw,
    )
    db_session.commit()
    return grant


# A Wednesday, so there is room either side of `now` inside one window.
MIDWEEK = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# the arithmetic
# --------------------------------------------------------------------------


def test_no_grant_leaves_the_tier_ceiling_alone(client, db_session):
    user = _user(db_session)
    bal = usage.balance(db_session, user, None)
    assert bal.granted_credits == 0
    assert bal.tier_credits == 20
    assert bal.limit_credits == 20


def test_a_grant_raises_the_ceiling_additively(client, db_session):
    user = _user(db_session)
    _grant(db_session, user, credits=50)
    bal = usage.balance(db_session, user, None)
    # The split is reported, not just the total: a meter has to be able to say
    # "20 + 50" rather than an unexplained 70.
    assert bal.tier_credits == 20
    assert bal.granted_credits == 50
    assert bal.limit_credits == 70
    assert bal.remaining_uusd == 70 * usage.CREDIT_UUSD


def test_grants_stack(client, db_session):
    user = _user(db_session)
    _grant(db_session, user, credits=5, reason="compensation")
    _grant(db_session, user, credits=15, reason="beta")
    assert granted_credits(db_session, user=user) == 20
    assert usage.balance(db_session, user, None).limit_credits == 40


def test_a_grant_adds_to_the_matching_tier_too(client, db_session):
    user = _user(db_session)
    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action="granted",
        locale="en",
        source="test",
    )
    db_session.commit()
    _grant(db_session, user, credits=25)
    bal = usage.balance(db_session, user, None)
    assert bal.tier == "matching"
    assert bal.tier_credits == 100
    assert bal.limit_credits == 125


def test_a_guest_has_no_grants_and_no_credit_ceiling(client, db_session):
    # Grants are user-only on purpose: a guest can mint a fresh cookie, which
    # is the farming vector the 'network' subject kind exists to stop. The
    # guest tier is also the only one with credits=None, so a grant there would
    # be arithmetically a no-op anyway -- the two rules agree by construction.
    assert granted_credits(db_session, user=None) == 0
    assert active_grants(db_session, user=None) == []
    bal = usage.balance(db_session, None, None)
    assert bal.tier == "guest"
    assert bal.tier_credits is None
    assert bal.granted_credits == 0
    assert bal.limit_uusd is None


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------


def test_a_grant_expiring_midweek_still_covers_that_whole_window(client, db_session):
    """The core rule. A ceiling must not move down inside a window."""
    user = _user(db_session)
    start = usage.window_start(MIDWEEK)
    # Lapsed yesterday, but inside the window we are reading.
    _grant(
        db_session,
        user,
        credits=50,
        starts_at=start,
        expires_at=start + timedelta(days=1),
    )
    assert granted_credits(db_session, user=user, now=MIDWEEK) == 50
    # ... and is gone by the next window, with no job running at the boundary.
    assert granted_credits(db_session, user=user, now=MIDWEEK + timedelta(days=7)) == 0


def test_spend_under_a_lapsing_grant_does_not_become_debt(client, db_session):
    user = _user(db_session)
    start = usage.window_start(MIDWEEK)
    _grant(
        db_session,
        user,
        credits=50,
        starts_at=start,
        expires_at=start + timedelta(days=1),
    )
    # 40 credits of spend: legitimate under 70, impossible under 20.
    _spend(
        db_session,
        user=user,
        turns=1,
        input_tokens=40 * usage.CREDIT_UUSD * 1_000_000 // 1_250_000,
        when=start + timedelta(hours=1),
    )
    bal = usage.balance(db_session, user, None, now=MIDWEEK)
    assert bal.limit_credits == 70
    assert bal.remaining_uusd is not None and bal.remaining_uusd > 0
    assert bal.exhausted is False


def test_a_grant_expired_before_this_window_does_not_count(client, db_session):
    user = _user(db_session)
    start = usage.window_start(MIDWEEK)
    _grant(
        db_session,
        user,
        credits=50,
        starts_at=start - timedelta(days=7),
        expires_at=start - timedelta(seconds=1),
    )
    assert granted_credits(db_session, user=user, now=MIDWEEK) == 0


def test_a_grant_issued_midweek_covers_the_whole_window(client, db_session):
    user = _user(db_session)
    start = usage.window_start(MIDWEEK)
    _grant(db_session, user, credits=50, starts_at=start + timedelta(days=3))
    assert granted_credits(db_session, user=user, now=MIDWEEK) == 50


def test_a_grant_starting_next_window_does_not_count_yet(client, db_session):
    user = _user(db_session)
    start = usage.window_start(MIDWEEK)
    _grant(db_session, user, credits=50, starts_at=start + timedelta(days=7))
    assert granted_credits(db_session, user=user, now=MIDWEEK) == 0
    assert granted_credits(db_session, user=user, now=MIDWEEK + timedelta(days=7)) == 50


def test_an_open_ended_grant_keeps_counting(client, db_session):
    user = _user(db_session)
    _grant(db_session, user, credits=50, reason="staff")
    for weeks in (0, 1, 52):
        when = MIDWEEK + timedelta(days=7 * weeks)
        assert granted_credits(db_session, user=user, now=when) == 50


# --------------------------------------------------------------------------
# revocation
# --------------------------------------------------------------------------


def test_revoking_bites_immediately_not_at_the_boundary(client, db_session):
    user = _user(db_session)
    grant = _grant(db_session, user, credits=50)
    assert granted_credits(db_session, user=user, now=MIDWEEK) == 50
    revoke_grant(db_session, grant=grant, now=MIDWEEK)
    db_session.commit()
    # The one place a ceiling is allowed to move down mid-window: this is the
    # lever for abuse, and an admin stopping someone cannot wait for Monday.
    assert granted_credits(db_session, user=user, now=MIDWEEK) == 0


def test_revoking_is_idempotent_and_keeps_the_first_timestamp(client, db_session):
    user = _user(db_session)
    grant = _grant(db_session, user, credits=50)
    revoke_grant(db_session, grant=grant, now=MIDWEEK)
    db_session.commit()
    first = grant.revoked_at
    assert first is not None
    revoke_grant(db_session, grant=grant, now=MIDWEEK + timedelta(days=1))
    db_session.commit()
    # Re-revoking must not rewrite when the ceiling dropped.
    assert grant.revoked_at == first


def test_a_revoked_grant_stays_in_the_history(client, db_session):
    user = _user(db_session)
    grant = _grant(db_session, user, credits=50)
    revoke_grant(db_session, grant=grant)
    db_session.commit()
    # Revoked, never deleted: "why did their ceiling drop on Tuesday" has to
    # stay answerable.
    assert [row.id for row in all_grants(db_session, user=user)] == [grant.id]
    assert active_grants(db_session, user=user) == []


def test_issue_grant_rejects_nonsense(client, db_session):
    user = _user(db_session)
    with pytest.raises(ValueError):
        issue_grant(db_session, user=user, credits=0, reason="manual", issued_by=None)
    with pytest.raises(ValueError):
        issue_grant(db_session, user=user, credits=5, reason="because", issued_by=None)
    with pytest.raises(ValueError):
        issue_grant(
            db_session,
            user=user,
            credits=5,
            reason="manual",
            issued_by=None,
            starts_at=MIDWEEK,
            expires_at=MIDWEEK,
        )


# --------------------------------------------------------------------------
# enforcement and the surfaces
# --------------------------------------------------------------------------


def test_a_grant_moves_the_wall(client, db_session, enforced):
    user = _user(db_session)
    # 25 credits of spend: over the registered tier's 20, under 20 + 50.
    _spend(
        db_session,
        user=user,
        turns=1,
        input_tokens=25 * usage.CREDIT_UUSD * 1_000_000 // 1_250_000,
    )
    with pytest.raises(Exception) as refused:
        usage.enforce_usage(db_session, user, None, purpose="chat")
    assert refused.value.status_code == 429

    _grant(db_session, user, credits=50)
    bal = usage.enforce_usage(db_session, user, None, purpose="chat")
    assert bal.limit_credits == 70


def test_the_refusal_names_the_granted_total_not_the_tier(client, db_session, enforced):
    """Regression: the refusal detail read tier.credits, so a granted person
    was told the wrong number at the exact moment they hit the wall.
    """
    user = _user(db_session)
    _grant(db_session, user, credits=50)
    _spend(
        db_session,
        user=user,
        turns=1,
        input_tokens=75 * usage.CREDIT_UUSD * 1_000_000 // 1_250_000,
    )
    with pytest.raises(Exception) as refused:
        usage.enforce_usage(db_session, user, None, purpose="chat")
    body = refused.value.detail
    assert body["limit"] == "weekly_credits"
    # Formatted as an integer, the way the tier number used to read.
    assert "70 credits" in body["detail"]
    assert body["limit_credits"] == 70


def test_usage_endpoint_reports_the_split(client: TestClient, db_session):
    user = _user(db_session)
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    _grant(db_session, user, credits=50)
    body = client.get("/v1/usage").json()
    assert body["tier_credits"] == 20
    assert body["granted_credits"] == 50
    assert body["limit_credits"] == 70


def test_published_tiers_know_nothing_about_grants(client: TestClient, db_session):
    user = _user(db_session)
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    _grant(db_session, user, credits=50)
    tiers = client.get("/v1/usage/tiers").json()["tiers"]
    registered = next(row for row in tiers if row["name"] == "registered")
    # /v1/usage/tiers is the *published* allowance and has no subject. A grant
    # leaking into it would make the public page personal.
    assert registered["credits"] == 20


# --------------------------------------------------------------------------
# admin
# --------------------------------------------------------------------------


def test_admin_issues_a_grant_and_sees_it_on_the_detail_page(
    client: TestClient, db_session, monkeypatch
):
    admin = _admin(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-grantee")
    created = client.post(
        f"/v1/admin/users/{user.id}/grants",
        json={"credits": 50, "reason": "compensation", "detail": "outage 2026-10-20"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["credits"] == 50
    assert body["active"] is True
    assert body["issued_by"] == admin.email

    detail = client.get(f"/v1/admin/users/{user.id}").json()
    assert detail["usage"]["granted_credits"] == 50
    assert detail["usage"]["limit_credits"] == 70
    assert [row["id"] for row in detail["grants"]] == [body["id"]]


def test_admin_revokes_a_grant(client: TestClient, db_session, monkeypatch):
    _admin(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-grantee")
    grant_id = client.post(
        f"/v1/admin/users/{user.id}/grants",
        json={"credits": 50, "reason": "manual"},
    ).json()["id"]

    revoked = client.delete(f"/v1/admin/users/{user.id}/grants/{grant_id}")
    assert revoked.status_code == 200
    assert revoked.json()["active"] is False
    assert revoked.json()["revoked_at"] is not None

    detail = client.get(f"/v1/admin/users/{user.id}").json()
    assert detail["usage"]["granted_credits"] == 0
    # Still listed, with active false -- the history is the point.
    assert detail["grants"][0]["active"] is False


def test_admin_grant_validation_and_scoping(
    client: TestClient, db_session, monkeypatch
):
    _admin(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-grantee")
    other = _user(db_session, sub="sub-other")

    assert (
        client.post(
            f"/v1/admin/users/{user.id}/grants",
            json={"credits": 0, "reason": "manual"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/admin/users/{user.id}/grants",
            json={"credits": 5, "reason": "because"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/admin/users/{user.id}/grants",
            json={"credits": 5, "reason": "manual", "expires_at": "not-a-date"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/admin/users/nope/grants", json={"credits": 5, "reason": "manual"}
        ).status_code
        == 404
    )

    # A grant id belonging to someone else is a 404 on this user's path, not a
    # successful revoke of a stranger's allowance.
    grant_id = client.post(
        f"/v1/admin/users/{other.id}/grants",
        json={"credits": 5, "reason": "manual"},
    ).json()["id"]
    assert (
        client.delete(f"/v1/admin/users/{user.id}/grants/{grant_id}").status_code == 404
    )


def test_grants_need_an_admin(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "")
    get_settings.cache_clear()
    user = _user(db_session)
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    denied = client.post(
        f"/v1/admin/users/{user.id}/grants", json={"credits": 500, "reason": "manual"}
    )
    assert denied.status_code == 404
    assert db_session.query(QuotaGrant).count() == 0
