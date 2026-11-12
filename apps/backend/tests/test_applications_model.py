"""applications: one row per (person, posting), the spine of the dashboard."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from app.models import Application, Job, Resume, User


def _user(db_session, sub="sub-apps"):
    user = User(
        google_sub=sub, email=f"{sub}@example.com", locale="en", name="Mei-Ling Chen"
    )
    db_session.add(user)
    db_session.commit()
    return user


def _job(db_session, source_id="j-1"):
    job = Job(
        source="greenhouse",
        source_id=source_id,
        company="Acme",
        title="Backend Engineer",
        apply_url="https://example.com/apply",
        source_url="https://example.com/job",
    )
    db_session.add(job)
    db_session.commit()
    return job


def _tailor(db_session, user):
    master = Resume(user_id=user.id, typst_source="= Master", source="create")
    db_session.add(master)
    db_session.commit()
    tailor = Resume(
        user_id=user.id,
        typst_source="= Tailor",
        source="create",
        kind="tailor",
        parent_resume_id=master.id,
    )
    db_session.add(tailor)
    db_session.commit()
    return tailor


def _enforce_foreign_keys(db_session):
    # conftest leaves SQLite's FK enforcement off, and ON DELETE only bites
    # with it on. Turn it on for this test's connection only.
    db_session.commit()
    db_session.execute(text("PRAGMA foreign_keys=ON"))


def test_application_round_trips(db_session):
    u = _user(db_session)
    j = _job(db_session)
    t = _tailor(db_session, u)
    applied = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)
    confirmed = datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc)
    last = datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)
    db_session.add(
        Application(
            user_id=u.id,
            job_id=j.id,
            tailor_resume_id=t.id,
            status="applied",
            source="digest",
            applied_at=applied,
            applied_confirmed_at=confirmed,
            last_status_at=last,
            notes="Referred by Sam.",
        )
    )
    db_session.flush()
    db_session.expire_all()
    row = db_session.query(Application).filter_by(user_id=u.id).one()
    assert row.id and len(row.id) == 36
    assert row.job_id == j.id
    assert row.tailor_resume_id == t.id
    assert row.status == "applied"
    assert row.source == "digest"
    assert row.applied_at.replace(tzinfo=timezone.utc) == applied
    assert row.applied_confirmed_at.replace(tzinfo=timezone.utc) == confirmed
    assert row.last_status_at.replace(tzinfo=timezone.utc) == last
    assert row.notes == "Referred by Sam."
    assert row.created_at is not None


def test_optional_columns_default_to_none(db_session):
    u = _user(db_session, "sub-apps-min")
    j = _job(db_session, "j-min")
    db_session.add(Application(user_id=u.id, job_id=j.id, status="saved", source="search"))
    db_session.flush()
    db_session.expire_all()
    row = db_session.query(Application).filter_by(user_id=u.id).one()
    assert row.tailor_resume_id is None
    assert row.applied_at is None
    assert row.applied_confirmed_at is None
    assert row.notes is None
    assert row.last_status_at is not None  # defaulted, NOT NULL


def test_one_application_per_user_and_job(db_session):
    u = _user(db_session, "sub-apps-dup")
    j = _job(db_session, "j-dup")
    db_session.add(Application(user_id=u.id, job_id=j.id, status="saved", source="search"))
    db_session.commit()
    db_session.add(Application(user_id=u.id, job_id=j.id, status="applied", source="digest"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_deleting_the_tailor_keeps_the_application(db_session):
    # ON DELETE SET NULL: deleting the tailor must not erase the record that
    # you applied.
    u = _user(db_session, "sub-apps-tailor")
    j = _job(db_session, "j-tailor")
    t = _tailor(db_session, u)
    tailor_id = t.id
    db_session.add(
        Application(
            user_id=u.id, job_id=j.id, tailor_resume_id=tailor_id,
            status="applied", source="digest",
        )
    )
    _enforce_foreign_keys(db_session)
    db_session.execute(delete(Resume).where(Resume.id == tailor_id))
    db_session.commit()
    db_session.expire_all()
    row = db_session.query(Application).filter_by(user_id=u.id).one()
    assert row.tailor_resume_id is None
    assert row.status == "applied"


def test_deleting_the_user_removes_the_application(db_session):
    u = _user(db_session, "sub-apps-cascade")
    j = _job(db_session, "j-cascade")
    uid = u.id
    db_session.add(Application(user_id=uid, job_id=j.id, status="saved", source="search"))
    _enforce_foreign_keys(db_session)
    db_session.execute(delete(User).where(User.id == uid))
    db_session.commit()
    assert db_session.query(Application).filter_by(user_id=uid).count() == 0


def test_last_status_at_moves_on_update(db_session):
    u = _user(db_session, "sub-apps-upd")
    j = _job(db_session, "j-upd")
    old = datetime.now(timezone.utc) - timedelta(days=3)
    row = Application(
        user_id=u.id, job_id=j.id, status="saved", source="search", last_status_at=old
    )
    db_session.add(row)
    db_session.commit()
    row.status = "applied"
    db_session.commit()
    db_session.refresh(row)
    assert row.last_status_at.replace(tzinfo=timezone.utc) > old + timedelta(days=2)
