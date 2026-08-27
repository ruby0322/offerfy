from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import User
from app.routers.auth import _google_picture


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
