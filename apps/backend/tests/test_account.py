"""The account profile: normalisation, refusals, and what it does not touch."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import User
from app.services.account import (
    AccountError,
    RESERVED_USERNAMES,
    display_name_of,
    normalize_bio,
    normalize_username,
    normalize_website,
)


def _signed_in(client, db_session, sub="sub-account", **columns):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en", **columns)
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


# --------------------------------------------------------------------------
# Normalisation, as pure functions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ruby", "ruby"),
        # Folded rather than refused: someone typing Ruby means ruby, and
        # storing both spellings would let two accounts hold one handle.
        ("Ruby_0322", "ruby_0322"),
        ("  ruby  ", "ruby"),
        ("", None),
        ("   ", None),
    ],
)
def test_username_normalises(raw, expected):
    assert normalize_username(raw) == expected


@pytest.mark.parametrize(
    "raw,code",
    [
        ("ab", "length"),
        ("a" * 31, "length"),
        # Underscores inside only. A leading or trailing one reads as padding.
        ("_ruby", "charset"),
        ("ruby_", "charset"),
        # No dots and no hyphens -- see the comment on _USERNAME_RE.
        ("ru.by", "charset"),
        ("ru-by", "charset"),
        ("Ruby 0322", "charset"),
        ("admin", "reserved"),
        ("settings", "reserved"),
    ],
)
def test_username_refusals(raw, code):
    with pytest.raises(AccountError) as err:
        normalize_username(raw)
    assert err.value.code == code
    assert err.value.field == "username"


def test_reserved_list_covers_the_account_routes():
    """A handle must not be able to shadow a page we already serve.

    These are the paths under /[locale]/ that a username could one day be
    mistaken for. The check is here rather than in a comment because the
    reserved list is easy to edit and hard to remember to edit.
    """
    for path in ("settings", "plan", "usage", "dashboard", "admin", "login", "pricing"):
        assert path in RESERVED_USERNAMES


@pytest.mark.parametrize(
    "raw,expected",
    [
        # People type a bare domain, so assuming https beats refusing.
        ("example.com", "https://example.com"),
        ("https://ruby.im/cv", "https://ruby.im/cv"),
        ("http://ruby.im", "http://ruby.im"),
        ("  ruby.im  ", "https://ruby.im"),
        ("", None),
    ],
)
def test_website_normalises(raw, expected):
    assert normalize_website(raw) == expected


@pytest.mark.parametrize(
    "raw,code",
    [
        # The reason this field is validated at all: the value is rendered as
        # a link. Refused as a scheme, not as a malformed host -- otherwise the
        # code we hand the client names the wrong problem.
        ("javascript:alert(1)", "scheme"),
        ("data:text/html,<script>", "scheme"),
        ("ftp://ruby.im", "scheme"),
        ("mailto:me@ruby.im", "scheme"),
        ("localhost", "host"),
        ("https://" + "a" * 260 + ".com", "length"),
    ],
)
def test_website_refusals(raw, code):
    with pytest.raises(AccountError) as err:
        normalize_website(raw)
    assert err.value.code == code


def test_bio_keeps_paragraphs_but_collapses_blank_runs():
    assert normalize_bio("one\r\n\r\n\r\n\r\ntwo   \n") == "one\n\ntwo"
    assert normalize_bio("  \n \n ") is None


def test_display_name_precedence():
    """Their own edit, then Google's, then nothing. Never the email."""
    assert display_name_of(User(display_name="Ruby", name="Ruby Chen")) == "Ruby"
    assert display_name_of(User(name="Ruby Chen")) == "Ruby Chen"
    assert display_name_of(User(display_name="  ", name="Ruby Chen")) == "Ruby Chen"
    assert display_name_of(User(email="ruby@example.com")) is None


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


def test_account_requires_sign_in(client: TestClient):
    assert client.get("/v1/me/account").status_code == 401
    assert client.patch("/v1/me/account", json={"username": "ruby"}).status_code == 401


