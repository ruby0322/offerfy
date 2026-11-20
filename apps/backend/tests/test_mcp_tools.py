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


# --- resume reads -----------------------------------------------------------------


def test_get_resume_returns_the_projection_and_a_hash(
    client, db_session, a_user, my_resume
):
    from app.services.revisions import source_hash

    out = json.loads(
        _call(client, db_session, a_user, "get_resume", {"resume_id": my_resume.id})
    )
    assert out["hash"] == source_hash(my_resume.typst_source)
    assert out["projection"]["units"]
    assert out["projection"]["hash"] == out["hash"]
    assert out["resume_id"] == my_resume.id
    assert out["title"] == my_resume.title
    assert out["locale"] == "zh-TW"
    assert out["kind"] == "master"
    assert out["parent_resume_id"] is None
    assert out["target_label"] is None


def test_get_resume_reports_a_tailors_parent_and_label(
    client, db_session, a_user, my_resume
):
    from app.models import Resume

    tailor = Resume(
        title="For Acme", typst_source=my_resume.typst_source, source="create",
        locale="zh-TW", user_id=a_user.id, kind="tailor",
        parent_resume_id=my_resume.id, target_label="Acme",
    )
    db_session.add(tailor)
    db_session.commit()
    out = json.loads(
        _call(client, db_session, a_user, "get_resume", {"resume_id": tailor.id})
    )
    assert (out["kind"], out["parent_resume_id"], out["target_label"]) == (
        "tailor", my_resume.id, "Acme",
    )


def test_get_resume_without_an_id_is_a_tool_error(client, db_session, a_user):
    assert _is_tool_error(_raw_call(client, db_session, a_user, "get_resume", {}))


def test_an_unknown_resume_id_is_not_found(client, db_session, a_user):
    assert _is_tool_error(
        _raw_call(client, db_session, a_user, "get_resume", {"resume_id": "nope"})
    )


def test_another_users_resume_is_not_found(client, db_session, a_user, other_user):
    from tests.conftest import _resume_for

    theirs = _resume_for(db_session, other_user)
    assert _is_tool_error(
        _raw_call(client, db_session, a_user, "get_resume", {"resume_id": theirs.id})
    )


def _guest_resume(db_session, key="g"):
    from app.models import GuestSession, Resume
    from app.services.guest import hash_guest_key

    guest = GuestSession(key_hash=hash_guest_key(key))
    db_session.add(guest)
    db_session.flush()
    orphan = Resume(title="Guest draft", locale="en", source="create",
                    typst_source="= x", guest_session_id=guest.id)
    db_session.add(orphan)
    db_session.commit()
    return orphan


def test_a_guest_owned_resume_is_not_found(client, db_session, a_user):
    """An agent must not reach a resume that belongs to a guest session, even
    though no user owns it. PINS ALREADY-CORRECT BEHAVIOUR: no earlier code read
    guest rows."""
    orphan = _guest_resume(db_session)
    assert _is_tool_error(
        _raw_call(client, db_session, a_user, "get_resume", {"resume_id": orphan.id})
    )


def test_not_found_is_identical_for_missing_foreign_and_guest(
    db_session, a_user, other_user
):
    """404 never 403, and the same detail for all three, so the response does
    not reveal which case it was."""
    import pytest
    from fastapi import HTTPException

    from app.services import resume_access
    from tests.conftest import _resume_for

    theirs = _resume_for(db_session, other_user)
    orphan = _guest_resume(db_session, "g2")
    seen = set()
    for rid in ("missing-id", theirs.id, orphan.id):
        with pytest.raises(HTTPException) as e:
            resume_access.owned_by_user(db_session, a_user, rid)
        seen.add((e.value.status_code, e.value.detail))
    assert seen == {(404, "Resume not found")}


def test_owned_by_user_returns_the_callers_own_row(db_session, a_user, my_resume):
    from app.services import resume_access

    got = resume_access.owned_by_user(db_session, a_user, my_resume.id)
    assert got.id == my_resume.id


def test_create_for_user_sets_the_user_and_never_a_guest(db_session, a_user):
    from app.services import resume_access

    r = resume_access.create_for_user(
        db_session, a_user, title="T", locale="en", source="create", typst_source="= T"
    )
    db_session.commit()
    assert r.user_id == a_user.id
    assert r.guest_session_id is None
    assert r.typst_source == "= T"


def test_list_resumes_shows_only_my_own(client, db_session, a_user, other_user):
    from tests.conftest import _resume_for

    _resume_for(db_session, a_user, title="Mine")
    _resume_for(db_session, other_user, title="Theirs")
    _guest_resume(db_session, "g3")
    listed = json.loads(_call(client, db_session, a_user, "list_resumes", {}))["items"]
    assert [r["title"] for r in listed] == ["Mine"]
    assert {"id", "title", "kind", "locale", "parent_resume_id", "target_label"} <= set(
        listed[0]
    )


def test_list_resumes_is_newest_first(client, db_session, a_user):
    from datetime import timedelta

    from tests.conftest import _resume_for

    old = _resume_for(db_session, a_user, title="Old")
    new = _resume_for(db_session, a_user, title="New")
    old.updated_at = new.updated_at - timedelta(days=3)
    db_session.commit()
    listed = json.loads(_call(client, db_session, a_user, "list_resumes", {}))["items"]
    assert [r["title"] for r in listed] == ["New", "Old"]


