import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.main import app
from app.models import Resume, ResumeRevision, ResumeShare, User
from app.services.revisions import backfill_existing_resumes, set_draft_source


def _session_cookie(user: User) -> dict[str, str]:
    return {SESSION_COOKIE: _sign(user.id, get_settings().auth_token_secret)}


def _make_user(db_session, *, sub: str = "sub-rev", email: str = "rev@example.com") -> User:
    user = User(google_sub=sub, email=email, locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _latest(db_session, resume_id: str) -> ResumeRevision:
    row = (
        db_session.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == resume_id)
        .order_by(ResumeRevision.created_at.desc(), ResumeRevision.id.desc())
        .first()
    )
    assert row is not None
    return row


def _origin(db_session, resume_id: str) -> datetime:
    return _aware(_latest(db_session, resume_id).created_at)


def _revision_count(db_session, resume_id: str) -> int:
    return (
        db_session.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == resume_id)
        .count()
    )


def test_create_resume_inserts_initial_revision(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "T"}).json()
    assert _revision_count(db_session, created["id"]) == 1
    assert created["updated_at"]
    assert created["published_revision_id"] is None
    assert created["unpublished_changes"] is True


def test_coalesce_puts_within_window_update_one_revision(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    resume = db_session.get(Resume, created["id"])
    assert resume is not None
    t0 = _origin(db_session, created["id"])
    set_draft_source(db_session, resume, created["typst_source"] + "\n// a\n", now=t0 + timedelta(seconds=1))
    set_draft_source(db_session, resume, created["typst_source"] + "\n// b\n", now=t0 + timedelta(seconds=2))
    db_session.commit()
    rows = (
        db_session.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == created["id"])
        .order_by(ResumeRevision.created_at.asc())
        .all()
    )
    assert len(rows) == 1
    assert rows[0].typst_source.endswith("// b\n")


