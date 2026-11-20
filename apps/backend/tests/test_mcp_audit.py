from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models import AgentToolCall, Resume
from app.services.agent_keys import mint_agent_key
from app.services.mcp_audit import QUOTA_PER_HOUR, audited


def test_a_successful_call_is_recorded(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with audited(key, "list_resumes"):
        pass
    rows = db_session.query(AgentToolCall).all()
    assert [(r.tool, r.outcome) for r in rows] == [("list_resumes", "ok")]


def test_a_refusal_is_still_recorded(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with pytest.raises(HTTPException):
        with audited(key, "get_resume"):
            raise HTTPException(status_code=404, detail="Resume not found")
    rows = db_session.query(AgentToolCall).all()
    assert [(r.tool, r.outcome) for r in rows] == [("get_resume", "refused")]


def test_any_other_exception_is_recorded_as_an_error(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with pytest.raises(RuntimeError):
        with audited(key, "get_resume"):
            raise RuntimeError("boom")
    rows = db_session.query(AgentToolCall).all()
    assert [(r.tool, r.outcome) for r in rows] == [("get_resume", "error")]


def test_the_resume_id_is_read_off_the_handle(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with pytest.raises(HTTPException):
        with audited(key, "get_resume") as call:
            call.resume_id = "r-123"
            raise HTTPException(status_code=404, detail="Resume not found")
    row = db_session.query(AgentToolCall).one()
    assert row.resume_id == "r-123"
    assert row.user_id == a_user.id
    assert row.key_id == key.id


def test_the_record_survives_a_rolled_back_request_session(client, db_session, a_user):
    """Review Focus: get_db rolls back on any exception, so the audit must not
    ride the request's transaction."""
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with pytest.raises(HTTPException):
        with audited(key, "edit_resume"):
            db_session.add(Resume(title="never committed", locale="en",
                                  source="create", typst_source="", user_id=a_user.id))
            raise HTTPException(status_code=409, detail="stale_hash")
    db_session.rollback()
    assert db_session.query(AgentToolCall).count() == 1
    assert db_session.query(Resume).filter(Resume.title == "never committed").count() == 0


def test_last_used_at_is_stamped(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    assert key.last_used_at is None
    with audited(key, "list_resumes"):
        pass
    db_session.refresh(key)
    assert key.last_used_at is not None


def test_the_quota_refuses_the_call_before_it_runs(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    for _ in range(QUOTA_PER_HOUR):
        with audited(key, "search_jobs"):
            pass
    ran = False
    with pytest.raises(HTTPException) as caught:
        with audited(key, "search_jobs"):
            ran = True
    assert caught.value.status_code == 429
    assert ran is False


def test_calls_older_than_an_hour_do_not_count(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    for _ in range(QUOTA_PER_HOUR):
        db_session.add(AgentToolCall(key_id=key.id, user_id=a_user.id,
                                     tool="search_jobs", outcome="ok", created_at=stale))
    db_session.commit()
    with audited(key, "search_jobs"):
        pass  # must not raise
