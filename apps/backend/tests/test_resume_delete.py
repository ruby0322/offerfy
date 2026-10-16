from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import ChatMessage, MemoryItem, Resume, ResumeRevision, ResumeShare, User


def _master(client, title="Master"):
    return client.post("/v1/resumes", json={"locale": "en", "title": title}).json()


def _tailor(client, master_id, title="Tailored"):
    return client.post(f"/v1/resumes/{master_id}/tailors", json={"title": title}).json()


def _sign_in(client, db_session, *, sub="sub-delete", email="owner@example.com") -> User:
    user = User(google_sub=sub, email=email, locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def test_delete_tailor_leaves_the_master(client):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    assert client.delete(f"/v1/resumes/{tailor['id']}").status_code == 204
    assert client.get(f"/v1/resumes/{tailor['id']}").status_code == 404
    assert client.get(f"/v1/resumes/{master['id']}").status_code == 200
    assert client.get(f"/v1/resumes/{master['id']}/family").json()["tailors"] == []


def test_delete_master_takes_its_tailors(client):
    master = _master(client)
    first = _tailor(client, master["id"], "A")
    second = _tailor(client, master["id"], "B")
    assert client.delete(f"/v1/resumes/{master['id']}").status_code == 204
    for resume in (master, first, second):
        assert client.get(f"/v1/resumes/{resume['id']}").status_code == 404
    assert client.get("/v1/resumes").json() == []


def test_delete_master_clears_revisions_chat_and_memory(client, db_session):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    db_session.add(ChatMessage(resume_id=tailor["id"], role="user", content="hi"))
    db_session.add(
        MemoryItem(
            master_resume_id=master["id"],
            source_resume_id=tailor["id"],
            text="No photo",
            status="accepted",
        )
    )
    db_session.commit()
    assert client.delete(f"/v1/resumes/{master['id']}").status_code == 204
    assert db_session.query(Resume).count() == 0
    assert db_session.query(ResumeRevision).count() == 0
    assert db_session.query(ChatMessage).count() == 0
    assert db_session.query(MemoryItem).count() == 0


def test_delete_tailor_keeps_the_memory_it_proposed(client, db_session):
    master = _master(client)
    tailor = _tailor(client, master["id"])
    db_session.add(
        MemoryItem(
            master_resume_id=master["id"],
            source_resume_id=tailor["id"],
            text="Keep it to one page",
            status="accepted",
        )
    )
    db_session.commit()
    assert client.delete(f"/v1/resumes/{tailor['id']}").status_code == 204
    rows = client.get(f"/v1/resumes/{master['id']}/memory").json()
    assert [row["text"] for row in rows] == ["Keep it to one page"]
    assert rows[0]["source_resume_id"] is None


def test_delete_published_and_shared_resume(client, db_session):
    _sign_in(client, db_session)
    master = _master(client)
    client.put(
        f"/v1/resumes/{master['id']}",
        json={"typst_source": master["typst_source"] + "\n// edited"},
    )
    assert client.post(f"/v1/resumes/{master['id']}/publish").status_code == 200
    assert client.put(
        f"/v1/resumes/{master['id']}/share", json={"public": True}
    ).status_code == 200
    assert client.delete(f"/v1/resumes/{master['id']}").status_code == 204
    assert db_session.query(ResumeShare).count() == 0
    assert db_session.query(ResumeRevision).count() == 0
    assert db_session.query(Resume).count() == 0


def test_delete_is_private_to_the_owner(client):
    master = _master(client)
    client.cookies.clear()
    assert client.delete(f"/v1/resumes/{master['id']}").status_code == 404


def test_delete_twice_is_not_found(client):
    master = _master(client)
    assert client.delete(f"/v1/resumes/{master['id']}").status_code == 204
    assert client.delete(f"/v1/resumes/{master['id']}").status_code == 404


def test_delete_removes_the_uploaded_file(client, db_session, monkeypatch, tmp_path):
    removed: list[str] = []
    monkeypatch.setattr(
        "app.routers.resumes.delete_object",
        lambda key: removed.append(key) is None,
    )
    master = _master(client)
    row = db_session.get(Resume, master["id"])
    row.upload_s3_key = "uploads/owner/master/resume.pdf"
    db_session.commit()
    tailor = _tailor(client, master["id"])
    tailor_row = db_session.get(Resume, tailor["id"])
    tailor_row.upload_s3_key = "uploads/owner/tailor/resume.pdf"
    db_session.commit()
    assert client.delete(f"/v1/resumes/{master['id']}").status_code == 204
    assert sorted(removed) == [
        "uploads/owner/master/resume.pdf",
        "uploads/owner/tailor/resume.pdf",
    ]
