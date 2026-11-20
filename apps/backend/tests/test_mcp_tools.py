"""The tools behind POST /mcp.

Helpers here (`_raw_call`, `_call`, `_is_tool_error`, `_score`) are shared by
the tool tests of later tasks: keep their shapes stable.
"""

import json

import pytest
from datetime import datetime, timezone

from app.models import JobScore
from app.services.agent_keys import mint_agent_key
from app.services.jobs_tools import catalogue_specs
from app.services.mcp_tools import specs


def _token_for(client, db, user) -> str:
    """One key per user per test, minted on first use.

    `_raw_call` used to mint on every call while MAX_LIVE_KEYS is 5, so any
    test making a sixth tool call as one user hit the cap -- which Task 8
    worked around with its own `_caller` instead of fixing the shared helper.
    The cache hangs off the `client` object, which the fixture replaces for
    every test along with the database, so a token can never outlive the rows
    it was minted in.
    """
    cache = getattr(client, "_agent_tokens", None)
    if cache is None:
        cache = {}
        client._agent_tokens = cache
    raw = cache.get(user.id)
    if raw is None:
        _, raw = mint_agent_key(db, user=user, label="t")
        db.commit()
        cache[user.id] = raw
    return raw


def _raw_call(client, db, user, name, args) -> dict:
    """Post one tools/call as `user` and return the JSON-RPC response."""
    raw = _token_for(client, db, user)
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


def test_a_missing_posting_is_a_404_and_another_refusal_is_a_400(
    client, db_session, a_user
):
    """The two branches of the catalogue wrapper's status mapping. It used to
    pick between them with `startswith("No posting")`, so rewording that
    sentence for a model's benefit would have made every missing posting a 400
    with nothing to notice."""
    from fastapi import HTTPException

    from app.services.mcp_tools import REGISTRY

    handler = REGISTRY["read_job"].handler
    with pytest.raises(HTTPException) as missing:
        handler(db_session, a_user, {"job_id": "no-such-posting"})
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as other:
        handler(db_session, a_user, {"job_id": ""})
    assert other.value.status_code == 400


def test_every_tool_is_classified_for_the_audit_log(client):
    """A tool added later must not default to invisible in the user's log: the
    two sets have to cover the registry exactly, so adding a tool without
    deciding which side it is on fails here."""
    from app.services.mcp_tools import (
        AUDIT_PINNED_TOOLS,
        AUDIT_UNPINNED_TOOLS,
        REGISTRY,
    )

    assert AUDIT_PINNED_TOOLS | AUDIT_UNPINNED_TOOLS == set(REGISTRY)
    assert not AUDIT_PINNED_TOOLS & AUDIT_UNPINNED_TOOLS
    # And the classification is the right way round for the write tools.
    assert {"publish_resume", "put_resume_source", "set_job_preferences"} <= (
        AUDIT_PINNED_TOOLS
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
    """The cap needs MORE scored rows than the cap, or it passes with no cap at
    all: with two rows in the table, `limit: 10**6` returns two either way."""
    from app.services.jobs_tools import SEARCH_LIMIT_MAX
    from tests.conftest import _job_with_facets

    scores = [_score(a_user, a_job, 0.4), _score(a_user, another_job, 0.9)]
    for n in range(SEARCH_LIMIT_MAX):  # SEARCH_LIMIT_MAX + 2 scored rows in all
        job = _job_with_facets(db_session, f"stripe:cap-{n}", f"Engineer {n}")
        scores.append(_score(a_user, job, 0.5))
    db_session.add_all(scores)
    db_session.commit()
    one = json.loads(_call(client, db_session, a_user, "list_matches", {"limit": 1}))
    assert len(one["items"]) == 1
    assert one["items"][0]["job_id"] == another_job.id
    many = json.loads(_call(client, db_session, a_user, "list_matches", {"limit": 10**6}))
    assert len(many["items"]) == SEARCH_LIMIT_MAX


@pytest.mark.parametrize("tool", ["list_matches", "search_jobs"])
def test_an_infinite_limit_falls_back_to_the_default(
    client, db_session, a_user, a_job, tool
):
    """JSON's `1e999` parses to float("inf"), and `int(inf)` raises
    OverflowError -- which neither `except (TypeError, ValueError)` caught, so
    the agent got "The tool failed unexpectedly" where the constraint says a
    malformed limit falls back to the default."""
    db_session.add(_score(a_user, a_job, 0.9))
    _, raw = mint_agent_key(db_session, user=a_user, label="t")
    db_session.commit()
    # Posted as raw bytes: `1e999` is valid JSON that Python parses to inf, but
    # the client's own encoder refuses to serialise a float that far out.
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
        '{"name":"%s","arguments":{"limit":1e999}}}' % tool
    )
    r = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw}", "Content-Type": "application/json"},
        content=body.encode(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["isError"] is False, r.text


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
    """Three rows, timestamped so the answer is neither id order nor its
    reverse: with two rows an implementation that sorted by the random uuid
    alone passed or failed depending on which uuid came out larger, and a test
    that fails one run in two is worse than none."""
    from datetime import timedelta

    from tests.conftest import _resume_for

    rows = [_resume_for(db_session, a_user, title=f"r{n}") for n in range(3)]
    rows.sort(key=lambda r: r.id)
    base = rows[0].updated_at
    # Middle id newest, lowest id next, highest id oldest.
    for resume, days in zip(rows, (1, 0, 2)):
        resume.updated_at = base - timedelta(days=days)
    db_session.commit()
    listed = json.loads(_call(client, db_session, a_user, "list_resumes", {}))["items"]
    assert [r["title"] for r in listed] == [rows[1].title, rows[0].title, rows[2].title]


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


def test_the_resume_router_rejects_an_unknown_locale(client):
    # validate_locale lives in services/starter.py; the router must still use it.
    r = client.post("/v1/resumes", json={"locale": "klingon"})
    assert r.status_code == 400
    assert "locale must be" in r.json()["detail"]
    assert client.post("/v1/resumes", json={"locale": "en"}).status_code == 201


def _caller(client, db, user):
    """`_raw_call` bound to one user, for a test that makes several calls.

    Identical to calling `_raw_call` directly now that the key is shared (see
    `_token_for`); kept because it reads better in a test that calls three
    tools in a row.
    """
    raw = _token_for(client, db, user)

    def call(name, args):
        r = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {raw}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": name, "arguments": args}},
        )
        assert r.status_code == 200, r.text
        return r.json()

    return call


