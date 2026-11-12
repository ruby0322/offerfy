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
