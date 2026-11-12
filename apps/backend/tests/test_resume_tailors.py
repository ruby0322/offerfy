from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import Application, Resume, User

from tests.test_applications_service import _job


def _master(client):
    return client.post("/v1/resumes", json={"locale": "en", "title": "Master"}).json()


def _signed_in(client, db_session, sub="sub-tailors"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _applications(db_session):
    db_session.expire_all()
    return db_session.query(Application).all()


def _tailors(db_session):
    db_session.expire_all()
    return db_session.query(Resume).filter_by(kind="tailor").all()


def test_a_tailor_with_a_job_id_writes_the_application(client, db_session):
    user = _signed_in(client, db_session)
    job = _job(db_session)
    master = _master(client)
    res = client.post(
        f"/v1/resumes/{master['id']}/tailors", json={"title": "T", "job_id": job.id}
    )
    assert res.status_code == 201
    [row] = _applications(db_session)
    assert (row.user_id, row.job_id) == (user.id, job.id)
    assert row.status == "tailored"
    assert row.tailor_resume_id == res.json()["id"]


def test_a_tailor_without_a_job_id_writes_no_application(client, db_session):
    _signed_in(client, db_session)
    master = _master(client)
    res = client.post(f"/v1/resumes/{master['id']}/tailors", json={"title": "T"})
    assert res.status_code == 201
    assert _applications(db_session) == []


def test_a_guest_tailor_with_a_job_id_still_creates_the_tailor(client, db_session):
    # applications.user_id is NOT NULL: a guest has nowhere to hold the
    # linkage. The tailor is the point of the request; the linkage is not.
    job = _job(db_session)
    master = _master(client)  # no session cookie: an anonymous guest
    res = client.post(
        f"/v1/resumes/{master['id']}/tailors", json={"title": "T", "job_id": job.id}
    )
    assert res.status_code == 201
    assert len(_tailors(db_session)) == 1
    assert _applications(db_session) == []


def test_an_unknown_job_id_is_422_and_creates_no_tailor(client, db_session):
    _signed_in(client, db_session)
    master = _master(client)
    res = client.post(
        f"/v1/resumes/{master['id']}/tailors", json={"title": "T", "job_id": "nope"}
    )
    assert res.status_code == 422 and res.json()["detail"] == "Job not found"
    assert _tailors(db_session) == []
    assert _applications(db_session) == []


def test_tailoring_a_posting_already_applied_to_does_not_move_it_backwards(
    client, db_session
):
    from app.services.jobs.applications import mark_applied

    user = _signed_in(client, db_session)
    job = _job(db_session)
    mark_applied(db_session, user=user, job_id=job.id)
    master = _master(client)
    res = client.post(
        f"/v1/resumes/{master['id']}/tailors", json={"title": "T", "job_id": job.id}
    )
    assert res.status_code == 201
    [row] = _applications(db_session)
    assert row.status == "applied"
    assert row.tailor_resume_id == res.json()["id"]
