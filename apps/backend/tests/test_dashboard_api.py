from sqlalchemy import event

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import Application, JobPreference, Resume, User
from app.services.jobs import applications as service

from tests.test_applications_service import _job
from tests.test_talent_api import LIST, _bearer, _candidate, _key

TOP_LEVEL = {"preference", "match_count", "applications", "resume_health", "employer_views"}
HEALTH_KEYS = {
    "has_master", "published", "facts_state", "matching_revision_current", "needs_rebuild",
}


def _signed_in(client, db_session, sub="sub-dash"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def test_requires_a_user(client):
    assert client.get("/v1/dashboard").status_code == 401


def test_the_brand_new_person_gets_every_section_empty(client, db_session):
    _signed_in(client, db_session)
    res = client.get("/v1/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert body["preference"]["updated_at"] is None  # the transient seed
    assert body["preference"]["facts_state"] == "missing"
    assert body["preference"]["target_functions"] == []
    assert body["match_count"] == 0
    assert body["applications"] == []
    assert body["resume_health"]["has_master"] is False
    assert body["employer_views"] == {"disclosure_count": 0, "last_disclosed_at": None}


def test_it_returns_the_whole_view_in_one_request(client, db_session):
    _signed_in(client, db_session)
    body = client.get("/v1/dashboard").json()
    assert set(body) == TOP_LEVEL
    assert set(body["resume_health"]) == HEALTH_KEYS
    assert set(body["employer_views"]) == {"disclosure_count", "last_disclosed_at"}
    assert "preference_summary_parts" not in body


def test_applications_come_back_in_group_order(client, db_session):
    user = _signed_in(client, db_session)
    saved = _job(db_session, "j-saved")
    applied = _job(db_session, "j-applied")
    interviewing = _job(db_session, "j-int")
    service.save(db_session, user=user, job_id=saved.id)
    service.mark_applied(db_session, user=user, job_id=applied.id)
    service.save(db_session, user=user, job_id=interviewing.id)
    row = db_session.query(Application).filter_by(job_id=interviewing.id).one()
    service.set_status(db_session, application=row, status="interviewing")
    expected = [
        a.job_id for a in service.pipeline(db_session, user=user)
    ]
    got = client.get("/v1/dashboard").json()["applications"]
    assert [a["job_id"] for a in got] == expected
    assert len(got) == 3
    assert got[0]["status"] == "interviewing"
    assert got[0]["job_title"] == interviewing.title


def test_it_never_persists_a_preference_row(client, db_session):
    user = _signed_in(client, db_session)
    assert client.get("/v1/dashboard").status_code == 200
    db_session.expire_all()
    assert db_session.query(JobPreference).filter_by(user_id=user.id).count() == 0
    assert db_session.query(Resume).filter_by(user_id=user.id).count() == 0


def test_one_request_costs_a_flat_number_of_queries(client, db_session):
    user = _signed_in(client, db_session)
    counts = []
    for extra in (1, 5):
        for i in range(extra):
            job = _job(db_session, f"q-{extra}-{i}")
            service.save(db_session, user=user, job_id=job.id)
        statements = []

        def count(conn, cursor, statement, *a):
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            assert client.get("/v1/dashboard").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", count)
        counts.append(len(statements))
    # 1 application vs 6: no per-row query (the N+1 application_out had).
    assert counts[0] == counts[1], counts


def test_applications_are_unreachable_from_the_employer_path(client, db_session):
    candidate, _resume, profile = _candidate(db_session)
    job = _job(db_session, "secret-job")
    job.title = "ZZSENTINEL-TITLE"
    job.company = "ZZSENTINEL-CO"
    db_session.commit()
    service.save(db_session, user=candidate, job_id=job.id)
    service.mark_applied(db_session, user=candidate, job_id=job.id)
    row = db_session.query(Application).filter_by(user_id=candidate.id).one()
    row.notes = "ZZSENTINEL-NOTES"
    db_session.commit()
    _key_row, raw = _key(db_session)

    listing = client.get(LIST, headers=_bearer(raw))
    detail = client.get(f"{LIST}/{profile.id}", headers=_bearer(raw))
    assert listing.status_code == 200 and detail.status_code == 200
    # The consenting candidate is really being served, so absence is meaningful.
    assert "Chen Wei" in listing.text and "Chen Wei" in detail.text

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    for res in (listing, detail):
        assert "ZZSENTINEL" not in res.text
        assert job.id not in res.text and job.apply_url not in res.text
        names = {k.lower() for k in keys(res.json())}
        assert not {n for n in names if "applic" in n or "apply" in n or n == "notes"}