# --- lifecycle: create, fork, rename ---------------------------------------


def test_create_resume_starts_from_the_starter_and_belongs_to_me(
    client, db_session, a_user
):
    from app.models import Resume
    from app.services.starter import generate_starter

    out = json.loads(_call(client, db_session, a_user, "create_resume", {"locale": "zh-TW"}))
    made = db_session.get(Resume, out["resume_id"])
    assert made.user_id == a_user.id
    assert made.guest_session_id is None
    assert made.typst_source == generate_starter("zh-TW")


def test_create_resume_uses_the_locales_default_title(client, db_session, a_user):
    from app.models import Resume
    from app.services.starter import default_title

    out = json.loads(_call(client, db_session, a_user, "create_resume", {"locale": "en"}))
    assert db_session.get(Resume, out["resume_id"]).title == default_title("en")
    assert out["title"] == default_title("en")


def test_create_resume_keeps_a_given_title_and_locale(client, db_session, a_user):
    from app.models import Resume

    out = json.loads(
        _call(client, db_session, a_user, "create_resume", {"title": "Mine", "locale": "en"})
    )
    made = db_session.get(Resume, out["resume_id"])
    assert (made.title, made.locale) == ("Mine", "en")


def test_create_resume_rejects_an_unknown_locale(client, db_session, a_user):
    from app.models import Resume

    assert _is_tool_error(
        _raw_call(client, db_session, a_user, "create_resume", {"locale": "klingon"})
    )
    assert db_session.query(Resume).filter(Resume.user_id == a_user.id).count() == 0


def test_create_resume_rejects_a_bad_title(client, db_session, a_user):
    for bad in ("", "   ", "x" * 256, 5):
        assert _is_tool_error(
            _raw_call(client, db_session, a_user, "create_resume", {"title": bad})
        ), bad


def test_create_tailor_forks_without_calling_a_model(
    client, db_session, a_user, my_resume
):
    from app.models import Resume

    out = json.loads(
        _call(
            client, db_session, a_user, "create_tailor",
            {"resume_id": my_resume.id, "title": "For Acme", "target_label": "Acme"},
        )
    )
    tailor = db_session.get(Resume, out["resume_id"])
    assert tailor.parent_resume_id == my_resume.id
    assert tailor.typst_source == my_resume.typst_source
    assert tailor.user_id == a_user.id
    assert tailor.title == "For Acme"
    assert tailor.target_label == "Acme"


def test_create_tailor_on_someone_elses_resume_is_not_found(
    client, db_session, a_user, other_user
):
    from tests.conftest import _resume_for

    theirs = _resume_for(db_session, other_user)
    assert _is_tool_error(
        _raw_call(
            client, db_session, a_user, "create_tailor",
            {"resume_id": theirs.id, "title": "x"},
        )
    )


def test_create_tailor_ownership_is_checked_before_the_title(
    client, db_session, a_user, other_user
):
    from tests.conftest import _resume_for

    theirs = _resume_for(db_session, other_user)
    body = _raw_call(
        client, db_session, a_user, "create_tailor", {"resume_id": theirs.id, "title": ""}
    )
    assert "not found" in body["result"]["content"][0]["text"].lower()


def test_create_tailor_rejects_a_blank_or_overlong_title(
    client, db_session, a_user, my_resume
):
    # A crash is also reported as a tool error, so check the message: the agent
    # must be told what was wrong with the title, not "tool failed".
    for bad, why in (("", "required"), ("   ", "required"), ("x" * 256, "too long")):
        body = _raw_call(
            client, db_session, a_user, "create_tailor",
            {"resume_id": my_resume.id, "title": bad},
        )
        assert _is_tool_error(body), bad
        assert why in body["result"]["content"][0]["text"], bad


def test_set_resume_title_renames_and_touches_nothing_else(
    client, db_session, a_user, my_resume
):
    before = (my_resume.typst_source, my_resume.locale, my_resume.kind)
    _call(
        client, db_session, a_user, "set_resume_title",
        {"resume_id": my_resume.id, "title": "  New name  "},
    )
    db_session.expire_all()
    assert my_resume.title == "New name"
    assert (my_resume.typst_source, my_resume.locale, my_resume.kind) == before


def test_set_resume_title_rejects_blank_and_overlong(client, db_session, a_user, my_resume):
    for bad in ("", "   ", "x" * 256):
        assert _is_tool_error(
            _raw_call(
                client, db_session, a_user, "set_resume_title",
                {"resume_id": my_resume.id, "title": bad},
            )
        ), bad


def test_set_resume_title_on_someone_elses_resume_is_not_found(
    client, db_session, a_user, other_user
):
    from tests.conftest import _resume_for

    theirs = _resume_for(db_session, other_user, title="Theirs")
    assert _is_tool_error(
        _raw_call(
            client, db_session, a_user, "set_resume_title",
            {"resume_id": theirs.id, "title": "Mine now"},
        )
    )
    db_session.expire_all()
    assert theirs.title == "Theirs"


def test_no_tool_can_delete_a_resume(client, db_session, a_user):
    # A guard, not a proof: it cannot catch a delete tool with another name.
    assert not [name for name in (s["name"] for s in specs()) if "delete" in name]
