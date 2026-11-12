"""The status machine: every Application.status transition goes through here."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Application, Job, Resume, User
from app.services.jobs.applications import (
    GROUP_ORDER,
    STATUSES,
    confirm_applied,
    link_tailor,
    mark_applied,
    pipeline,
    revert_applied,
    save,
    set_status,
    unsave,
)


def _user(db_session, sub="sub-svc"):
    user = User(
        google_sub=sub, email=f"{sub}@example.com", locale="en", name="Mei-Ling Chen"
    )
    db_session.add(user)
    db_session.commit()
    return user


def _job(db_session, source_id="j-1", is_active=True):
    job = Job(
        source="greenhouse",
        source_id=source_id,
        company="Acme",
        title=f"Backend Engineer {source_id}",
        apply_url="https://example.com/apply",
        source_url="https://example.com/job",
        is_active=is_active,
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


def _count(db_session):
    return db_session.query(Application).count()


def test_save_creates_a_saved_row(db_session):
    u, j = _user(db_session), _job(db_session)
    row = save(db_session, user=u, job_id=j.id)
    assert row.status == "saved" and row.source == "search"
    assert row.user_id == u.id and row.job_id == j.id
    assert row.last_status_at is not None


def test_save_twice_returns_the_same_row(db_session):
    u, j = _user(db_session), _job(db_session)
    first = save(db_session, user=u, job_id=j.id, source="digest")
    again = save(db_session, user=u, job_id=j.id, source="search")
    assert again.id == first.id and again.source == "digest"
    assert _count(db_session) == 1


def test_saving_an_already_tailored_posting_does_not_move_it_backwards(db_session):
    u, j = _user(db_session), _job(db_session)
    t = _tailor(db_session, u)
    row = link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t.id)
    again = save(db_session, user=u, job_id=j.id)
    assert again.id == row.id and again.status == "tailored"


def test_unsave_removes_only_a_saved_row(db_session):
    u = _user(db_session)
    j1, j2 = _job(db_session, "a"), _job(db_session, "b")
    save(db_session, user=u, job_id=j1.id)
    mark_applied(db_session, user=u, job_id=j2.id)
    assert unsave(db_session, user=u, job_id=j1.id) is True
    assert unsave(db_session, user=u, job_id=j2.id) is False
    assert _count(db_session) == 1
    assert unsave(db_session, user=u, job_id=j1.id) is False  # already gone


def test_unsave_does_not_touch_another_users_row(db_session):
    u, other, j = _user(db_session), _user(db_session, "sub-other"), _job(db_session)
    save(db_session, user=other, job_id=j.id)
    assert unsave(db_session, user=u, job_id=j.id) is False
    assert _count(db_session) == 1


def test_link_tailor_advances_and_sets_the_resume(db_session):
    u, j = _user(db_session), _job(db_session)
    t = _tailor(db_session, u)
    save(db_session, user=u, job_id=j.id)
    row = link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t.id)
    assert row.status == "tailored" and row.tailor_resume_id == t.id
    assert _count(db_session) == 1


def test_link_tailor_is_idempotent_for_a_double_click(db_session):
    u, j = _user(db_session), _job(db_session)
    t = _tailor(db_session, u)
    a = link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t.id)
    b = link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t.id)
    assert a.id == b.id and b.status == "tailored"
    assert _count(db_session) == 1


def test_link_tailor_does_not_pull_an_applied_row_back(db_session):
    u, j = _user(db_session), _job(db_session)
    t = _tailor(db_session, u)
    mark_applied(db_session, user=u, job_id=j.id)
    row = link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t.id)
    assert row.status == "applied" and row.tailor_resume_id == t.id


def test_mark_applied_leaves_the_confirmation_owed(db_session):
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    assert row.status == "applied" and row.applied_at is not None
    assert row.applied_confirmed_at is None


def test_mark_applied_twice_keeps_the_first_stamp_and_confirmation(db_session):
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    confirm_applied(db_session, application=row)
    stamp, confirmed = row.applied_at, row.applied_confirmed_at
    again = mark_applied(db_session, user=u, job_id=j.id)
    assert again.applied_at == stamp and again.applied_confirmed_at == confirmed


def test_confirm_then_never_owed_again(db_session):
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    confirmed = confirm_applied(db_session, application=row)
    assert confirmed.applied_confirmed_at is not None
    first = confirmed.applied_confirmed_at
    again = confirm_applied(db_session, application=confirmed)
    assert again.applied_confirmed_at == first
    # a second outbound click must not reopen the question
    assert mark_applied(db_session, user=u, job_id=j.id).applied_confirmed_at == first


def test_revert_applied_after_a_tailor_returns_to_tailored_and_clears_both_stamps(
    db_session,
):
    u, j = _user(db_session), _job(db_session)
    t = _tailor(db_session, u)
    link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t.id)
    row = mark_applied(db_session, user=u, job_id=j.id)
    confirm_applied(db_session, application=row)
    out = revert_applied(db_session, application=row)
    assert out.status == "tailored"
    assert out.tailor_resume_id == t.id
    assert out.applied_at is None and out.applied_confirmed_at is None


def test_revert_applied_without_a_tailor_returns_to_saved_not_a_phantom_tailored(
    db_session,
):
    # The real path: click the outbound link without saving or tailoring (the
    # row is created at saved and advanced straight to applied), then answer No.
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    assert row.status == "applied" and row.tailor_resume_id is None
    out = revert_applied(db_session, application=row)
    assert out.status == "saved"
    assert out.tailor_resume_id is None
    assert out.applied_at is None and out.applied_confirmed_at is None
    # saved is deletable; a phantom tailored row would not be.
    assert unsave(db_session, user=u, job_id=j.id) is True


def test_set_status_moves_to_interviewing_and_closed(db_session):
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    assert set_status(db_session, application=row, status="interviewing").status == (
        "interviewing"
    )
    assert set_status(db_session, application=row, status="closed").status == "closed"


def test_set_status_rejects_an_unknown_value(db_session):
    u, j = _user(db_session), _job(db_session)
    row = save(db_session, user=u, job_id=j.id)
    with pytest.raises(ValueError):
        set_status(db_session, application=row, status="ghosted")
    assert row.status == "saved"


def test_confirming_does_not_move_last_status_at(db_session):
    # Confirming is not a status change, so last_status_at must not move.
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.last_status_at = old
    db_session.commit()
    confirm_applied(db_session, application=row)
    db_session.refresh(row)
    assert row.applied_confirmed_at is not None
    assert row.last_status_at.replace(tzinfo=timezone.utc) == old


def test_a_status_change_moves_last_status_at(db_session):
    u, j = _user(db_session), _job(db_session)
    row = save(db_session, user=u, job_id=j.id)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.last_status_at = old
    db_session.commit()
    set_status(db_session, application=row, status="interviewing")
    db_session.refresh(row)
    assert row.last_status_at.replace(tzinfo=timezone.utc) > old


def test_pipeline_orders_by_group_then_recency(db_session):
    u = _user(db_session)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    spec = [
        ("s-old", "saved", 5),
        ("i-1", "interviewing", 3),
        ("t-1", "tailored", 2),
        ("a-new", "applied", 1),
        ("a-old", "applied", 9),
        ("s-new", "saved", 0),
        ("c-1", "closed", 0),
    ]
    for sid, status, days in spec:
        j = _job(db_session, sid)
        db_session.add(
            Application(
                user_id=u.id,
                job_id=j.id,
                status=status,
                source="search",
                last_status_at=now - timedelta(days=days),
            )
        )
    db_session.commit()
    rows = pipeline(db_session, user=u)
    assert [r.job.source_id for r in rows] == [
        "i-1",
        "a-new",
        "a-old",
        "t-1",
        "s-new",
        "s-old",
        "c-1",
    ]
    assert GROUP_ORDER == ("interviewing", "applied", "tailored", "saved")
    assert set(GROUP_ORDER) | {"closed"} == set(STATUSES)


def test_pipeline_is_scoped_to_the_user(db_session):
    u, other, j = _user(db_session), _user(db_session, "sub-o"), _job(db_session)
    save(db_session, user=other, job_id=j.id)
    assert pipeline(db_session, user=u) == []


def test_pipeline_keeps_an_application_whose_posting_went_inactive(db_session):
    u = _user(db_session)
    j = _job(db_session, is_active=True)
    save(db_session, user=u, job_id=j.id)
    j.is_active = False
    db_session.commit()
    rows = pipeline(db_session, user=u)
    assert len(rows) == 1 and rows[0].job.is_active is False


@pytest.mark.parametrize("status", ["saved", "tailored", "applied", "ghosted"])
def test_set_status_accepts_only_the_self_reported_moves(db_session, status):
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    with pytest.raises(ValueError):
        set_status(db_session, application=row, status=status)
    assert row.status == "applied"


def test_a_notes_only_update_leaves_last_status_at(db_session):
    u, j = _user(db_session), _job(db_session)
    row = save(db_session, user=u, job_id=j.id)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.last_status_at = old
    db_session.commit()
    row.notes = "called the recruiter"
    db_session.commit()
    db_session.refresh(row)
    assert row.last_status_at.replace(tzinfo=timezone.utc) == old


def test_a_tailor_id_only_update_leaves_last_status_at(db_session):
    u, j = _user(db_session), _job(db_session)
    t1, t2 = _tailor(db_session, u), _tailor(db_session, u)
    row = link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t1.id)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row.last_status_at = old
    db_session.commit()
    link_tailor(db_session, user=u, job_id=j.id, tailor_resume_id=t2.id)
    db_session.refresh(row)
    assert row.tailor_resume_id == t2.id
    assert row.last_status_at.replace(tzinfo=timezone.utc) == old


@pytest.mark.parametrize("status", ["interviewing", "closed"])
def test_revert_applied_ignores_a_row_that_moved_on(db_session, status):
    u, j = _user(db_session), _job(db_session)
    row = mark_applied(db_session, user=u, job_id=j.id)
    set_status(db_session, application=row, status=status)
    out = revert_applied(db_session, application=row)
    assert out.status == status and out.applied_at is not None


def test_save_and_link_tailor_reject_an_unknown_source(db_session):
    u, j = _user(db_session), _job(db_session)
    t = _tailor(db_session, u)
    with pytest.raises(ValueError):
        save(db_session, user=u, job_id=j.id, source="bogus")
    with pytest.raises(ValueError):
        link_tailor(
            db_session, user=u, job_id=j.id, tailor_resume_id=t.id, source="bogus"
        )
    assert _count(db_session) == 0


def test_mark_applied_on_a_closed_row_changes_nothing(db_session):
    u, j = _user(db_session), _job(db_session)
    row = save(db_session, user=u, job_id=j.id)
    set_status(db_session, application=row, status="closed")
    out = mark_applied(db_session, user=u, job_id=j.id)
    assert out.status == "closed" and out.applied_at is None


def test_pipeline_breaks_timestamp_ties_by_id(db_session):
    u = _user(db_session)
    when = datetime(2026, 6, 1, tzinfo=timezone.utc)
    ids = []
    for sid in ("x", "y", "z"):
        j = _job(db_session, sid)
        a = Application(
            user_id=u.id, job_id=j.id, status="saved", source="search",
            last_status_at=when,
        )
        db_session.add(a)
        db_session.commit()
        ids.append(a.id)
    assert [r.id for r in pipeline(db_session, user=u)] == sorted(ids)


def test_confirm_applied_only_confirms_a_row_at_applied(db_session):
    u, j = _user(db_session), _job(db_session)
    row = save(db_session, user=u, job_id=j.id)
    out = confirm_applied(db_session, application=row)
    assert out.status == "saved" and out.applied_confirmed_at is None
    set_status(db_session, application=row, status="interviewing")
    assert confirm_applied(db_session, application=row).applied_confirmed_at is None
