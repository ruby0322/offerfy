import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import JobPreference, LlmCall, ResumeFacts, TalentProfile, User
from app.services import profile as profile_service
from app.services.resume_facts import input_hash
from app.services.revisions import pin_current_draft, set_draft_source

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
            input_hash=input_hash(resume.published_revision_id, resume.typst_source, []),
            facts_json=json.dumps(payload),
        )
    )
    db_session.commit()


def test_get_seeds_on_first_call_without_persisting_it(client, db_session):
    user = _signed_in(client, db_session)
    _facts(db_session, user, roles=[{"title": "Staff Backend Engineer"}],
           locations=["Taipei, Taiwan"])
    body = client.get("/v1/me/job-preferences").json()
    assert body["target_functions"] == ["backend"]
    assert body["target_locations"] == ["tw"]
    # Computed, not stored: a row would mean "the user has edited this".
    assert db_session.query(JobPreference).filter_by(user_id=user.id).count() == 0


def test_seed_follows_the_facts_until_the_first_save(client, db_session):
    user = _signed_in(client, db_session)
    assert client.get("/v1/me/job-preferences").json()["target_functions"] == []
    _facts(db_session, user, roles=[{"title": "Backend Engineer"}])
    assert client.get("/v1/me/job-preferences").json()["target_functions"] == ["backend"]
    client.put("/v1/me/job-preferences", json={"target_functions": ["design"]})
    row = db_session.query(ResumeFacts).filter_by(user_id=user.id).one()
    row.facts_json = json.dumps({"roles": [{"title": "Product Manager"}], "locations": []})
    db_session.commit()
    assert client.get("/v1/me/job-preferences").json()["target_functions"] == ["design"]


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
    before = first.updated_at
    client.put("/v1/me/job-preferences", json={"target_functions": ["design"]})
    db_session.expire_all()
    rows = db_session.query(JobPreference).filter_by(user_id=user.id).all()
    assert [r.id for r in rows] == [row_id]
    assert rows[0].target_functions == "|design|"
    assert rows[0].updated_at > before


def test_get_reports_facts_state(client, db_session):
    user = _signed_in(client, db_session)
    assert client.get("/v1/me/job-preferences").json()["facts_state"] == "missing"
    _facts(db_session, user, roles=[{"title": "Backend Engineer"}])
    assert client.get("/v1/me/job-preferences").json()["facts_state"] == "fresh"


def test_refresh_builds_facts_for_a_user_without_employer_consent(
    client, db_session, monkeypatch, openai_enabled
):
    user = _signed_in(client, db_session)
    _resume(db_session, user)
    assert user.profile_resume_id is None   # no pointer: the whole point
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


def test_put_is_a_replacement_so_every_omitted_field_is_cleared(client, db_session):
    _signed_in(client, db_session)
    full = {
        "target_functions": ["backend"],
        "target_locations": ["tw"],
        "target_seniority": "senior",
        "remote_required": True,
        "activity": "paused",
    }
    got = client.put("/v1/me/job-preferences", json=full).json()
    assert {k: got[k] for k in full} == full
    got = client.put("/v1/me/job-preferences", json={}).json()
    assert got["target_functions"] == []
    assert got["target_locations"] == []
    assert got["target_seniority"] is None
    assert got["remote_required"] is None
    assert got["activity"] == "actively_looking"


def test_one_users_preferences_are_invisible_to_another(client, db_session):
    _signed_in(client, db_session, sub="sub-a")
    client.put("/v1/me/job-preferences", json={"target_functions": ["design"]})
    _signed_in(client, db_session, sub="sub-b")
    assert client.get("/v1/me/job-preferences").json()["target_functions"] == []


def test_endpoints_require_sign_in(client):
    assert client.get("/v1/me/job-preferences").status_code == 401
    assert client.put("/v1/me/job-preferences", json={}).status_code == 401
    assert client.post("/v1/me/resume-facts/refresh").status_code == 401


def _ready(client, db_session, monkeypatch, sub):
    user = _signed_in(client, db_session, sub=sub)
    resume = _resume(db_session, user)
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    return user, resume


def test_refresh_with_no_pointer_then_republish_reads_stale(
    client, db_session, monkeypatch, openai_enabled
):
    user, resume = _ready(client, db_session, monkeypatch, "sub-stale")
    assert user.profile_resume_id is None
    assert client.post("/v1/me/resume-facts/refresh").json()["facts_state"] == "fresh"
    set_draft_source(db_session, resume, "= CV\nChen Wei\nNew job")
    pin_current_draft(db_session, resume)
    db_session.commit()
    assert client.get("/v1/me/job-preferences").json()["facts_state"] == "stale"


