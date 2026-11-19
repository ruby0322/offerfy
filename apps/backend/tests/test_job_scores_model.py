"""job_scores: one cached judgement per (person, posting), and the arrivals marker."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.models import Job, JobScore, User

SCORED = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)


def _user(db_session, sub="sub-scores"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en", name="Mei")
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


def _score(user, job, **kw):
    fields = dict(
        user_id=user.id,
        job_id=job.id,
        facts_input_hash="a" * 64,
        description_hash="b" * 64,
        model="claude-sonnet-5-5",
        outcome="scored",
        scored_at=SCORED,
        tier="strong",
        fit_score=0.8,
    )
    fields.update(kw)
    return JobScore(**fields)


def test_outcomes_vocabulary():
    assert JobScore.OUTCOMES == ("scored", "refused", "prose_rejected")


def test_one_row_per_user_and_posting(db_session):
    u, j = _user(db_session), _job(db_session)
    db_session.add(_score(u, j))
    db_session.commit()
    db_session.add(_score(u, j, fit_score=0.1))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_a_refused_pair_stores_no_score(db_session):
    u, j = _user(db_session), _job(db_session)
    db_session.add(_score(u, j, outcome="refused", tier=None, fit_score=None))
    db_session.commit()
    row = db_session.scalars(select(JobScore)).one()
    assert row.outcome == "refused"
    assert row.tier is None and row.fit_score is None
    assert row.reason is None and row.gap is None and row.prose_model is None
    assert row.requirements_met is None and row.direction_fit is None


def test_deleting_the_user_removes_their_scores(db_session):
    u, j = _user(db_session), _job(db_session)
    db_session.add(_score(u, j))
    db_session.commit()
    db_session.execute(delete(User).where(User.id == u.id))
    db_session.commit()
    assert db_session.scalars(select(JobScore)).all() == []


def test_deleting_the_posting_removes_its_scores(db_session):
    u, j = _user(db_session), _job(db_session)
    db_session.add(_score(u, j))
    db_session.commit()
    db_session.execute(delete(Job).where(Job.id == j.id))
    db_session.commit()
    assert db_session.scalars(select(JobScore)).all() == []


def test_matches_seen_at_defaults_to_null(db_session):
    # A fresh user has never looked; None is "first visit", not "no window".
    u = _user(db_session)
    db_session.refresh(u)
    assert u.matches_seen_at is None
