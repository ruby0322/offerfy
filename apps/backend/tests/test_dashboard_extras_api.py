"""The routes the redesigned dashboard added: disclosures, usage breakdown."""

from datetime import timedelta

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import DisclosureEvent, LlmCall, User
from app.services.usage import window_start


def _signed_in(client, db_session, sub="sub-extras"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _disclose(db_session, user, label, contact, when, n=1):
    for i in range(n):
        db_session.add(
            DisclosureEvent(
                user_id=user.id,
                recipient_label=label,
                recipient_contact=contact,
                purpose="talent_introduction",
                scope="full",
                profile_content_hash="h" * 64,
                policy_version="2026-10-24",
                route="GET /x",
                created_at=when + timedelta(minutes=i),
            )
        )
    db_session.commit()


# --- disclosures ----------------------------------------------------------


def test_disclosures_needs_a_user(client):
    assert client.get("/v1/me/disclosures").status_code == 401


def test_disclosures_are_empty_before_anyone_has_looked(client, db_session):
    _signed_in(client, db_session, "sub-no-disclosures")
    res = client.get("/v1/me/disclosures")
    assert res.status_code == 200 and res.json() == []


def test_disclosures_group_by_recipient_newest_contact_first(client, db_session):
    user = _signed_in(client, db_session, "sub-disclosures")
    start = window_start()
    _disclose(db_session, user, "Appier", "talent@appier.example", start, n=3)
    _disclose(
        db_session, user, "Gogolook", "hr@gogolook.example", start + timedelta(days=2)
    )
    rows = client.get("/v1/me/disclosures").json()
    assert [r["recipient_label"] for r in rows] == ["Gogolook", "Appier"]
    # Three events from one employer are one row with a count, not three rows:
    # the question is who has seen you, not how many times a key was used.
    appier = rows[1]
    assert appier["disclosure_count"] == 3
    assert appier["first_disclosed_at"] <= appier["last_disclosed_at"]


def test_another_persons_disclosures_are_invisible(client, db_session):
    user = _signed_in(client, db_session, "sub-mine")
    other = User(google_sub="sub-theirs", email="theirs@example.com", locale="en")
    db_session.add(other)
    db_session.commit()
    _disclose(db_session, other, "Acme", "a@acme.example", window_start())
    assert client.get("/v1/me/disclosures").json() == []
    assert user.id != other.id


# --- usage breakdown ------------------------------------------------------


def _call(db_session, user, purpose, *, when=None, model="gpt-5.6-terra"):
    db_session.add(
        LlmCall(
            user_id=user.id,
            purpose=purpose,
            model=model,
            round_index=0,
            created_at=when or (window_start() + timedelta(hours=1)),
        )
    )
    db_session.commit()


def test_breakdown_is_empty_for_a_window_with_no_calls(client, db_session):
    _signed_in(client, db_session, "sub-breakdown-empty")
    body = client.get("/v1/usage/breakdown").json()
    assert body["slices"] == [] and body["daily"] == []
    assert body["total_credits"] == 0


def test_breakdown_splits_by_purpose_and_sums_to_the_total(client, db_session):
    user = _signed_in(client, db_session, "sub-breakdown")
    _call(db_session, user, "chat")
    _call(db_session, user, "chat")
    _call(db_session, user, "import")
    body = client.get("/v1/usage/breakdown").json()
    purposes = {s["purpose"]: s for s in body["slices"]}
    assert purposes["chat"]["rounds"] == 2
    assert purposes["import"]["rounds"] == 1
    # The parts have to add up to the meter, or the page explains a number
    # that disagrees with the one above it.
    assert round(sum(s["credits"] for s in body["slices"]), 2) == body["total_credits"]


def test_breakdown_lists_purposes_in_a_fixed_order(client, db_session):
    # The person's own work first, the background jobs after, so the panel
    # does not reshuffle between visits.
    user = _signed_in(client, db_session, "sub-breakdown-order")
    for purpose in ("repair", "digest", "chat", "import"):
        _call(db_session, user, purpose)
    order = [s["purpose"] for s in client.get("/v1/usage/breakdown").json()["slices"]]
    assert order == ["chat", "import", "digest", "repair"]


def test_breakdown_ignores_calls_from_before_the_window(client, db_session):
    user = _signed_in(client, db_session, "sub-breakdown-window")
    _call(db_session, user, "chat", when=window_start() - timedelta(days=1))
    body = client.get("/v1/usage/breakdown").json()
    assert body["slices"] == []


def test_breakdown_does_not_leak_another_persons_spend(client, db_session):
    # _apply_subject returns None for no subject, which must read as no spend
    # rather than as everyone's spend.
    _signed_in(client, db_session, "sub-breakdown-mine")
    other = User(google_sub="sub-breakdown-theirs", email="t@example.com", locale="en")
    db_session.add(other)
    db_session.commit()
    _call(db_session, other, "chat")
    assert client.get("/v1/usage/breakdown").json()["slices"] == []
