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
