"""job_preferences: the user-written half of a match profile."""

from app.models import JobPreference, User


def _user(db_session, sub="sub-prefs"):
    user = User(
        google_sub=sub, email=f"{sub}@example.com", locale="en", name="Mei-Ling Chen"
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_job_preference_round_trips_pipe_delimited_fields(db_session):
    u = _user(db_session)
    db_session.add(
        JobPreference(
            user_id=u.id,
            target_functions="|backend|infra|",
            target_locations="|tw|us|",
            target_seniority="senior",
            remote_required=None,
            activity="actively_looking",
        )
    )
    db_session.flush()
    db_session.expire_all()
    row = db_session.query(JobPreference).filter_by(user_id=u.id).one()
    assert row.target_functions == "|backend|infra|"
    assert row.target_locations == "|tw|us|"
    assert row.target_seniority == "senior"
    assert row.remote_required is None  # tri-state survives the round trip
    assert row.activity == "actively_looking"
    assert row.updated_at is not None


def test_remote_required_keeps_false_distinct_from_none(db_session):
    u = _user(db_session, "sub-prefs-2")
    db_session.add(JobPreference(user_id=u.id, remote_required=False))
    db_session.flush()
    db_session.expire_all()
    row = db_session.query(JobPreference).filter_by(user_id=u.id).one()
    assert row.remote_required is False
    assert row.activity == "actively_looking"
    assert row.target_functions == ""


# --- Task 5: seeding from resume facts through the shared taxonomy ---------

import json  # noqa: E402

from app.models import ResumeFacts  # noqa: E402
from app.services.jobs.enrich import default_taxonomy  # noqa: E402
from app.services.jobs.preferences import get_or_seed, seed_from_facts  # noqa: E402


def _facts(roles=None, locations=None, years_experience=None):
    payload = {"roles": roles or [], "locations": locations or []}
    return ResumeFacts(
        user_id="u",
        source_resume_id="r",
        source_revision_id="rev",
        input_hash="h" * 64,
        facts_json=json.dumps(payload),
        years_experience=years_experience,
    )


def test_seed_derives_function_and_seniority_from_the_latest_role():
    facts = _facts(
        roles=[{"title": "Staff Backend Engineer", "employer": "X"}],
        locations=["Taipei, Taiwan"],
        years_experience=9,
    )
    seed = seed_from_facts(facts)
    assert seed["target_functions"] == "|backend|"
    assert seed["target_seniority"] == "staff_plus"
    assert seed["target_locations"] == "|tw|"


def test_seed_uses_the_job_taxonomy_not_a_second_vocabulary():
    valid = {r.function for r in default_taxonomy().function_rules} | {"other"}
    seed = seed_from_facts(_facts(roles=[{"title": "Senior Data Scientist"}]))
    assert set(filter(None, seed["target_functions"].split("|"))) <= valid


def test_seed_from_no_facts_is_empty_not_an_error():
    seed = seed_from_facts(None)
    assert seed["target_functions"] == "" and seed["target_seniority"] is None
    assert seed["target_locations"] == ""
    assert seed["activity"] == "actively_looking"


def test_seed_from_facts_with_no_roles_is_empty():
    seed = seed_from_facts(_facts(roles=[]))
    assert seed["target_functions"] == "" and seed["target_seniority"] is None


def test_seed_falls_back_to_years_band_when_title_is_unknown():
    seed = seed_from_facts(_facts(roles=[{"title": "Wizard"}], years_experience=12))
    assert seed["target_seniority"] == "senior"
    seed = seed_from_facts(_facts(roles=[{"title": "Wizard"}], years_experience=1))
    assert seed["target_seniority"] == "junior"


def test_get_or_seed_never_overwrites_a_users_own_row(db_session):
    u = _user(db_session, "sub-prefs-seed-1")
    db_session.add(
        JobPreference(user_id=u.id, target_functions="|design|", activity="paused")
    )
    db_session.flush()
    got = get_or_seed(db_session, u)
    assert got.target_functions == "|design|" and got.activity == "paused"


def test_get_or_seed_creates_a_row_with_no_facts(db_session):
    u = _user(db_session, "sub-prefs-seed-2")
    got = get_or_seed(db_session, u)
    assert got.id is not None and got.target_functions == ""
    assert get_or_seed(db_session, u).id == got.id