def _text(body):
    return body["result"]["content"][0]["text"]


def test_set_resume_title_checks_ownership_before_the_title(
    client, db_session, a_user, other_user
):
    from tests.conftest import _resume_for

    theirs = _resume_for(db_session, other_user)
    body = _raw_call(
        client, db_session, a_user, "set_resume_title",
        {"resume_id": theirs.id, "title": ""},
    )
    assert _is_tool_error(body)
    assert "not found" in _text(body).lower()


def test_guest_owned_resumes_are_not_found_for_fork_and_rename(
    client, db_session, a_user
):
    orphan = _guest_resume(db_session, "g8")
    for tool in ("create_tailor", "set_resume_title"):
        body = _raw_call(
            client, db_session, a_user, tool, {"resume_id": orphan.id, "title": "x"}
        )
        assert _is_tool_error(body), tool
        assert "not found" in _text(body).lower(), tool
    db_session.expire_all()
    assert orphan.title == "Guest draft"


def test_a_title_of_the_wrong_type_is_a_400_not_a_crash(
    client, db_session, a_user, my_resume
):
    # A crash is also isError, but its text is the generic "failed unexpectedly".
    call = _caller(client, db_session, a_user)
    for tool, args in (
        ("create_tailor", {"resume_id": my_resume.id}),
        ("set_resume_title", {"resume_id": my_resume.id}),
        ("create_resume", {}),
    ):
        for bad in (3.5, [], {}, 0, False, None):
            if tool == "create_resume" and bad is None:
                continue
            body = call(tool, {**args, "title": bad})
            assert _is_tool_error(body), (tool, bad)
            assert "required" in _text(body), (tool, bad, _text(body))


def test_create_resume_locale_of_the_wrong_type_is_a_400(client, db_session, a_user):
    from app.models import Resume

    call = _caller(client, db_session, a_user)
    for bad in ([], {}, 0, False, 3.5, 7):
        body = call("create_resume", {"locale": bad})
        assert _is_tool_error(body), bad
        assert "locale must be" in _text(body), bad
    assert db_session.query(Resume).filter(Resume.user_id == a_user.id).count() == 0


def test_create_resume_null_or_empty_locale_means_the_default(
    client, db_session, a_user
):
    for unset in (None, ""):
        out = json.loads(
            _call(client, db_session, a_user, "create_resume", {"locale": unset})
        )
        assert out["locale"] == "zh-TW"


