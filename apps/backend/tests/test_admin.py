from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import ChatMessage, Resume, User


def _session_cookie(user: User) -> dict[str, str]:
    return {SESSION_COOKIE: _sign(user.id, get_settings().auth_token_secret)}


def _allow(monkeypatch, email: str = "ops@example.com") -> None:
    monkeypatch.setenv("ADMIN_EMAILS", email)
    get_settings.cache_clear()


def test_user_and_resume_have_created_at(client: TestClient, db_session):
    user = User(google_sub="sub-ts", email="ts@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.created_at is not None
    created_at = user.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    assert created_at <= datetime.now(timezone.utc)

    posted = client.post("/v1/resumes", json={"locale": "en", "title": "T"}).json()
    resume = db_session.get(Resume, posted["id"])
    assert resume is not None
    assert resume.created_at is not None


def test_admin_overview_unauthenticated_401(client: TestClient, monkeypatch):
    _allow(monkeypatch)
    assert client.get("/v1/admin/overview").status_code == 401


def test_admin_overview_non_admin_404(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    user = User(google_sub="sub-user", email="user@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    assert client.get("/v1/admin/overview", cookies=_session_cookie(user)).status_code == 404


def test_admin_overview_empty_allowlist_404(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "")
    get_settings.cache_clear()
    user = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    assert client.get("/v1/admin/overview", cookies=_session_cookie(user)).status_code == 404


def test_admin_overview_allowlisted_counts(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.setenv("S3_ACCESS_KEY", "")
    monkeypatch.setenv("S3_SECRET_KEY", "")
    monkeypatch.setenv("S3_BUCKET", "")
    get_settings.cache_clear()
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    other = User(google_sub="sub-o", email="o@example.com", locale="zh-TW")
    db_session.add_all([ops, other])
    db_session.commit()
    client.post("/v1/resumes", json={"locale": "en", "title": "Guest one"})
    old = ChatMessage(
        resume_id=client.get("/v1/resumes").json()[0]["id"],
        role="user",
        content="old",
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    db_session.add(old)
    db_session.commit()
    res = client.get("/v1/admin/overview", cookies=_session_cookie(ops))
    assert res.status_code == 200
    body = res.json()
    assert body["health"]["api"] == "ok"
    assert body["health"]["database"] == "ok"
    assert body["health"]["s3_configured"] is False
    assert body["counts"]["users"] == 2
    assert body["counts"]["resumes"] >= 1
    assert body["counts"]["chat_messages_24h"] == 0
    assert body["counts"]["chat_messages_7d"] >= 1
    assert isinstance(body["recent_users"], list)
    assert isinstance(body["recent_resumes"], list)
    series = body["series"]
    assert len(series) == 14
    assert series[-1]["users"] >= 2
    assert series[-1]["resumes_create"] >= 1
    chat_days = [row["chats"] for row in series]
    assert sum(chat_days) >= 1
    assert series[-1]["chats"] == 0


def test_admin_users_search_and_detail(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    ada = User(google_sub="sub-ada", email="ada@example.com", locale="en")
    db_session.add_all([ops, ada])
    db_session.commit()
    cookies = _session_cookie(ops)
    listed = client.get("/v1/admin/users", params={"q": "ada@"}, cookies=cookies)
    assert listed.status_code == 200
    emails = [row["email"] for row in listed.json()["items"]]
    assert emails == ["ada@example.com"]
    assert listed.json()["total"] == 1
    detail = client.get(f"/v1/admin/users/{ada.id}", cookies=cookies)
    assert detail.status_code == 200
    assert detail.json()["google_sub"] == "sub-ada"
    assert detail.json()["resumes"] == []
    missing = client.get("/v1/admin/users/not-a-user", cookies=cookies)
    assert missing.status_code == 404


def test_admin_reads_other_user_and_guest_resume(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    ada = User(google_sub="sub-ada", email="ada@example.com", locale="en")
    db_session.add_all([ops, ada])
    db_session.commit()
    guest_resume = client.post("/v1/resumes", json={"locale": "en", "title": "Guest CV"}).json()
    db_session.add(
        ChatMessage(resume_id=guest_resume["id"], role="user", content="hello from guest")
    )
    db_session.commit()
    client.cookies.clear()
    other = client.get(
        f"/v1/resumes/{guest_resume['id']}",
        cookies=_session_cookie(ops),
    )
    assert other.status_code == 404

    cookies = _session_cookie(ops)
    listed = client.get("/v1/admin/resumes", params={"owner": "guest"}, cookies=cookies)
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()["items"]]
    assert guest_resume["id"] in ids
    detail = client.get(f"/v1/admin/resumes/{guest_resume['id']}", cookies=cookies)
    assert detail.status_code == 200
    assert "typst_source" in detail.json()
    assert detail.json()["owner_kind"] == "guest"
    msgs = client.get(f"/v1/admin/resumes/{guest_resume['id']}/messages", cookies=cookies)
    assert msgs.status_code == 200
    assert any(m["content"] for m in msgs.json()["items"])


def test_admin_resume_invalid_filter_422(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "OPS@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    res = client.get(
        "/v1/admin/resumes",
        params={"owner": "nope"},
        cookies=_session_cookie(ops),
    )
    assert res.status_code == 422


def test_admin_preview_and_ats_for_foreign_resume(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    guest = client.post("/v1/resumes", json={"locale": "en"}).json()
    cookies = _session_cookie(ops)
    preview = client.get(f"/v1/admin/resumes/{guest['id']}/preview", cookies=cookies)
    if preview.status_code != 503:
        assert preview.status_code == 200
        assert "pages" in preview.json()
    ats = client.get(f"/v1/admin/resumes/{guest['id']}/ats", cookies=cookies)
    if ats.status_code != 503:
        assert ats.status_code == 200
        assert "checks" in ats.json()
        assert "score" not in ats.json()


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
                "sub": "g-admin-next",
                "email": "nav-admin@example.com",
                "picture": "https://lh3.googleusercontent.com/a/from-google",
            },
        )


def _safe_next_cases(client, monkeypatch, next_value, expected_location):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _FakeHttpx)
    start = client.get(
        "/v1/auth/google/start",
        params={"next": next_value},
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    cb = client.get(
        "/v1/auth/google/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == expected_location


def test_google_next_admin_allowed(client, monkeypatch):
    _safe_next_cases(client, monkeypatch, "/admin", "/admin")


def test_google_next_admin_nested_allowed(client, monkeypatch):
    _safe_next_cases(client, monkeypatch, "/admin/resumes/x", "/admin/resumes/x")


def test_google_next_evil_ignored(client, monkeypatch):
    _safe_next_cases(client, monkeypatch, "https://evil.test", "/dashboard")
