from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import Application, Job, User

from tests.test_applications_service import _job, _tailor


def _signed_in(client, db_session, sub="sub-apps"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _rows(db_session, user):
    db_session.expire_all()
    return db_session.query(Application).filter_by(user_id=user.id).all()


def test_every_route_requires_a_user(client):
    assert client.post("/v1/me/applications", json={"job_id": "x"}).status_code == 401
    assert client.get("/v1/me/applications/x").status_code == 401
    assert client.delete("/v1/me/applications/x").status_code == 401
    assert client.post("/v1/me/applications/x/applied").status_code == 401
    assert client.post("/v1/me/applications/x/confirm", json={"applied": True}).status_code == 401
    assert client.patch("/v1/me/applications/x", json={"notes": "n"}).status_code == 401


def test_save_is_idempotent_over_two_posts(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    first = client.post("/v1/me/applications", json={"job_id": job.id})
    second = client.post("/v1/me/applications", json={"job_id": job.id, "source": "digest"})
    assert (first.status_code, second.status_code) == (201, 201)
    assert len(_rows(db_session, user)) == 1
    body = first.json()
    assert body["status"] == "saved" and body["source"] == "search"
    assert body["job_title"] == job.title and body["company"] == "Acme"
    assert body["apply_url"] == job.apply_url and body["job_is_active"] is True
    assert body["applied_at"] is None and body["applied_confirmed_at"] is None
    assert second.json()["source"] == "search"  # untouched, not overwritten


def test_save_rejects_an_unknown_source_and_an_unknown_job(client, db_session):
    _signed_in(client, db_session)
    job = _job(db_session)
    assert client.post("/v1/me/applications", json={"job_id": job.id, "source": "x"}).status_code == 422
    res = client.post("/v1/me/applications", json={"job_id": "nope"})
    assert res.status_code == 422 and res.json()["detail"] == "Job not found"


def test_get_returns_the_row_or_404(client, db_session):
    _signed_in(client, db_session)
    job = _job(db_session)
    assert client.get(f"/v1/me/applications/{job.id}").status_code == 404
    client.post("/v1/me/applications", json={"job_id": job.id})
    res = client.get(f"/v1/me/applications/{job.id}")
    assert res.status_code == 200 and res.json()["job_id"] == job.id


def test_delete_removes_a_saved_row(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    assert client.delete(f"/v1/me/applications/{job.id}").status_code == 204
    assert _rows(db_session, user) == []


def test_delete_refuses_once_the_row_has_moved_on(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post(f"/v1/me/applications/{job.id}/applied")
    res = client.delete(f"/v1/me/applications/{job.id}")
    assert res.status_code == 409
    assert "applied" in res.json()["detail"]
    assert [r.status for r in _rows(db_session, user)] == ["applied"]


def test_delete_of_nothing_is_404(client, db_session):
    _signed_in(client, db_session)
    job = _job(db_session)
    res = client.delete(f"/v1/me/applications/{job.id}")
    assert res.status_code == 404 and res.json()["detail"] == "Application not found"


def test_applied_then_confirm_true_stamps_and_false_reverts(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    applied = client.post(f"/v1/me/applications/{job.id}/applied").json()
    assert applied["status"] == "applied" and applied["applied_at"]
    assert applied["applied_confirmed_at"] is None

    confirmed = client.post(
        f"/v1/me/applications/{job.id}/confirm", json={"applied": True}
    ).json()
    assert confirmed["status"] == "applied" and confirmed["applied_confirmed_at"]

    reverted = client.post(
        f"/v1/me/applications/{job.id}/confirm", json={"applied": False}
    ).json()
    # No tailor was ever linked, so the honest place to land is saved.
    assert reverted["status"] == "saved"
    assert reverted["applied_at"] is None and reverted["applied_confirmed_at"] is None
    assert [r.status for r in _rows(db_session, user)] == ["saved"]


def test_confirm_false_leaves_a_row_that_can_be_deleted(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post(f"/v1/me/applications/{job.id}/applied")
    res = client.post(f"/v1/me/applications/{job.id}/confirm", json={"applied": False})
    assert res.status_code == 200 and res.json()["status"] == "saved"
    assert client.delete(f"/v1/me/applications/{job.id}").status_code == 204
    assert _rows(db_session, user) == []


def test_confirm_false_on_a_row_that_is_not_applied_changes_nothing(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    client.patch(f"/v1/me/applications/{job.id}", json={"status": "interviewing"})
    before = client.get(f"/v1/me/applications/{job.id}").json()
    res = client.post(f"/v1/me/applications/{job.id}/confirm", json={"applied": False})
    assert res.status_code == 200
    assert res.json() == before
    assert before["status"] == "interviewing"
    assert [r.status for r in _rows(db_session, user)] == ["interviewing"]


def test_confirm_requires_a_boolean_and_an_existing_row(client, db_session):
    _signed_in(client, db_session)
    job = _job(db_session)
    assert client.post(f"/v1/me/applications/{job.id}/confirm", json={"applied": True}).status_code == 404
    assert client.post(f"/v1/me/applications/{job.id}/confirm", json={"applied": "no"}).status_code == 422


def test_patch_sets_a_self_reported_status(client, db_session):
    _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    res = client.patch(f"/v1/me/applications/{job.id}", json={"status": "closed"})
    assert res.status_code == 200 and res.json()["status"] == "closed"


def test_patch_rejects_a_status_outside_the_vocabulary(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    # Unknown, and in STATUSES but not something a person reports themselves.
    for bad in ("bogus", "saved", "applied", "tailored"):
        res = client.patch(f"/v1/me/applications/{job.id}", json={"status": bad})
        assert res.status_code == 422, bad
    assert [r.status for r in _rows(db_session, user)] == ["saved"]


def test_notes_edit_leaves_last_status_at_alone(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    row = _rows(db_session, user)[0]
    old = datetime.now(timezone.utc) - timedelta(days=9)
    row.last_status_at = old
    db_session.commit()

    res = client.patch(f"/v1/me/applications/{job.id}", json={"notes": "call Tuesday"})
    assert res.status_code == 200 and res.json()["notes"] == "call Tuesday"
    row = _rows(db_session, user)[0]
    assert row.notes == "call Tuesday"
    assert row.last_status_at.replace(tzinfo=timezone.utc) == old
    # Clearing notes is an explicit null, not an omission.
    assert client.patch(f"/v1/me/applications/{job.id}", json={"notes": None}).json()["notes"] is None


def test_another_users_application_is_404_not_403(client, db_session):
    owner = User(google_sub="owner", email="owner@example.com", locale="en")
    db_session.add(owner)
    db_session.commit()
    job = _job(db_session)
    row = Application(
        user_id=owner.id, job_id=job.id, status="applied", source="search",
        last_status_at=datetime.now(timezone.utc), notes="mine",
    )
    db_session.add(row)
    db_session.commit()

    _signed_in(client, db_session, sub="intruder")
    j = job.id
    responses = [
        client.get(f"/v1/me/applications/{j}"),
        client.delete(f"/v1/me/applications/{j}"),
        client.post(f"/v1/me/applications/{j}/confirm", json={"applied": False}),
        client.post(f"/v1/me/applications/{j}/confirm", json={"applied": True}),
        client.patch(f"/v1/me/applications/{j}", json={"status": "closed", "notes": "x"}),
    ]
    assert [r.status_code for r in responses] == [404] * 5
    assert {r.json()["detail"] for r in responses} == {"Application not found"}

    db_session.expire_all()
    after = db_session.get(Application, row.id)
    assert (after.status, after.notes) == ("applied", "mine")
    assert after.applied_confirmed_at is None and after.applied_at is None
    assert db_session.query(Application).count() == 1


def test_out_reports_an_inactive_posting_rather_than_hiding_it(client, db_session):
    _signed_in(client, db_session)
    job = _job(db_session, is_active=False)
    saved = client.post("/v1/me/applications", json={"job_id": job.id})
    assert saved.status_code == 201 and saved.json()["job_is_active"] is False
    assert client.get(f"/v1/me/applications/{job.id}").json()["job_is_active"] is False


def test_out_carries_the_tailor_resume_id(client, db_session):
    from app.services.jobs import applications as service

    user = _signed_in(client, db_session)
    job = _job(db_session)
    tailor = _tailor(db_session, user)
    service.link_tailor(db_session, user=user, job_id=job.id, tailor_resume_id=tailor.id)
    body = client.get(f"/v1/me/applications/{job.id}").json()
    assert body["status"] == "tailored" and body["tailor_resume_id"] == tailor.id


def test_applied_on_an_unknown_job_is_404(client, db_session):
    _signed_in(client, db_session)
    res = client.post("/v1/me/applications/nope/applied")
    assert res.status_code == 404 and res.json()["detail"] == "Job not found"


def test_confirm_true_on_a_row_that_is_not_applied_changes_nothing(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    before = client.get(f"/v1/me/applications/{job.id}").json()
    res = client.post(f"/v1/me/applications/{job.id}/confirm", json={"applied": True})
    assert res.status_code == 200 and res.json() == before
    assert res.json()["applied_confirmed_at"] is None
    assert _rows(db_session, user)[0].applied_confirmed_at is None


def test_patch_with_status_and_notes_applies_both_in_one_commit(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    commits = []
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    def record(session):
        # get_db commits once more at teardown with nothing pending; count only
        # the commits that carry a change.
        if session.dirty:
            commits.append(1)

    event.listen(Session, "before_commit", record)
    try:
        res = client.patch(
            f"/v1/me/applications/{job.id}",
            json={"status": "interviewing", "notes": "phone screen"},
        )
    finally:
        event.remove(Session, "before_commit", record)
    assert res.status_code == 200
    assert (res.json()["status"], res.json()["notes"]) == ("interviewing", "phone screen")
    row = _rows(db_session, user)[0]
    assert (row.status, row.notes) == ("interviewing", "phone screen")
    assert len(commits) == 1


def test_patch_notes_over_2000_chars_is_422(client, db_session):
    _signed_in(client, db_session)
    job = _job(db_session)
    client.post("/v1/me/applications", json={"job_id": job.id})
    assert client.patch(f"/v1/me/applications/{job.id}", json={"notes": "x" * 2001}).status_code == 422
    assert client.patch(f"/v1/me/applications/{job.id}", json={"notes": "x" * 2000}).status_code == 200


def test_patch_reports_a_reply_and_projects_the_stamp(client, db_session):
    user = _signed_in(client, db_session, "sub-replied")
    job = _job(db_session, "j-replied")
    client.post(f"/v1/me/applications/{job.id}/applied")

    heard = client.patch(
        f"/v1/me/applications/{job.id}", json={"status": "interviewing"}
    )
    assert heard.status_code == 200
    assert heard.json()["replied_at"] is not None

    # Closing afterwards keeps it: the status can no longer carry the fact.
    closed = client.patch(
        f"/v1/me/applications/{job.id}", json={"status": "closed", "replied": False}
    )
    assert closed.json()["status"] == "closed"
    assert closed.json()["replied_at"] == heard.json()["replied_at"]


def test_patch_closes_without_a_reply(client, db_session):
    user = _signed_in(client, db_session, "sub-ghosted")
    job = _job(db_session, "j-ghosted")
    client.post(f"/v1/me/applications/{job.id}/applied")
    body = client.patch(
        f"/v1/me/applications/{job.id}", json={"status": "closed", "replied": False}
    ).json()
    assert body["status"] == "closed" and body["replied_at"] is None


def test_patch_refuses_a_reply_with_no_status(client, db_session):
    # It would be accepted and silently dropped otherwise: set_status is the
    # only writer of the stamp, and no status means it is never called.
    _signed_in(client, db_session, "sub-bare-reply")
    job = _job(db_session, "j-bare-reply")
    client.post(f"/v1/me/applications/{job.id}/applied")
    assert (
        client.patch(
            f"/v1/me/applications/{job.id}", json={"replied": True}
        ).status_code
        == 422
    )


def test_patch_rejects_a_truthy_string_for_replied(client, db_session):
    _signed_in(client, db_session, "sub-strict-reply")
    job = _job(db_session, "j-strict-reply")
    client.post(f"/v1/me/applications/{job.id}/applied")
    assert (
        client.patch(
            f"/v1/me/applications/{job.id}",
            json={"status": "closed", "replied": "no"},
        ).status_code
        == 422
    )