def test_the_router_still_treats_an_empty_locale_as_the_default(client):
    r = client.post("/v1/resumes", json={"locale": ""})
    assert r.status_code == 201
    assert r.json()["locale"] == "zh-TW"


# --- publish / unpublish ----------------------------------------------------


def _share_rows(db_session, resume):
    from app.models import ResumeShare

    db_session.expire_all()
    return db_session.query(ResumeShare).filter(ResumeShare.resume_id == resume.id).count()


def test_publish_creates_a_working_public_link(client, db_session, a_user, my_resume):
    out = json.loads(_call(client, db_session, a_user, "publish_resume",
                           {"resume_id": my_resume.id}))
    assert out["public"] is True and out["token"]
    assert client.get(f"/v1/shares/{out['token']}").status_code == 200


def test_publish_also_pins_a_revision(client, db_session, a_user, my_resume):
    _call(client, db_session, a_user, "publish_resume", {"resume_id": my_resume.id})
    db_session.refresh(my_resume)
    assert my_resume.published_revision_id is not None


def test_publish_never_leaves_a_share_without_a_pinned_revision(
    client, db_session, a_user, my_resume
):
    # A share row with no pin makes /v1/shares/{token} 404 on a resume the tool
    # just called public. Check the pair, from the database, after each call.
    from app.models import Resume

    call = _caller(client, db_session, a_user)
    seen_shared = False
    for tool in ("publish_resume", "unpublish_resume", "publish_resume"):
        call(tool, {"resume_id": my_resume.id})
        db_session.expire_all()
        pinned = db_session.get(Resume, my_resume.id).published_revision_id
        if _share_rows(db_session, my_resume):
            seen_shared = True
            assert pinned is not None, tool
    assert seen_shared


def test_publish_is_idempotent_and_keeps_the_token(client, db_session, a_user, my_resume):
    call = _caller(client, db_session, a_user)
    first = json.loads(_text(call("publish_resume", {"resume_id": my_resume.id})))
    second = json.loads(_text(call("publish_resume", {"resume_id": my_resume.id})))
    assert first["token"] == second["token"]
    assert _share_rows(db_session, my_resume) == 1


def test_unpublish_closes_the_link_but_not_the_talent_gate(client, db_session, a_user,
                                                            my_resume):
    """The user chose this. Pinning stays, so a consenting profile stays readable."""
    pub = json.loads(_call(client, db_session, a_user, "publish_resume",
                           {"resume_id": my_resume.id}))
    db_session.refresh(my_resume)
    pinned = my_resume.published_revision_id
    assert pinned is not None
    out = json.loads(_call(client, db_session, a_user, "unpublish_resume",
                           {"resume_id": my_resume.id}))
    assert out["public"] is False and out["token"] is None
    assert client.get(f"/v1/shares/{pub['token']}").status_code == 404
    db_session.refresh(my_resume)
    assert my_resume.published_revision_id == pinned


def test_unpublish_on_a_never_published_resume_is_a_quiet_no_op(
    client, db_session, a_user, my_resume
):
    out = json.loads(_call(client, db_session, a_user, "unpublish_resume",
                           {"resume_id": my_resume.id}))
    assert out["public"] is False
    db_session.refresh(my_resume)
    assert my_resume.published_revision_id is None


def test_publish_and_unpublish_on_someone_elses_resume_are_not_found(
    client, db_session, a_user, other_user
):
    from tests.conftest import _resume_for

    theirs = _resume_for(db_session, other_user)
    call = _caller(client, db_session, a_user)
    for tool in ("publish_resume", "unpublish_resume"):
        body = call(tool, {"resume_id": theirs.id})
        assert _is_tool_error(body), tool
        assert "not found" in _text(body).lower(), tool
    assert _share_rows(db_session, theirs) == 0
    db_session.refresh(theirs)
    assert theirs.published_revision_id is None


def test_publish_and_unpublish_resume_id_of_the_wrong_type_is_a_400(
    client, db_session, a_user
):
    call = _caller(client, db_session, a_user)
    for tool in ("publish_resume", "unpublish_resume"):
        for bad in (3, [], {}, None, False, ""):
            body = call(tool, {"resume_id": bad})
            assert _is_tool_error(body), (tool, bad)
            assert "resume_id is required" in _text(body), (tool, bad, _text(body))


def test_publish_is_recorded_in_the_audit(client, db_session, a_user, my_resume):
    from app.models import AgentToolCall

    _call(client, db_session, a_user, "publish_resume", {"resume_id": my_resume.id})
    row = db_session.query(AgentToolCall).filter(
        AgentToolCall.tool == "publish_resume").one()
    assert row.resume_id == my_resume.id and row.outcome == "ok"


