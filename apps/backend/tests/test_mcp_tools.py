"""The tools behind POST /mcp.

Helpers here (`_raw_call`, `_call`, `_is_tool_error`, `_score`) are shared by
the tool tests of later tasks: keep their shapes stable.
"""

import json
from datetime import datetime, timezone

from app.models import JobScore
from app.services.agent_keys import mint_agent_key
from app.services.jobs_tools import catalogue_specs
from app.services.mcp_tools import specs


def _raw_call(client, db, user, name, args) -> dict:
    """Mint a key for `user`, post one tools/call, return the JSON-RPC response."""
    _, raw = mint_agent_key(db, user=user, label="t")
    db.commit()
    r = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _call(client, db, user, name, args) -> str:
    """As `_raw_call`, asserting the tool succeeded; returns its text content."""
    body = _raw_call(client, db, user, name, args)
    assert not _is_tool_error(body), body
    return body["result"]["content"][0]["text"]


def _is_tool_error(response: dict) -> bool:
    return response["result"].get("isError") is True


def _score(user, job, fit_score, outcome="scored") -> JobScore:
    return JobScore(
        user_id=user.id,
        job_id=job.id,
        facts_input_hash="f" * 64,
        description_hash="d" * 64,
        model="test-model",
        tier="strong" if outcome == "scored" else None,
        fit_score=fit_score,
        reason="because" if outcome == "scored" else None,
        gap="a gap" if outcome == "scored" else None,
        outcome=outcome,
        scored_at=datetime.now(timezone.utc),
    )


def test_search_jobs_finds_a_posting_by_keyword(client, db_session, a_user, a_job):
    assert a_job.title in _call(client, db_session, a_user, "search_jobs", {"q": a_job.title})


def test_read_job_returns_the_posting(client, db_session, a_user, a_job):
    assert a_job.company in _call(client, db_session, a_user, "read_job", {"job_id": a_job.id})


def test_read_job_on_an_unknown_id_is_a_tool_error(client, db_session, a_user):
    assert _is_tool_error(
        _raw_call(client, db_session, a_user, "read_job", {"job_id": "no-such-posting"})
    )


def test_the_mcp_job_specs_are_the_catalogue_specs(client):
    """One search_jobs in this codebase, not two."""
    listed = {s["name"]: s for s in specs()}
    for shared in catalogue_specs():
        assert listed[shared["name"]] == shared


def test_list_matches_returns_scored_rows_best_first(
    client, db_session, a_user, a_job, another_job
):
    db_session.add_all([_score(a_user, a_job, 0.4), _score(a_user, another_job, 0.9)])
    db_session.commit()
    rows = json.loads(_call(client, db_session, a_user, "list_matches", {}))["items"]
    assert [r["job_id"] for r in rows] == [another_job.id, a_job.id]
    assert rows[0]["fit_score"] == 0.9
    assert rows[0]["reason"] == "because"
    assert rows[0]["title"] == another_job.title


def test_list_matches_hides_refusals(client, db_session, a_user, a_job):
    db_session.add(_score(a_user, a_job, None, outcome="refused"))
    db_session.commit()
    assert json.loads(_call(client, db_session, a_user, "list_matches", {}))["items"] == []


def test_list_matches_never_shows_another_persons_scores(
    client, db_session, a_user, other_user, a_job
):
    db_session.add(_score(other_user, a_job, 0.9))
    db_session.commit()
    assert json.loads(_call(client, db_session, a_user, "list_matches", {}))["items"] == []


def test_list_matches_limit_is_capped(client, db_session, a_user, a_job, another_job):
    from app.services.jobs_tools import SEARCH_LIMIT_MAX

    db_session.add_all([_score(a_user, a_job, 0.4), _score(a_user, another_job, 0.9)])
    db_session.commit()
    one = json.loads(_call(client, db_session, a_user, "list_matches", {"limit": 1}))
    assert len(one["items"]) == 1
    assert one["items"][0]["job_id"] == another_job.id
    many = json.loads(_call(client, db_session, a_user, "list_matches", {"limit": 10**6}))
    assert len(many["items"]) <= SEARCH_LIMIT_MAX


def test_list_matches_hides_a_posting_that_has_closed(
    client, db_session, a_user, a_job, another_job
):
    another_job.is_active = False
    db_session.add_all([_score(a_user, a_job, 0.4), _score(a_user, another_job, 0.9)])
    db_session.commit()
    rows = json.loads(_call(client, db_session, a_user, "list_matches", {}))["items"]
    assert [r["job_id"] for r in rows] == [a_job.id]