def test_get_account_starts_empty_but_carries_the_google_fields(
    client: TestClient, db_session
):
    _signed_in(client, db_session, name="Ruby Chen", picture="https://p/x.png")
    body = client.get("/v1/me/account").json()
    assert body["email"] == "sub-account@example.com"
    assert body["google_name"] == "Ruby Chen"
    assert body["picture"] == "https://p/x.png"
    assert body["username"] is None
    assert body["display_name"] is None


def test_patch_saves_and_normalises(client: TestClient, db_session):
    user = _signed_in(client, db_session)
    res = client.patch(
        "/v1/me/account",
        json={
            "username": "  Ruby_0322 ",
            "display_name": "  Ruby   Chen ",
            "headline": "Backend engineer",
            "location": "Taipei",
            "website": "ruby.im",
            "bio": "I build things.\n\n\n\nMostly backends.",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "ruby_0322"
    assert body["display_name"] == "Ruby Chen"
    assert body["website"] == "https://ruby.im"
    assert body["bio"] == "I build things.\n\nMostly backends."
    db_session.refresh(user)
    assert user.username == "ruby_0322"
    assert user.headline == "Backend engineer"


def test_patch_is_partial(client: TestClient, db_session):
    """Omitting a key leaves it; sending it empty clears it.

    The distinction is the whole reason this is a PATCH -- a form that posts
    one field must not blank the other five.
    """
    user = _signed_in(client, db_session)
    client.patch("/v1/me/account", json={"username": "ruby", "headline": "Engineer"})
    client.patch("/v1/me/account", json={"location": "Taipei"})
    db_session.refresh(user)
    assert (user.username, user.headline, user.location) == ("ruby", "Engineer", "Taipei")

    body = client.patch("/v1/me/account", json={"headline": ""}).json()
    assert body["headline"] is None
    assert body["username"] == "ruby"
    body = client.patch("/v1/me/account", json={"location": None}).json()
    assert body["location"] is None


def test_patch_refuses_a_taken_username(client: TestClient, db_session):
    db_session.add(User(google_sub="other", email="o@example.com", username="ruby"))
    db_session.commit()
    _signed_in(client, db_session)
    res = client.patch("/v1/me/account", json={"username": "RUBY"})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert (detail["field"], detail["code"]) == ("username", "taken")


def test_keeping_your_own_username_is_not_a_collision(client: TestClient, db_session):
    user = _signed_in(client, db_session)
    client.patch("/v1/me/account", json={"username": "ruby"})
    res = client.patch("/v1/me/account", json={"username": "ruby", "headline": "Eng"})
    assert res.status_code == 200
    db_session.refresh(user)
    assert user.headline == "Eng"


def test_a_refusal_writes_nothing(client: TestClient, db_session):
    """Not even the fields that were valid.

    services.account.apply normalises every field before assigning any, so a
    good display_name does not survive a bad website in the same request.
    """
    user = _signed_in(client, db_session)
    res = client.patch(
        "/v1/me/account",
        json={"display_name": "Ruby", "website": "javascript:alert(1)"},
    )
    assert res.status_code == 422
    assert res.json()["detail"]["field"] == "website"
    db_session.refresh(user)
    assert user.display_name is None
    assert user.website is None


def test_unknown_field_is_ignored_not_stored(client: TestClient, db_session):
    """Pydantic drops it, so `exclude_unset` never sees it.

    Worth pinning: the router trusts the schema to be the allow-list, and
    `apply` would otherwise setattr whatever arrived onto the User row.
    """
    user = _signed_in(client, db_session)
    res = client.patch("/v1/me/account", json={"is_admin": True, "email": "x@y.z"})
    assert res.status_code == 200
    db_session.refresh(user)
    assert user.email == "sub-account@example.com"


def test_auth_me_carries_the_handle_and_the_name(client: TestClient, db_session):
    """The nav reads /v1/auth/me on every page and must not need a second call."""
    _signed_in(client, db_session, name="Ruby Chen")
    client.patch("/v1/me/account", json={"username": "ruby", "display_name": "Ruby!"})
    body = client.get("/v1/auth/me").json()["user"]
    assert body["username"] == "ruby"
    assert body["display_name"] == "Ruby!"


# --------------------------------------------------------------------------
# Agent keys: the cookie-authenticated management routes
# --------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.models import AgentApiKey, AgentToolCall  # noqa: E402
from app.services.agent_keys import key_for_token, mint_agent_key  # noqa: E402

KEYS = "/v1/me/agent-keys"


def _bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


def test_no_agent_key_route_is_in_the_published_schema(client: TestClient):
    """Global Constraint: every new route carries include_in_schema=False,
    whether it sits on a new router or on an existing one. The Next rewrite
    publishes /api/openapi.json. The sibling account route is listed, so this
    cannot pass by the document being empty."""
    paths = client.app.openapi()["paths"]
    assert not [p for p in paths if "agent-keys" in p]
    assert "/v1/me/account" in paths


def test_agent_key_routes_require_a_cookie(client: TestClient):
    assert client.get(KEYS).status_code == 401
    assert client.post(KEYS, json={"label": "k"}).status_code == 401
    assert client.delete(f"{KEYS}/x").status_code == 401
    assert client.get(f"{KEYS}/x/calls").status_code == 401


def test_minting_returns_the_token_once(client, signed_in):
    made = client.post(KEYS, json={"label": "laptop"}).json()
    assert made["token"].startswith("ofa_")
    assert made["label"] == "laptop"
    listed = client.get(KEYS).json()
    assert len(listed) == 1
    assert listed[0]["id"] == made["id"]
    assert "token" not in listed[0]
    assert "key_hash" not in listed[0]
    # No other field may smuggle the secret back either.
    assert made["token"] not in str(listed)


def test_an_agent_token_cannot_mint_another(client, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    assert (
        client.post(KEYS, json={"label": "second"}, headers=_bearer(raw)).status_code
        == 401
    )
    # Nor read: the whole surface is cookie-only.
    assert client.get(KEYS, headers=_bearer(raw)).status_code == 401
    assert db_session.query(AgentApiKey).count() == 1


def test_i_cannot_revoke_someone_elses_key(client, db_session, signed_in, other_user):
    theirs, _ = mint_agent_key(db_session, user=other_user, label="theirs")
    db_session.commit()
    assert client.delete(f"{KEYS}/{theirs.id}").status_code == 404
    db_session.refresh(theirs)
    assert theirs.revoked_at is None


def test_revoking_my_key_kills_it_and_frees_a_slot(client, db_session, signed_in):
    made = [client.post(KEYS, json={"label": f"k{n}"}).json() for n in range(5)]
    assert client.delete(f"{KEYS}/{made[0]['id']}").status_code == 204
    listed = client.get(KEYS).json()
    # Still listed, and marked: the log of a key you have just revoked is the
    # log you most want to read. It no longer counts against the cap, though.
    revoked = [row for row in listed if row["revoked_at"] is not None]
    assert [row["id"] for row in revoked] == [made[0]["id"]]
    assert len([row for row in listed if row["revoked_at"] is None]) == 4
    # The revoked token no longer authenticates, and a slot is free again.
    assert key_for_token(db_session, made[0]["token"]) is None
    assert client.post(KEYS, json={"label": "again"}).status_code == 200
    # Revoking twice answers like revoking something that is not there.
    assert client.delete(f"{KEYS}/{made[0]['id']}").status_code == 404


def test_the_sixth_key_is_a_400(client, signed_in):
    for n in range(5):
        assert client.post(KEYS, json={"label": f"k{n}"}).status_code == 200
    assert client.post(KEYS, json={"label": "sixth"}).status_code == 400
    assert len(client.get(KEYS).json()) == 5


def test_a_blank_or_overlong_label_is_a_400(client, signed_in):
    for bad in ("", "   ", "x" * 256):
        assert client.post(KEYS, json={"label": bad}).status_code == 400
    assert client.get(KEYS).json() == []
    # The boundary itself is fine.
    assert client.post(KEYS, json={"label": "x" * 255}).status_code == 200


def test_a_non_positive_expiry_is_a_400(client, signed_in):
    for bad in (0, -1):
        resp = client.post(KEYS, json={"label": "k", "expires_in_days": bad})
        assert resp.status_code == 400
    assert client.get(KEYS).json() == []


def test_the_call_log_is_mine_only(client, db_session, signed_in, other_user):
    theirs, _ = mint_agent_key(db_session, user=other_user, label="theirs")
    db_session.commit()
    assert client.get(f"{KEYS}/{theirs.id}/calls").status_code == 404


def test_the_call_log_is_newest_first_and_carries_no_arguments(
    client, db_session, signed_in, a_user, other_user
):
    mine, _ = mint_agent_key(db_session, user=a_user, label="mine")
    theirs, _ = mint_agent_key(db_session, user=other_user, label="theirs")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for n, tool in enumerate(["list_resumes", "publish_resume"]):
        db_session.add(
            AgentToolCall(
                key_id=mine.id,
                user_id=a_user.id,
                tool=tool,
                resume_id="r-1" if n else None,
                outcome="ok",
                created_at=t0 + timedelta(minutes=n),
            )
        )
    db_session.add(
        AgentToolCall(
            key_id=theirs.id, user_id=other_user.id, tool="their_tool", outcome="ok"
        )
    )
    db_session.commit()
    rows = client.get(f"{KEYS}/{mine.id}/calls").json()
    assert [r["tool"] for r in rows] == ["publish_resume", "list_resumes"]
    assert rows[0]["resume_id"] == "r-1" and rows[1]["resume_id"] is None
    assert set(rows[0]) == {"id", "tool", "resume_id", "outcome", "created_at"}


def test_the_call_log_is_capped(client, db_session, signed_in, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="busy")
    for _n in range(75):
        db_session.add(
            AgentToolCall(key_id=key.id, user_id=a_user.id, tool="t", outcome="ok")
        )
    db_session.commit()
    assert len(client.get(f"{KEYS}/{key.id}/calls").json()) == 50


def test_a_revoked_keys_log_is_still_readable(client, db_session, signed_in, a_user):
    """The anti-goal is "the user does not know what the agent did", and
    revoking is what they do when they suspect it did something. Hiding the key
    hid the answer."""
    made = client.post(KEYS, json={"label": "suspect"}).json()
    db_session.add(
        AgentToolCall(
            key_id=made["id"], user_id=a_user.id, tool="publish_resume",
            resume_id="r-1", outcome="ok",
        )
    )
    db_session.commit()
    assert client.delete(f"{KEYS}/{made['id']}").status_code == 204
    rows = client.get(f"{KEYS}/{made['id']}/calls").json()
    assert [r["tool"] for r in rows] == ["publish_resume"]


def test_the_list_bounds_how_many_retired_keys_it_shows(
    client, db_session, signed_in, a_user
):
    from app.routers.account import RETIRED_KEYS_LISTED

    for n in range(RETIRED_KEYS_LISTED + 4):
        key, _ = mint_agent_key(db_session, user=a_user, label=f"old-{n}")
        key.revoked_at = datetime.now(timezone.utc) - timedelta(minutes=n)
        db_session.commit()
    live, _ = mint_agent_key(db_session, user=a_user, label="current")
    db_session.commit()
    listed = client.get(KEYS).json()
    assert len(listed) == RETIRED_KEYS_LISTED + 1
    assert listed[0]["id"] == live.id  # live first, then newest-retired first
    assert listed[1]["label"] == "old-0"


def test_ordering_retired_keys_survives_a_mixed_timezone_pair():
    """One stamp read back from SQLite (naive) beside one assigned in this
    session (aware) is what `max()` refuses to compare."""
    from app.routers.account import _retired_at

    key = AgentApiKey(
        user_id="u",
        key_hash="h",
        label="l",
        revoked_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        expires_at=datetime(2026, 1, 1),
    )
    assert _retired_at(key) == datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_an_expired_key_is_listed_and_marked_but_does_not_authenticate(
    client, db_session, signed_in, a_user
):
    key, raw = mint_agent_key(db_session, user=a_user, label="lapsed")
    key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    listed = client.get(KEYS).json()
    assert [(r["id"], r["revoked_at"]) for r in listed] == [(key.id, None)]
    assert listed[0]["expires_at"] is not None
    assert key_for_token(db_session, raw) is None


def test_a_publish_stays_in_the_log_behind_a_wall_of_reads(
    client, db_session, signed_in, a_user
):
    """At the quota's 300 calls an hour one flat limit of 50 scrolled a publish
    out of view in minutes -- the one row this table exists for."""
    from app.routers.account import CALL_LOG_LIMIT

    key, _ = mint_agent_key(db_session, user=a_user, label="busy")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add(
        AgentToolCall(
            key_id=key.id, user_id=a_user.id, tool="publish_resume",
            resume_id="r-1", outcome="ok", created_at=t0,
        )
    )
    for n in range(CALL_LOG_LIMIT * 2):
        db_session.add(
            AgentToolCall(
                key_id=key.id, user_id=a_user.id, tool="search_jobs", outcome="ok",
                created_at=t0 + timedelta(minutes=n + 1),
            )
        )
    db_session.commit()
    rows = client.get(f"{KEYS}/{key.id}/calls").json()
    assert "publish_resume" in [r["tool"] for r in rows]
    # Still newest first, and still only one row per call.
    stamps = [r["created_at"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
    assert len({r["id"] for r in rows}) == len(rows)
    assert rows[-1]["tool"] == "publish_resume"


def test_one_keys_log_does_not_show_my_other_keys_calls(
    client, db_session, signed_in, a_user
):
    first, _ = mint_agent_key(db_session, user=a_user, label="first")
    second, _ = mint_agent_key(db_session, user=a_user, label="second")
    for key, tool in ((first, "from_first"), (second, "from_second")):
        db_session.add(
            AgentToolCall(key_id=key.id, user_id=a_user.id, tool=tool, outcome="ok")
        )
    db_session.commit()
    rows = client.get(f"{KEYS}/{first.id}/calls").json()
    assert [r["tool"] for r in rows] == ["from_first"]


def test_a_positive_expiry_is_accepted_and_sets_expires_at(client, db_session, signed_in):
    resp = client.post(KEYS, json={"label": "k", "expires_in_days": 30})
    assert resp.status_code == 200
    expires = datetime.fromisoformat(resp.json()["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(days=29, hours=23) < delta <= timedelta(days=30)
    # And without the field the key does not expire.
    assert client.post(KEYS, json={"label": "k2"}).json()["expires_at"] is None


def test_only_the_cap_is_reported_as_too_many_keys(client, signed_in, monkeypatch):
    """A different ValueError from the service must not read as a key-limit message."""
    from app.routers import account as account_router

    def boom(*_a, **_k):
        raise ValueError("something else entirely")

    monkeypatch.setattr(account_router, "mint_agent_key", boom)
    with pytest.raises(ValueError, match="something else"):
        client.post(KEYS, json={"label": "k"})


def test_a_revoked_keys_call_log_outlives_the_credential(
    client, db_session, signed_in, a_user
):
    """Deliberate: DELETE on a revoked key is 404, but its log stays readable.

    An audit trail that vanishes when you revoke is useless exactly when you
    revoke because something looked wrong."""
    key, _ = mint_agent_key(db_session, user=a_user, label="leaked")
    db_session.add(
        AgentToolCall(key_id=key.id, user_id=a_user.id, tool="get_resume", outcome="ok")
    )
    db_session.commit()
    assert client.delete(f"{KEYS}/{key.id}").status_code == 204
    assert client.delete(f"{KEYS}/{key.id}").status_code == 404
    rows = client.get(f"{KEYS}/{key.id}/calls")
    assert rows.status_code == 200
    assert [r["tool"] for r in rows.json()] == ["get_resume"]