def test_the_publish_descriptions_name_the_talent_profile_consequence():
    by_name = {s["name"]: s["description"] for s in specs()}
    pub, unpub = by_name["publish_resume"], by_name["unpublish_resume"]
    assert "anyone" in pub and "link" in pub
    assert "open to matching" in pub and "recruiter" in pub
    assert "open to matching" in unpub and "website" in unpub
    assert "not" in unpub.lower().split("open to matching")[0]


def _edit_after_publish(db_session, resume):
    from app.services.revisions import set_draft_source

    # The tool ran on its own session: without a refresh this one still sees no
    # pin and would coalesce the edit into the published revision in place.
    db_session.refresh(resume)

    set_draft_source(db_session, resume, resume.typst_source + "\n// edited by agent\n")
    db_session.commit()


def test_publish_after_an_edit_publishes_the_current_draft(
    client, db_session, a_user, my_resume
):
    from app.services.revisions import published_source

    call = _caller(client, db_session, a_user)
    first = json.loads(_text(call("publish_resume", {"resume_id": my_resume.id})))
    assert first["unpublished_changes"] is False
    _edit_after_publish(db_session, my_resume)
    second = json.loads(_text(call("publish_resume", {"resume_id": my_resume.id})))
    db_session.expire_all()
    db_session.refresh(my_resume)
    assert "edited by agent" in my_resume.typst_source
    assert second["unpublished_changes"] is False
    assert second["token"] == first["token"]
    assert published_source(db_session, my_resume) == my_resume.typst_source


def test_publish_of_a_pinned_but_unshared_resume_pins_the_current_draft(
    client, db_session, a_user, my_resume
):
    # Pinned earlier (e.g. by the website's publish button), then edited, never shared.
    from app.services.revisions import pin_current_draft, published_source

    pin_current_draft(db_session, my_resume)
    db_session.commit()
    _edit_after_publish(db_session, my_resume)
    out = json.loads(_call(client, db_session, a_user, "publish_resume",
                           {"resume_id": my_resume.id}))
    db_session.refresh(my_resume)
    assert out["public"] is True and out["unpublished_changes"] is False
    assert published_source(db_session, my_resume) == my_resume.typst_source


# ---- job preferences ----------------------------------------------------------


def _prefs(call, name="get_job_preferences", args=None):
    return json.loads(_text(call(name, args or {})))


def test_get_returns_the_seed_before_the_first_save(client, db_session, a_user):
    from app.models import JobPreference
    from tests.test_job_preferences_api import _facts

    _facts(db_session, a_user, roles=[{"title": "Staff Backend Engineer"}],
           locations=["Taipei, Taiwan"])
    call = _caller(client, db_session, a_user)
    out = _prefs(call)
    assert out["target_functions"] == ["backend"]
    assert out["target_locations"] == ["tw"]
    assert out["updated_at"] is None
    assert out["first_save"] is False
    # Reading never stores: a row would mean "the user has edited this".
    assert db_session.query(JobPreference).count() == 0


def test_set_then_get_round_trips_every_field(client, db_session, a_user):
    call = _caller(client, db_session, a_user)
    saved = _prefs(call, "set_job_preferences", {
        "target_functions": ["backend"], "target_locations": ["tw"],
        "target_seniority": "senior", "remote_required": True, "activity": "browsing",
    })
    assert saved["first_save"] is True
    out = _prefs(call)
    assert out["target_functions"] == ["backend"]
    assert out["target_locations"] == ["tw"]
    assert out["target_seniority"] == "senior"
    assert out["remote_required"] is True
    assert out["activity"] == "browsing"
    assert out["first_save"] is False  # a read never says so


def test_first_save_is_true_once_then_false(client, db_session, a_user):
    call = _caller(client, db_session, a_user)
    assert _prefs(call, "set_job_preferences", {"target_functions": ["backend"]})[
        "first_save"] is True
    assert _prefs(call, "set_job_preferences", {"target_functions": ["design"]})[
        "first_save"] is False


def test_set_replaces_wholesale_rather_than_patching(client, db_session, a_user):
    call = _caller(client, db_session, a_user)
    _prefs(call, "set_job_preferences", {
        "target_functions": ["backend"], "target_locations": ["tw"],
        "target_seniority": "senior", "remote_required": True, "activity": "paused",
    })
    _prefs(call, "set_job_preferences", {"target_functions": ["design"]})
    out = _prefs(call)
    assert out["target_functions"] == ["design"]
    assert out["target_locations"] == []
    assert out["target_seniority"] is None
    assert out["remote_required"] is None
    assert out["activity"] == "actively_looking"


