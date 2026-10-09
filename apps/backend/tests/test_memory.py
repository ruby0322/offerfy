from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import MemoryItem, Resume, User
from app.services.memory import MAX_ACCEPTED, propose_memory


def _guest_master(client):
    return client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()


def _signed_in(client, db_session, sub="sub-mem"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def test_propose_dedupes_and_lists_family_wide(client, db_session):
    master = _guest_master(client)
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "T"}).json()
    row = db_session.get(Resume, master["id"])
    first, created1 = propose_memory(db_session, row, tailor["id"], "  No   photo ")
    again, created2 = propose_memory(db_session, row, master["id"], "no photo")
    db_session.commit()
    assert created1 and not created2 and first.id == again.id
    assert first.text == "No photo"
    listed = client.get(f"/v1/resumes/{tailor['id']}/memory").json()
    assert [item["text"] for item in listed] == ["No photo"]
    assert listed[0]["status"] == "pending"


def test_propose_rejects_empty_and_long(client, db_session):
    master = _guest_master(client)
    row = db_session.get(Resume, master["id"])
    for bad in ("   ", "x" * 281):
        try:
            propose_memory(db_session, row, master["id"], bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_guest_cannot_accept_but_can_dismiss(client, db_session):
    master = _guest_master(client)
    item, _ = propose_memory(
        db_session, db_session.get(Resume, master["id"]), master["id"], "No photo"
    )
    db_session.commit()
    assert client.post(f"/v1/memory/{item.id}/accept").status_code == 401
    db_session.expire_all()
    assert db_session.get(MemoryItem, item.id).status == "pending"
    assert client.post(f"/v1/memory/{item.id}/dismiss").json()["status"] == "dismissed"
    assert client.get(f"/v1/resumes/{master['id']}/memory").json() == []


def test_signed_in_owner_accepts_after_claim_and_cap(client, db_session):
    master = _guest_master(client)
    row = db_session.get(Resume, master["id"])
    item, _ = propose_memory(db_session, row, master["id"], "Keep one page")
    db_session.commit()
    _signed_in(client, db_session)
    assert client.post("/v1/auth/claim").json()["claimed"] == 1
    assert client.post(f"/v1/memory/{item.id}/accept").json()["status"] == "accepted"
    for i in range(MAX_ACCEPTED - 1):
        extra, _ = propose_memory(db_session, row, master["id"], f"Fact {i}")
        db_session.commit()
        assert client.post(f"/v1/memory/{extra.id}/accept").status_code == 200
    over, _ = propose_memory(db_session, row, master["id"], "One too many")
    db_session.commit()
    assert client.post(f"/v1/memory/{over.id}/accept").status_code == 409
    assert client.delete(f"/v1/memory/{item.id}").status_code == 204
    assert client.post(f"/v1/memory/{over.id}/accept").status_code == 200


def test_other_user_cannot_touch_memory(client, db_session):
    master = _guest_master(client)
    item, _ = propose_memory(
        db_session, db_session.get(Resume, master["id"]), master["id"], "X"
    )
    db_session.commit()
    client.cookies.clear()
    _signed_in(client, db_session, sub="sub-other")
    assert client.post(f"/v1/memory/{item.id}/accept").status_code == 404
    assert client.post(f"/v1/memory/{item.id}/dismiss").status_code == 404
