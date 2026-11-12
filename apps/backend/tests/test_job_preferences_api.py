import json

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import JobPreference, ResumeFacts, TalentProfile, User

from tests.test_talent_profile import StubProfileLlm, _profile, _resume, _said, _stub


def _signed_in(client, db_session, sub="sub-prefs"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _facts(db_session, user, roles=None, locations=None):
    resume = _resume(db_session, user, title=f"CV {user.id[:6]}")
    payload = {"roles": roles or [], "locations": locations or []}
    db_session.add(
        ResumeFacts(
            user_id=user.id,
            source_resume_id=resume.id,
            source_revision_id=resume.published_revision_id,
            input_hash="h" * 64,
            facts_json=json.dumps(payload),
        )
    )
    db_session.commit()


def test_get_seeds_on_first_call_and_persists_it(client, db_session):
    user = _signed_in(client, db_session)
    _facts(db_session, user, roles=[{"title": "Staff Backend Engineer"}],
           locations=["Taipei, Taiwan"])
    body = client.get("/v1/me/job-preferences").json()
    assert body["target_functions"] == ["backend"]
    assert body["target_locations"] == ["tw"]
    assert db_session.query(JobPreference).filter_by(user_id=user.id).count() == 1


def test_get_for_a_user_with_no_resume_returns_empty_not_500(client, db_session):
    _signed_in(client, db_session)
    r = client.get("/v1/me/job-preferences")
    assert r.status_code == 200
    assert r.json()["target_functions"] == []
    assert r.json()["target_seniority"] is None


def test_put_rejects_a_function_outside_the_taxonomy(client, db_session):
    _signed_in(client, db_session)
    r = client.put("/v1/me/job-preferences", json={"target_functions": ["astrology"]})
    assert r.status_code == 422


def test_put_rejects_bad_seniority_location_and_activity(client, db_session):
    _signed_in(client, db_session)
    assert client.put("/v1/me/job-preferences", json={"target_seniority": "wizard"}).status_code == 422
    assert client.put("/v1/me/job-preferences", json={"target_locations": ["atlantis"]}).status_code == 422
    assert client.put("/v1/me/job-preferences", json={"activity": "sleeping"}).status_code == 422


def test_put_returns_lists_not_pipe_text(client, db_session):
    _signed_in(client, db_session)
    got = client.put(
        "/v1/me/job-preferences",
        json={"target_functions": ["backend", "design"], "target_locations": ["TW"]},
    ).json()
    assert got["target_functions"] == ["backend", "design"]
    assert got["target_locations"] == ["tw"]


def test_put_round_trips_the_tri_state_remote_flag(client, db_session):
    _signed_in(client, db_session)
    for value in (True, False, None):
        got = client.put("/v1/me/job-preferences", json={"remote_required": value}).json()
        assert got["remote_required"] is value


def test_put_mutates_in_place_so_the_row_survives(client, db_session):
    user = _signed_in(client, db_session)
    client.put("/v1/me/job-preferences", json={"target_functions": ["backend"]})
    first = db_session.query(JobPreference).filter_by(user_id=user.id).one()
    row_id = first.id
    client.put("/v1/me/job-preferences", json={"target_functions": ["design"]})
    db_session.expire_all()
    rows = db_session.query(JobPreference).filter_by(user_id=user.id).all()
    assert [r.id for r in rows] == [row_id]
    assert rows[0].target_functions == "|design|"


def test_get_reports_facts_state(client, db_session):
    user = _signed_in(client, db_session)
    assert client.get("/v1/me/job-preferences").json()["facts_state"] == "missing"
    _facts(db_session, user, roles=[{"title": "Backend Engineer"}])
    assert client.get("/v1/me/job-preferences").json()["facts_state"] == "fresh"


def test_refresh_builds_facts_for_a_user_without_employer_consent(
    client, db_session, monkeypatch, openai_enabled
):
    user = _signed_in(client, db_session)
    resume = _resume(db_session, user)
    # The source resume is the one the person pointed at; refresh builds from
    # it, and does not choose one for them.
    user.profile_resume_id = resume.id
    db_session.commit()
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    r = client.post("/v1/me/resume-facts/refresh")
    assert r.status_code == 200
    assert r.json() == {"facts_state": "fresh", "error": None}
    assert db_session.query(ResumeFacts).filter_by(user_id=user.id).count() == 1
    assert db_session.query(TalentProfile).filter_by(user_id=user.id).count() == 0


def test_refresh_without_a_published_resume_is_not_a_500(client, db_session):
    _signed_in(client, db_session)
    r = client.post("/v1/me/resume-facts/refresh")
    assert r.status_code == 200
    assert r.json()["facts_state"] == "missing"
    assert r.json()["error"]


def test_put_is_a_replacement_so_an_omitted_field_is_cleared(client, db_session):
    _signed_in(client, db_session)
    client.put("/v1/me/job-preferences", json={"target_functions": ["backend"]})
    assert client.put("/v1/me/job-preferences", json={}).json()["target_functions"] == []


def test_one_users_preferences_are_invisible_to_another(client, db_session):
    _signed_in(client, db_session, sub="sub-a")
    client.put("/v1/me/job-preferences", json={"target_functions": ["design"]})
    _signed_in(client, db_session, sub="sub-b")
    assert client.get("/v1/me/job-preferences").json()["target_functions"] == []


def test_endpoints_require_sign_in(client):
    assert client.get("/v1/me/job-preferences").status_code == 401
    assert client.put("/v1/me/job-preferences", json={}).status_code == 401
    assert client.post("/v1/me/resume-facts/refresh").status_code == 401