def test_set_updates_the_row_in_place(client, db_session, a_user):
    from app.models import JobPreference

    call = _caller(client, db_session, a_user)
    _prefs(call, "set_job_preferences", {"target_functions": ["backend"]})
    first = db_session.query(JobPreference).one()
    first_id, first_stamp = first.id, first.updated_at
    _prefs(call, "set_job_preferences", {"target_functions": ["design"]})
    db_session.expire_all()
    row = db_session.query(JobPreference).one()
    assert row.id == first_id  # not deleted and re-inserted
    assert row.updated_at > first_stamp


def test_preferences_are_per_user(client, db_session, a_user, other_user):
    from app.models import JobPreference

    _prefs(_caller(client, db_session, a_user), "set_job_preferences",
           {"target_functions": ["backend"]})
    out = _prefs(_caller(client, db_session, other_user))
    assert out["target_functions"] == []
    assert db_session.query(JobPreference).filter_by(user_id=other_user.id).count() == 0


@pytest.mark.parametrize("args,expected", [
    ({"target_functions": "backend"}, "target_functions must be a list of strings"),
    ({"target_functions": [1]}, "target_functions must be a list of strings"),
    ({"target_locations": "tw"}, "target_locations must be a list of strings"),
    ({"target_locations": [None]}, "target_locations must be a list of strings"),
    ({"target_functions": None}, "target_functions must be a list of strings"),
    ({"target_seniority": 3}, "target_seniority must be a string"),
    ({"remote_required": "yes"}, "remote_required must be a boolean"),
    ({"remote_required": 1}, "remote_required must be a boolean"),
    ({"activity": 5}, "activity must be a string"),
    # Well-typed but not in the taxonomy: the model's validators, mapped to a 400.
    ({"activity": "retired"}, "Invalid preferences: activity"),
    ({"target_functions": ["no-such-function"]}, "Invalid preferences: target_functions"),
    ({"target_seniority": "unknown"}, "Invalid preferences: target_seniority"),
    # A typo would otherwise read as "omitted" and clear the field it meant.
    ({"target_function": ["backend"]}, "Unknown argument: target_function"),
])
def test_set_rejects_a_bad_argument_by_name_and_writes_nothing(
    client, db_session, a_user, args, expected
):
    call = _caller(client, db_session, a_user)
    _prefs(call, "set_job_preferences", {"target_functions": ["backend"]})
    body = call("set_job_preferences", args)
    assert _is_tool_error(body), body
    assert _text(body) == expected
    assert _prefs(call)["target_functions"] == ["backend"]


def test_a_bad_first_set_leaves_no_row(client, db_session, a_user):
    from app.models import JobPreference

    body = _caller(client, db_session, a_user)("set_job_preferences",
                                               {"remote_required": "yes"})
    assert _is_tool_error(body)
    assert db_session.query(JobPreference).count() == 0


def test_explicit_empty_and_null_are_accepted(client, db_session, a_user):
    call = _caller(client, db_session, a_user)
    _prefs(call, "set_job_preferences", {"target_functions": ["backend"],
                                         "target_seniority": "senior",
                                         "remote_required": True})
    _prefs(call, "set_job_preferences", {"target_functions": [], "target_locations": [],
                                         "target_seniority": None,
                                         "remote_required": None})
    out = _prefs(call)
    assert out["target_functions"] == [] and out["target_seniority"] is None
    assert out["remote_required"] is None


def test_set_description_leads_with_the_replace_semantics():
    spec = next(s for s in specs() if s["name"] == "set_job_preferences")
    first = spec["description"].split(".")[0].lower()
    assert "replace" in first and "clear" in first


def test_descriptions_say_an_empty_match_list_may_just_be_unscored():
    by_name = {s["name"]: s["description"] for s in specs()}
    # Saving cannot score (scoring is a model call), so an agent must be told
    # not to read the empty list that follows as "no matches".
    assert "saving does not score anything" in by_name["set_job_preferences"].lower()
    assert "not mean the user has no matches" in by_name["list_matches"].lower()


def test_no_preference_tool_can_spend_the_users_allowance():
    names = {s["name"] for s in specs()}
    assert {"get_job_preferences", "set_job_preferences"} <= names
    assert not any("refresh" in n or "facts" in n for n in names)