def test_employer_path_still_reports_no_source_without_a_pointer(
    client, db_session, monkeypatch, openai_enabled
):
    user, _ = _ready(client, db_session, monkeypatch, "sub-employer")
    client.put("/v1/me/consents/talent_introduction", json={"granted": True})
    result = asyncio.run(profile_service.build_profile(db_session, user))
    assert result.profile is None
    assert result.error == "no_source"


def test_a_pointer_to_an_unpublished_resume_is_no_source_not_a_fallback(
    client, db_session, monkeypatch, openai_enabled
):
    user, _ = _ready(client, db_session, monkeypatch, "sub-unpub")
    draft = _resume(db_session, user, publish=False, title="Draft")
    user.profile_resume_id = draft.id
    db_session.commit()
    r = client.post("/v1/me/resume-facts/refresh").json()
    assert r["error"] == "no_source"
    assert r["facts_state"] == "missing"


def test_a_later_edited_second_master_does_not_flip_the_source(
    client, db_session, monkeypatch, openai_enabled
):
    user, first = _ready(client, db_session, monkeypatch, "sub-sticky")
    client.post("/v1/me/resume-facts/refresh")
    second = _resume(db_session, user, source="= Other\nCV", title="Second")
    # Edit the second master later; Resume.updated_at moves, nothing publishes.
    second.typst_source = "= Other\nedited"
    second.updated_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.commit()
    facts = db_session.query(ResumeFacts).filter_by(user_id=user.id).one()
    assert facts.source_resume_id == first.id
    assert client.get("/v1/me/job-preferences").json()["facts_state"] == "fresh"


def test_a_tailor_is_never_the_default_source(
    client, db_session, monkeypatch, openai_enabled
):
    from app.services.resume_facts import default_source

    user, master = _ready(client, db_session, monkeypatch, "sub-tailor")
    tailor = _resume(db_session, user, title="Tailor")
    tailor.kind = "tailor"
    tailor.parent_resume_id = master.id
    db_session.commit()
    assert default_source(db_session, user).id == master.id


def test_a_failed_build_still_records_spend(
    client, db_session, monkeypatch, openai_enabled
):
    user = _signed_in(client, db_session, sub="sub-fail")
    _resume(db_session, user)
    _stub(monkeypatch, StubProfileLlm(status_code=500))
    r = client.post("/v1/me/resume-facts/refresh").json()
    assert r["error"] == "provider_error"
    assert r["facts_state"] == "missing"
    assert db_session.query(LlmCall).count() == 1


def _posting(db_session, n, function="backend", country="TW"):
    from datetime import timedelta
    from app.models import Job, JobFacets
    from app.services.jobs.store import freshness_cutoff

    job = Job(source="greenhouse", source_id=f"cnt-{n}", company="Acme", title="Engineer",
              apply_url="https://x/a", source_url="https://x/s",
              last_seen_at=freshness_cutoff() + timedelta(days=3), is_active=True)
    db_session.add(job)
    db_session.flush()
    db_session.add(JobFacets(job_id=job.id, country=country, seniority="mid",
                             function=function, description_hash="h"))
    db_session.commit()


def test_match_count_is_zero_for_a_user_with_no_preferences(client, db_session):
    _signed_in(client, db_session, sub="sub-count0")
    _posting(db_session, 0)
    assert client.get("/v1/me/matches/count").json() == {"count": 0}


def test_match_count_reflects_the_prefilter(client, db_session):
    user = _signed_in(client, db_session, sub="sub-count3")
    for i in range(3):
        _posting(db_session, 100 + i)
    _posting(db_session, 200, function="design")
    _posting(db_session, 201, country="JP")
    client.put("/v1/me/job-preferences",
               json={"target_functions": ["backend"], "target_locations": ["tw"]})
    assert client.get("/v1/me/matches/count").json() == {"count": 3}


def test_match_count_counts_a_seed_that_was_never_saved(client, db_session):
    user = _signed_in(client, db_session, sub="sub-countseed")
    _facts(db_session, user, roles=[{"title": "Backend Engineer"}], locations=["Taipei, Taiwan"])
    for i in range(2):
        _posting(db_session, 300 + i)
    assert client.get("/v1/me/matches/count").json() == {"count": 2}
    assert db_session.query(JobPreference).filter_by(user_id=user.id).count() == 0


def test_match_count_requires_sign_in(client):
    assert client.get("/v1/me/matches/count").status_code == 401