def test_put_after_coalesce_window_inserts_revision(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    resume = db_session.get(Resume, created["id"])
    assert resume is not None
    t0 = _origin(db_session, created["id"])
    set_draft_source(db_session, resume, created["typst_source"] + "\n// a\n", now=t0 + timedelta(seconds=1))
    set_draft_source(
        db_session,
        resume,
        created["typst_source"] + "\n// b\n",
        now=t0 + timedelta(seconds=121),
    )
    db_session.commit()
    assert _revision_count(db_session, created["id"]) == 2


def test_identical_source_does_not_insert_revision(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    resume = db_session.get(Resume, created["id"])
    assert resume is not None
    set_draft_source(db_session, resume, created["typst_source"], now=_origin(db_session, created["id"]) + timedelta(seconds=121))
    db_session.commit()
    assert _revision_count(db_session, created["id"]) == 1


def test_put_resume_records_revision(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    new_source = created["typst_source"] + "\n// put\n"
    put = client.put(f"/v1/resumes/{created['id']}", json={"typst_source": new_source})
    assert put.status_code == 200
    assert put.json()["typst_source"] == new_source
    assert put.json()["updated_at"]
    db_session.expire_all()
    resume = db_session.get(Resume, created["id"])
    assert resume is not None
    latest = (
        db_session.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == created["id"])
        .order_by(ResumeRevision.created_at.desc())
        .first()
    )
    assert latest is not None
    assert latest.typst_source == new_source


def test_list_revisions_newest_first_without_source(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    resume = db_session.get(Resume, created["id"])
    assert resume is not None
    t0 = _origin(db_session, created["id"])
    set_draft_source(db_session, resume, created["typst_source"] + "\n// a\n", now=t0 + timedelta(seconds=1))
    set_draft_source(
        db_session,
        resume,
        created["typst_source"] + "\n// b\n",
        now=t0 + timedelta(seconds=121),
    )
    db_session.commit()
    listed = client.get(f"/v1/resumes/{created['id']}/revisions")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 2
    assert rows[0]["created_at"] >= rows[1]["created_at"]
    assert "typst_source" not in rows[0]
    assert rows[0]["is_published"] is False
    detail = client.get(f"/v1/resumes/{created['id']}/revisions/{rows[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["typst_source"].endswith("// b\n")


def test_restore_sets_draft_without_moving_published_pointer(client: TestClient, db_session):
    user = _make_user(db_session)
    cookies = _session_cookie(user)
    created = client.post(
        "/v1/resumes", json={"locale": "en", "title": "Mine"}, cookies=cookies
    ).json()
    resume_id = created["id"]
    resume = db_session.get(Resume, resume_id)
    assert resume is not None
    first_source = created["typst_source"] + "\n// first\n"
    second_source = created["typst_source"] + "\n// second\n"
    t0 = _origin(db_session, resume_id)
    set_draft_source(db_session, resume, first_source, now=t0 + timedelta(seconds=1))
    db_session.commit()
    older_id = _latest(db_session, resume_id).id
    set_draft_source(db_session, resume, second_source, now=t0 + timedelta(seconds=121))
    db_session.commit()
    pub = client.post(f"/v1/resumes/{resume_id}/publish", cookies=cookies)
    assert pub.status_code == 200
    published_id = pub.json()["published_revision_id"]
    restored = client.post(
        f"/v1/resumes/{resume_id}/revisions/{older_id}/restore",
        cookies=cookies,
    )
    assert restored.status_code == 200
    assert restored.json()["typst_source"] == first_source
    assert restored.json()["published_revision_id"] == published_id
    assert restored.json()["unpublished_changes"] is True


def test_restore_within_coalesce_window_keeps_prior_revision(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    resume_id = created["id"]
    resume = db_session.get(Resume, resume_id)
    assert resume is not None
    older_id = _latest(db_session, resume_id).id
    starter = created["typst_source"]
    edited = starter + "\n// unpublished\n"
    t0 = _origin(db_session, resume_id)
    set_draft_source(db_session, resume, edited, now=t0 + timedelta(seconds=121))
    db_session.commit()
    edited_id = _latest(db_session, resume_id).id
    assert edited_id != older_id
    restored = client.post(f"/v1/resumes/{resume_id}/revisions/{older_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["typst_source"] == starter
    db_session.expire_all()
    assert _revision_count(db_session, resume_id) == 3
    edited_row = db_session.get(ResumeRevision, edited_id)
    assert edited_row is not None
    assert edited_row.typst_source == edited


def test_backfill_existing_share_keeps_preview_source(client: TestClient, db_session, monkeypatch):
    seen: list[str] = []

    def _pages(source, fmt):
        seen.append(source)
        return [b"<svg>page</svg>"]

    monkeypatch.setattr("app.routers.shares.compile_typst_pages", _pages)
    created = client.post("/v1/resumes", json={"locale": "en", "title": "Old"}).json()
    resume_id = created["id"]
    original = created["typst_source"]
    resume = db_session.get(Resume, resume_id)
    assert resume is not None
    resume.published_revision_id = None
    db_session.query(ResumeRevision).filter(ResumeRevision.resume_id == resume_id).delete()
    db_session.add(ResumeShare(resume_id=resume_id, token="backfilltok"))
    db_session.commit()
    db_session.expire_all()
    anon = TestClient(app)
    missing = anon.get("/v1/shares/backfilltok/preview")
    assert missing.status_code == 404
    backfill_existing_resumes(db_session)
    db_session.commit()
    db_session.expire_all()
    resume = db_session.get(Resume, resume_id)
    assert resume is not None
    assert resume.published_revision_id
    preview = anon.get("/v1/shares/backfilltok/preview")
    assert preview.status_code == 200
    assert seen[-1] == original
    client.put(
        f"/v1/resumes/{resume_id}",
        json={"typst_source": original + "\n// later draft\n"},
    )
    again = anon.get("/v1/shares/backfilltok/preview")
    assert again.status_code == 200
    assert seen[-1] == original
    assert "// later draft" not in seen[-1]


def test_guest_cannot_publish(client: TestClient):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    response = client.post(f"/v1/resumes/{created['id']}/publish")
    assert response.status_code == 401
    assert response.json()["detail"] == "Sign in required"


def test_non_owner_publish_404(client: TestClient, db_session):
    owner = _make_user(db_session, sub="sub-owner", email="owner@example.com")
    other = _make_user(db_session, sub="sub-other", email="other@example.com")
    created = client.post(
        "/v1/resumes",
        json={"locale": "en"},
        cookies=_session_cookie(owner),
    ).json()
    response = client.post(
        f"/v1/resumes/{created['id']}/publish",
        cookies=_session_cookie(other),
    )
    assert response.status_code == 404


def test_first_public_auto_pins_draft(client: TestClient, db_session):
    user = _make_user(db_session, sub="sub-pin", email="pin@example.com")
    cookies = _session_cookie(user)
    created = client.post(
        "/v1/resumes", json={"locale": "en", "title": "Pin"}, cookies=cookies
    ).json()
    state = client.put(
        f"/v1/resumes/{created['id']}/share",
        json={"public": True},
        cookies=cookies,
    )
    assert state.status_code == 200
    body = state.json()
    assert body["public"] is True
    assert body["published_revision_id"]
    assert body["unpublished_changes"] is False
    got = client.get(f"/v1/resumes/{created['id']}", cookies=cookies).json()
    assert got["published_revision_id"] == body["published_revision_id"]
    assert got["unpublished_changes"] is False


def test_public_preview_ignores_draft_until_publish(client: TestClient, db_session, monkeypatch):
    seen: list[str] = []

    def _pages(source, fmt):
        seen.append(source)
        return [b"<svg>page</svg>"]

    monkeypatch.setattr("app.routers.shares.compile_typst_pages", _pages)
    user = _make_user(db_session, sub="sub-pub-draft", email="pubd@example.com")
    cookies = _session_cookie(user)
    created = client.post(
        "/v1/resumes", json={"locale": "en", "title": "Pub"}, cookies=cookies
    ).json()
    token = client.put(
        f"/v1/resumes/{created['id']}/share",
        json={"public": True},
        cookies=cookies,
    ).json()["token"]
    original = created["typst_source"]
    client.put(
        f"/v1/resumes/{created['id']}",
        json={"typst_source": original + "\n// secret draft\n"},
        cookies=cookies,
    )
    anon = TestClient(app)
    preview = anon.get(f"/v1/shares/{token}/preview")
    assert preview.status_code == 200
    assert seen[-1] == original
    assert "// secret draft" not in seen[-1]
    client.post(f"/v1/resumes/{created['id']}/publish", cookies=cookies)
    again = anon.get(f"/v1/shares/{token}/preview")
    assert again.status_code == 200
    assert seen[-1].endswith("// secret draft\n")


def test_edit_after_publish_inserts_new_revision(client: TestClient, db_session):
    user = _make_user(db_session, sub="sub-after", email="after@example.com")
    cookies = _session_cookie(user)
    created = client.post(
        "/v1/resumes", json={"locale": "en"}, cookies=cookies
    ).json()
    resume_id = created["id"]
    client.post(f"/v1/resumes/{resume_id}/publish", cookies=cookies)
    published_id = client.get(f"/v1/resumes/{resume_id}", cookies=cookies).json()[
        "published_revision_id"
    ]
    client.put(
        f"/v1/resumes/{resume_id}",
        json={"typst_source": created["typst_source"] + "\n// after\n"},
        cookies=cookies,
    )
    db_session.expire_all()
    rows = (
        db_session.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == resume_id)
        .order_by(ResumeRevision.created_at.asc())
        .all()
    )
    assert len(rows) == 2
    assert rows[0].id == published_id
    assert rows[0].typst_source == created["typst_source"]
    assert rows[1].typst_source.endswith("// after\n")


def test_job_typst_edit_records_revision(client: TestClient, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    from app.jobs.typst_edit import main

    extra = created["typst_source"] + "\n// job\n"
    rc = main(
        [
            "--resume-id",
            created["id"],
            "--patch",
            json.dumps({"source": extra}),
        ]
    )
    assert rc == 0
    db_session.expire_all()
    latest = (
        db_session.query(ResumeRevision)
        .filter(ResumeRevision.resume_id == created["id"])
        .order_by(ResumeRevision.created_at.desc())
        .first()
    )
    assert latest is not None
    assert latest.typst_source == extra
