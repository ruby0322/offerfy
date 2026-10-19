from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import MemoryItem, Resume, User


def test_resume_defaults_to_master(client, db_session):
    created = client.post("/v1/resumes", json={"locale": "en"}).json()
    row = db_session.get(Resume, created["id"])
    assert row.kind == "master"
    assert row.parent_resume_id is None


def test_deleting_master_cascades_tailors_and_memory(client, db_session):
    created = client.post("/v1/resumes", json={"locale": "en", "title": "M"}).json()
    master = db_session.get(Resume, created["id"])
    tailor = Resume(
        title="T",
        typst_source=master.typst_source,
        source="create",
        locale="en",
        guest_session_id=master.guest_session_id,
        kind="tailor",
        parent_resume_id=master.id,
    )
    db_session.add(tailor)
    db_session.flush()
    db_session.add(
        MemoryItem(master_resume_id=master.id, source_resume_id=tailor.id, text="No photo")
    )
    db_session.commit()
    tailor_id = tailor.id
    db_session.delete(master)
    db_session.commit()
    assert db_session.get(Resume, tailor_id) is None
    assert db_session.query(MemoryItem).count() == 0


def _master(client, title="Master"):
    return client.post("/v1/resumes", json={"locale": "en", "title": title}).json()


def test_create_tailor_copies_master_saved_draft(client):
    master = _master(client)
    edited = master["typst_source"].replace("Your Name", "Ada Lovelace", 1)
    client.put(f"/v1/resumes/{master['id']}", json={"typst_source": edited})
    res = client.post(
        f"/v1/resumes/{master['id']}/tailors",
        json={"title": "Acme SWE", "target_label": "SWE @ Acme", "target_context": "Python, Go"},
    )
    assert res.status_code == 201
    tailor = res.json()
    assert tailor["kind"] == "tailor"
    assert tailor["parent_resume_id"] == master["id"]
    assert tailor["target_label"] == "SWE @ Acme"
    assert tailor["target_context"] == "Python, Go"
    assert "Ada Lovelace" in tailor["typst_source"]
    assert client.get(f"/v1/resumes/{tailor['id']}/messages").json() == []


def test_create_tailor_from_tailor_forks_master_not_tailor(client):
    master = _master(client)
    first = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    client.put(
        f"/v1/resumes/{first['id']}",
        json={"typst_source": first["typst_source"] + "\n// tailor-only"},
    )
    second = client.post(f"/v1/resumes/{first['id']}/tailors", json={"title": "B"}).json()
    assert second["parent_resume_id"] == master["id"]
    assert "tailor-only" not in second["typst_source"]


def test_create_tailor_from_current_copies_the_open_tailor(client):
    master = _master(client)
    first = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    client.put(
        f"/v1/resumes/{first['id']}",
        json={"typst_source": first["typst_source"] + "\n// tailor-only"},
    )
    second = client.post(
        f"/v1/resumes/{first['id']}/tailors",
        json={"title": "B", "fork_from": "current"},
    ).json()
    assert "tailor-only" in second["typst_source"]
    # The family stays one level deep even when the draft came from a sibling.
    assert second["parent_resume_id"] == master["id"]
    body = client.get(f"/v1/resumes/{second['id']}/family").json()
    assert body["master"]["id"] == master["id"]
    assert {row["id"] for row in body["tailors"]} == {first["id"], second["id"]}


def test_create_tailor_rejects_unknown_fork_source(client):
    master = _master(client)
    res = client.post(
        f"/v1/resumes/{master['id']}/tailors",
        json={"title": "B", "fork_from": "sibling"},
    )
    assert res.status_code == 422


def test_create_tailor_requires_title(client):
    master = _master(client)
    res = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "  "})
    assert res.status_code == 400


def test_family_lists_master_and_tailors(client):
    master = _master(client)
    a = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    body = client.get(f"/v1/resumes/{a['id']}/family").json()
    assert body["master"]["id"] == master["id"]
    assert body["master"]["kind"] == "master"
    assert [row["id"] for row in body["tailors"]] == [a["id"]]


def test_tailor_is_private_to_owner(client):
    master = _master(client)
    client.cookies.clear()
    res = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "X"})
    assert res.status_code == 404


def test_list_resumes_includes_kind_and_parent(client):
    master = _master(client)
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    rows = {row["id"]: row for row in client.get("/v1/resumes").json()}
    assert rows[master["id"]]["kind"] == "master"
    assert rows[tailor["id"]]["kind"] == "tailor"
    assert rows[tailor["id"]]["parent_resume_id"] == master["id"]


def test_claim_moves_master_and_tailors(client, db_session):
    master = _master(client)
    tailor = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "A"}).json()
    user = User(google_sub="sub-family", email="fam@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    assert client.post("/v1/auth/claim").json()["claimed"] == 2
    ids = {row["id"] for row in client.get("/v1/resumes").json()}
    assert {master["id"], tailor["id"]} <= ids
