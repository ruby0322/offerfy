"""digests / digest_items: the weekly digest, frozen once written."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.models import Digest, DigestItem, Job, User

PERIOD = datetime(2026, 11, 9, tzinfo=timezone.utc)
BUILT = datetime(2026, 11, 9, 6, 30, tzinfo=timezone.utc)
OPENED = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)


def _user(db_session, sub="sub-digest"):
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


def _digest(db_session, user, period=PERIOD, status="full"):
    digest = Digest(
        user_id=user.id,
        period_start=period,
        built_at=BUILT,
        model="claude-sonnet-5-5",
        input_hash="a" * 64,
        status=status,
        candidate_count=42,
    )
    db_session.add(digest)
    db_session.commit()
    return digest


def test_digest_round_trips(db_session):
    u = _user(db_session)
    j = _job(db_session)
    d = Digest(
        user_id=u.id,
        period_start=PERIOD,
        built_at=BUILT,
        model="claude-sonnet-5-5",
        input_hash="b" * 64,
        status="full",
        candidate_count=17,
        opened_at=OPENED,
    )
    db_session.add(d)
    db_session.flush()
    db_session.add(
        DigestItem(
            digest_id=d.id,
            job_id=j.id,
            rank=1,
            track="strong_fit",
            tier="strong",
            fit_score=0.82,
            reason="Your Go work matches.",
            gap="No Kubernetes.",
            requirements_met=0.9,
            seniority_fit=0.8,
            location_fit=1.0,
            direction_fit=0.7,
        )
    )
    db_session.flush()
    db_session.expire_all()

    row = db_session.query(Digest).filter_by(user_id=u.id).one()
    assert row.id and len(row.id) == 36
    assert row.period_start.replace(tzinfo=timezone.utc) == PERIOD
    assert row.built_at.replace(tzinfo=timezone.utc) == BUILT
    assert row.opened_at.replace(tzinfo=timezone.utc) == OPENED
    assert row.model == "claude-sonnet-5-5"
    assert row.input_hash == "b" * 64
    assert row.status == "full"
    assert row.candidate_count == 17

    item = db_session.query(DigestItem).filter_by(digest_id=row.id).one()
    assert item.id and len(item.id) == 36
    assert item.job_id == j.id
    assert item.rank == 1
    assert item.track == "strong_fit"
    assert item.tier == "strong"
    assert item.fit_score == pytest.approx(0.82)
    assert item.reason == "Your Go work matches."
    assert item.gap == "No Kubernetes."
    assert item.requirements_met == pytest.approx(0.9)
    assert item.seniority_fit == pytest.approx(0.8)
    assert item.location_fit == pytest.approx(1.0)
    assert item.direction_fit == pytest.approx(0.7)


def test_one_digest_per_user_and_period(db_session):
    u = _user(db_session, "sub-digest-dup")
    _digest(db_session, u)
    db_session.add(
        Digest(
            user_id=u.id, period_start=PERIOD, built_at=BUILT, model="m",
            input_hash="c" * 64, status="full", candidate_count=1,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_posting_can_appear_in_both_tracks(db_session):
    # The reason `track` is in the unique constraint: a fresh posting can be
    # both a strong fit and new this week.
    u = _user(db_session, "sub-digest-both")
    j = _job(db_session, "j-both")
    d = _digest(db_session, u)
    db_session.add(DigestItem(digest_id=d.id, job_id=j.id, rank=1, track="strong_fit"))
    db_session.add(DigestItem(digest_id=d.id, job_id=j.id, rank=1, track="new_this_week"))
    db_session.commit()
    assert db_session.query(DigestItem).filter_by(digest_id=d.id).count() == 2


def test_the_same_posting_twice_in_one_track_is_refused(db_session):
    u = _user(db_session, "sub-digest-twice")
    j = _job(db_session, "j-twice")
    d = _digest(db_session, u)
    db_session.add(DigestItem(digest_id=d.id, job_id=j.id, rank=1, track="strong_fit"))
    db_session.commit()
    db_session.add(DigestItem(digest_id=d.id, job_id=j.id, rank=2, track="strong_fit"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_deleting_the_digest_removes_its_items(db_session):
    u = _user(db_session, "sub-digest-del")
    j = _job(db_session, "j-del")
    d = _digest(db_session, u)
    digest_id = d.id
    db_session.add(DigestItem(digest_id=digest_id, job_id=j.id, rank=1, track="strong_fit"))
    db_session.commit()
    db_session.execute(delete(Digest).where(Digest.id == digest_id))
    db_session.commit()
    assert db_session.query(DigestItem).count() == 0


def test_deleting_the_user_removes_the_digest(db_session):
    u = _user(db_session, "sub-digest-user")
    j = _job(db_session, "j-user")
    d = _digest(db_session, u)
    db_session.add(DigestItem(digest_id=d.id, job_id=j.id, rank=1, track="strong_fit"))
    db_session.commit()
    db_session.execute(delete(User).where(User.id == u.id))
    db_session.commit()
    assert db_session.query(Digest).count() == 0
    assert db_session.query(DigestItem).count() == 0


def test_tier_and_score_are_nullable_for_a_degraded_digest(db_session):
    # A degraded digest has items with no tier, no score and no prose.
    u = _user(db_session, "sub-digest-degraded")
    j = _job(db_session, "j-degraded")
    d = _digest(db_session, u, status="degraded")
    db_session.add(DigestItem(digest_id=d.id, job_id=j.id, rank=1, track="new_this_week"))
    db_session.flush()
    db_session.expire_all()
    item = db_session.query(DigestItem).filter_by(digest_id=d.id).one()
    assert item.tier is None
    assert item.fit_score is None
    assert item.reason is None
    assert item.gap is None
    assert item.requirements_met is None
    assert item.seniority_fit is None
    assert item.location_fit is None
    assert item.direction_fit is None
    assert d.opened_at is None


def test_deleting_the_job_removes_its_digest_items(db_session):
    # digest_items.job_id CASCADE: ingest retires postings constantly, and an
    # item for a vanished posting has nothing to show.
    u = _user(db_session, "sub-digest-job")
    j = _job(db_session, "j-job")
    d = _digest(db_session, u)
    db_session.add(DigestItem(digest_id=d.id, job_id=j.id, rank=1, track="strong_fit"))
    db_session.commit()
    db_session.execute(delete(Job).where(Job.id == j.id))
    db_session.commit()
    assert db_session.query(DigestItem).count() == 0
    assert db_session.query(Digest).count() == 1
