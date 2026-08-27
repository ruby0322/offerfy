from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.main import app
from app.models import User
from app.services.guest import GUEST_COOKIE


def _session_cookie(user: User) -> dict[str, str]:
    return {SESSION_COOKIE: _sign(user.id, get_settings().auth_token_secret)}


def _make_user(db_session, *, sub: str = "sub-share", email: str = "owner@example.com") -> User:
    user = User(google_sub=sub, email=email, locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def test_guest_put_share_401(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "G"}).json()
    response = client.put(f"/v1/resumes/{created['id']}/share", json={"public": True})
    assert response.status_code == 401
    assert response.json()["detail"] == "Sign in required"


def test_unclaimed_guest_resume_put_share_403(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "G"})
    resume_id = created.json()["id"]
    guest_key = created.cookies.get(GUEST_COOKIE) or client.cookies.get(GUEST_COOKIE)
    user = _make_user(db_session)
    cookies = {**_session_cookie(user), GUEST_COOKIE: guest_key}
    response = client.put(
        f"/v1/resumes/{resume_id}/share",
        json={"public": True},
        cookies=cookies,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Sign in required to share"


def test_user_share_public_keeps_token_until_private(client: TestClient, db_session):
    user = _make_user(db_session)
    cookies = _session_cookie(user)
    created = client.post(
        "/v1/resumes",
        json={"locale": "en", "title": "Mine"},
        cookies=cookies,
    ).json()
    resume_id = created["id"]

    first = client.put(
        f"/v1/resumes/{resume_id}/share",
        json={"public": True},
        cookies=cookies,
    )
    assert first.status_code == 200
    body = first.json()
    assert body["public"] is True
    token = body["token"]
    assert isinstance(token, str) and len(token) >= 16

    again = client.put(
        f"/v1/resumes/{resume_id}/share",
        json={"public": True},
        cookies=cookies,
    )
    assert again.status_code == 200
    assert again.json()["token"] == token

    got = client.get(f"/v1/resumes/{resume_id}/share", cookies=cookies)
    assert got.status_code == 200
    assert got.json() == {"public": True, "token": token}

    private = client.put(
        f"/v1/resumes/{resume_id}/share",
        json={"public": False},
        cookies=cookies,
    )
    assert private.status_code == 200
    assert private.json() == {"public": False, "token": None}

    anon = TestClient(app)
    assert anon.get(f"/v1/shares/{token}").status_code == 404

    second = client.put(
        f"/v1/resumes/{resume_id}/share",
        json={"public": True},
        cookies=cookies,
    )
    assert second.status_code == 200
    new_token = second.json()["token"]
    assert new_token != token
    assert anon.get(f"/v1/shares/{token}").status_code == 404


def test_put_title_strips_and_rejects_empty(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "Old"}).json()
    trimmed = client.put(f"/v1/resumes/{created['id']}", json={"title": "  New Name  "})
    assert trimmed.status_code == 200
    assert trimmed.json()["title"] == "New Name"

    empty = client.put(f"/v1/resumes/{created['id']}", json={"title": "   "})
    assert empty.status_code == 400
    assert empty.json()["detail"] == "Title is required"


def test_get_resume_includes_created_at(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "T"}).json()
    assert "created_at" in created
    assert created["created_at"]
    got = client.get(f"/v1/resumes/{created['id']}").json()
    assert got["created_at"] == created["created_at"]


def test_public_share_meta_preview_export(client: TestClient, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.routers.shares.compile_typst_pages",
        lambda source, fmt: [b"<svg>page</svg>"],
    )
    monkeypatch.setattr(
        "app.routers.shares.compile_typst",
        lambda source, fmt: b"%PDF-share",
    )
    user = _make_user(db_session, sub="sub-pub", email="pub@example.com")
    cookies = _session_cookie(user)
    created = client.post(
        "/v1/resumes",
        json={"locale": "en", "title": "Public Me"},
        cookies=cookies,
    ).json()
    token = client.put(
        f"/v1/resumes/{created['id']}/share",
        json={"public": True},
        cookies=cookies,
    ).json()["token"]

    anon = TestClient(app)
    meta = anon.get(f"/v1/shares/{token}")
    assert meta.status_code == 200
    body = meta.json()
    assert body == {"title": "Public Me", "locale": "en"}
    assert "id" not in body
    assert "typst_source" not in body

    preview = anon.get(f"/v1/shares/{token}/preview")
    assert preview.status_code == 200
    assert preview.json()["pages"] == ["<svg>page</svg>"]

    export = anon.get(f"/v1/shares/{token}/export")
    assert export.status_code == 200
    assert export.content == b"%PDF-share"
    assert "attachment" in export.headers.get("content-disposition", "").lower()

    assert anon.get("/v1/shares/not-a-real-token").status_code == 404
    assert anon.get("/v1/shares/not-a-real-token/preview").status_code == 404
    assert anon.get("/v1/shares/not-a-real-token/export").status_code == 404
