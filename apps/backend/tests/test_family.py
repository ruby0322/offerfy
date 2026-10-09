from app.models import MemoryItem, Resume


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
