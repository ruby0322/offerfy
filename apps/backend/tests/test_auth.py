from fastapi import Request
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import GuestSession, Resume, User
from app.routers.auth import _google_picture, claim, google_callback
from app.services.guest import GUEST_COOKIE, hash_guest_key


def test_google_picture_accepts_https_only():
    assert _google_picture({"picture": "https://lh3.googleusercontent.com/a/x"}) == (
        "https://lh3.googleusercontent.com/a/x"
    )
    assert _google_picture({"picture": "http://evil.example/x"}) is None
    assert _google_picture({"picture": "javascript:alert(1)"}) is None
    assert _google_picture({}) is None


def test_me_guest(client: TestClient):
    response = client.get("/v1/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["guest"] is True
    assert body["user"] is None


def test_me_returns_picture(client: TestClient, db_session):
    user = User(
        google_sub="sub-nav",
        email="ada@example.com",
        locale="en",
        picture="https://lh3.googleusercontent.com/a/test",
    )
    db_session.add(user)
    db_session.commit()
    token = _sign(user.id, get_settings().auth_token_secret)
    response = client.get("/v1/auth/me", cookies={SESSION_COOKIE: token})
    assert response.status_code == 200
    body = response.json()
    assert body["guest"] is False
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["picture"] == "https://lh3.googleusercontent.com/a/test"


def test_me_is_admin_true_for_allowlisted_email(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    user = User(google_sub="sub-ops-me", email="ops@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    token = _sign(user.id, get_settings().auth_token_secret)
    body = client.get("/v1/auth/me", cookies={SESSION_COOKIE: token}).json()
    assert body["guest"] is False
    assert body["user"]["is_admin"] is True


def test_me_is_admin_false_for_everyone_else(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    user = User(google_sub="sub-user-me", email="ada@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    token = _sign(user.id, get_settings().auth_token_secret)
    body = client.get("/v1/auth/me", cookies={SESSION_COOKIE: token}).json()
    assert body["user"]["is_admin"] is False

    monkeypatch.setenv("ADMIN_EMAILS", "")
    get_settings.cache_clear()
    ops = User(google_sub="sub-ops-empty", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    token = _sign(ops.id, get_settings().auth_token_secret)
    body = client.get("/v1/auth/me", cookies={SESSION_COOKIE: token}).json()
    assert body["user"]["is_admin"] is False


class _FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttpx:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, data=None):
        return _FakeResp(200, {"access_token": "tok"})

    def get(self, url, headers=None):
        return _FakeResp(
            200,
            {
                "sub": "g-nav",
                "email": "nav@example.com",
                "picture": "https://lh3.googleusercontent.com/a/from-google",
            },
        )


def test_google_callback_persists_picture(client: TestClient, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _FakeHttpx)

    response = client.get("/v1/auth/google/callback", params={"code": "abc"}, follow_redirects=False)
    assert response.status_code == 302
    me = client.get("/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["guest"] is False
    assert body["user"]["email"] == "nav@example.com"
    assert body["user"]["picture"] == "https://lh3.googleusercontent.com/a/from-google"


def test_google_callback_missing_code(client: TestClient):
    response = client.get("/v1/auth/google/callback")
    assert response.status_code == 400


def test_google_next_editor_allowed(client: TestClient, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _FakeHttpx)
    editor_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    start = client.get(
        "/v1/auth/google/start",
        params={"next": f"/editor/{editor_id}"},
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    assert state == f"/editor/{editor_id}"
    cb = client.get(
        "/v1/auth/google/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == f"/editor/{editor_id}"


def test_google_start_production_rejects_loopback_redirect(
    client: TestClient, monkeypatch
):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "http://127.0.0.1:3001/api/v1/auth/google/callback",
    )
    get_settings.cache_clear()
    response = client.get("/v1/auth/google/start", follow_redirects=False)
    assert response.status_code == 500


def test_google_start_production_uses_public_redirect(
    client: TestClient, monkeypatch
):
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "https://offerfy.cc/api/v1/auth/google/callback",
    )
    get_settings.cache_clear()
    response = client.get("/v1/auth/google/start", follow_redirects=False)
    assert response.status_code == 302
    uri = parse_qs(urlparse(response.headers["location"]).query)["redirect_uri"][
        0
    ]
    assert uri == "https://offerfy.cc/api/v1/auth/google/callback"


def test_google_next_editor_traversal_rejected(client: TestClient, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _FakeHttpx)
    start = client.get(
        "/v1/auth/google/start",
        params={"next": "/editor/../admin"},
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    assert state == ""
    cb = client.get(
        "/v1/auth/google/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == "/dashboard"


def _fake_google(payload: dict):
    """An httpx.Client stand-in returning one chosen userinfo payload."""

    class _Client(_FakeHttpx):
        def get(self, url, headers=None):
            return _FakeResp(200, payload)

    return _Client


def _sign_in_with(client: TestClient, monkeypatch, payload: dict):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _fake_google(payload))
    response = client.get(
        "/v1/auth/google/callback", params={"code": "abc"}, follow_redirects=False
    )
    assert response.status_code == 302


def test_google_name_is_persisted_on_signup(client: TestClient, db_session, monkeypatch):
    _sign_in_with(
        client,
        monkeypatch,
        {"sub": "g-name", "email": "name@example.com", "name": "Chen Wei"},
    )
    user = db_session.query(User).filter(User.google_sub == "g-name").one()
    assert user.name == "Chen Wei"


def test_google_name_is_refreshed_on_later_login(
    client: TestClient, db_session, monkeypatch
):
    _sign_in_with(
        client,
        monkeypatch,
        {"sub": "g-name", "email": "name@example.com", "name": "Chen Wei"},
    )
    _sign_in_with(
        client,
        monkeypatch,
        {"sub": "g-name", "email": "name@example.com", "name": "Wei Chen"},
    )
    user = db_session.query(User).filter(User.google_sub == "g-name").one()
    assert user.name == "Wei Chen"


def test_absent_google_name_does_not_blank_a_known_name(
    client: TestClient, db_session, monkeypatch
):
    """Google can omit `name`. Blanking it would hand an employer a nameless profile."""
    _sign_in_with(
        client,
        monkeypatch,
        {"sub": "g-name", "email": "name@example.com", "name": "Chen Wei"},
    )
    _sign_in_with(client, monkeypatch, {"sub": "g-name", "email": "name@example.com"})
    user = db_session.query(User).filter(User.google_sub == "g-name").one()
    assert user.name == "Chen Wei"


# --------------------------------------------------------------------------
# The commit boundary: a write must be durable before the caller reads back
#
# `get_db` commits in FastAPI's dependency teardown, and FastAPI closes that
# exit stack *after* the response has been sent. Both routes below are read
# back at once -- the callback by the page the browser is redirected to, the
# claim by the resume list the dashboard and the editor fetch next -- so each
# commits before it responds. The long version is in
# `app.routers.account.create_agent_key`.
#
# Both are called directly rather than over the TestClient, for the reason
# `tests/test_account.py::test_minting_commits_before_it_returns` sets out: a
# TestClient request runs its own teardown before the call returns, and the
# in-memory database shares one connection, so flushed and committed look
# alike over HTTP. The open transaction is the one thing that differs.
# --------------------------------------------------------------------------


def _bare_request(cookie: str | None = None) -> Request:
    """The smallest ASGI scope `google_callback` and `claim` actually read."""
    headers = [(b"host", b"testserver")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/auth/google/callback",
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("test", 1),
            "root_path": "",
        }
    )


def test_the_google_callback_commits_before_it_redirects(db_session, monkeypatch):
    """A sign-up's account row must outlive the 302 that carries its cookie."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI", "http://testserver/api/v1/auth/google/callback"
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _FakeHttpx)

    response = google_callback(_bare_request(), code="abc", db=db_session)

    assert response.status_code == 302
    assert not db_session.in_transaction(), "the new account was left uncommitted"
    get_settings.cache_clear()


def test_claiming_commits_before_it_returns(db_session):
    """`list_resumes` reads by user_id, so an uncommitted claim lists nothing."""
    user = User(google_sub="g-claim-commit", email="claim@example.com", locale="en")
    db_session.add(user)
    raw = "guest-key-for-the-claim-commit-test"
    guest = GuestSession(key_hash=hash_guest_key(raw), locale="en")
    db_session.add(guest)
    db_session.flush()
    db_session.add(
        Resume(
            title="Anonymous draft",
            typst_source="= CV",
            source="create",
            locale="en",
            guest_session_id=guest.id,
        )
    )
    db_session.commit()

    out = claim(_bare_request(cookie=f"{GUEST_COOKIE}={raw}"), db=db_session, user=user)

    assert out.claimed == 1
    assert not db_session.in_transaction(), "the claim was left uncommitted"
