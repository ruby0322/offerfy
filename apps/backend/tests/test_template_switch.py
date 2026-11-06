"""The picker's switch target as a parameter, not a guessed sentence.

`mentioned_template` is the one place that still has to guess -- a message
typed by hand rather than sent by the picker's own request -- so its tests
live here rather than in test_chat.py, alongside the endpoint that makes the
parameter the normal path.
"""

import json

import pytest

from fastapi import HTTPException

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import ChatMessage, GuestSession, LlmCall, Resume, User
from app.routers.chat import _history
from app.services import templates, usage
from app.services.metering import Meter, system_guard
from app.services.resume_content import ExtractResult, store_content
from app.services.role_card import build_role_card
from app.services.templates import mentioned_template, reset_template_index_cache
from app.services.typst_compile import typst_available
from tests.fixtures.golden_content import GOLDEN


@pytest.fixture(autouse=True)
def _reset_templates_cache():
    reset_template_index_cache()
    yield
    reset_template_index_cache()


def _create_resume(client) -> str:
    return client.post("/v1/resumes", json={"locale": "en"}).json()["id"]


def test_switch_rejects_a_template_not_on_the_support_list(client):
    rid = _create_resume(client)
    resp = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "modern-cv", "version": "1.0.0"}
    )
    assert resp.status_code == 400


def test_switch_returns_the_model_path_prompt_for_a_supported_template(client):
    rid = _create_resume(client)
    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "gloat", "version": "0.1.0"}
    ).json()
    assert body["mode"] == "llm"
    assert "@preview/gloat:0.1.0" in body["prompt"]


def test_switch_404s_for_a_resume_this_caller_does_not_own(client):
    rid = _create_resume(client)
    client.cookies.clear()
    resp = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "gloat", "version": "0.1.0"}
    )
    assert resp.status_code == 404


# --- applied mode: a switch that writes the document itself, no model -----
#
# Every test below that asserts `mode == "applied"` needs the real typst
# binary: the endpoint compiles the emitted source before it writes
# anything, `compile_typst` raises `HTTPException(503)` with no binary,
# and the handler's own `except HTTPException` turns that into the model
# path -- so without the binary these fail rather than skip. Marked per
# test, deliberately *not* module-level: the eleven other tests in this
# module (the 400/404 gates, the model-path fallbacks, `mentioned_
# template`, the role card) are binary-independent and worth keeping in
# a fresh clone or a worktree that has no typst.
_needs_typst = pytest.mark.skipif(
    not typst_available(), reason="typst binary not available"
)


def _signed_in(client, db_session, sub="sub-template-switch"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _never_called(*args, **kwargs):
    raise AssertionError("extract_content must not be called when the snapshot is fresh")


@_needs_typst
def test_switch_applies_without_a_model_when_an_emitter_exists(client, db_session, monkeypatch):
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    store_content(db_session, resume, GOLDEN, model="m")
    db_session.commit()
    monkeypatch.setattr("app.routers.resumes.extract_content", _never_called)

    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()

    assert body["mode"] == "applied"
    assert body["dropped"] == ["projects[].dates"]
    assert "@preview/acorn-resume:0.1.0" in db_session.get(Resume, rid).typst_source


@_needs_typst
def test_switch_writes_a_system_row_that_never_reaches_the_model(client, db_session, monkeypatch):
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    store_content(db_session, resume, GOLDEN, model="m")
    db_session.commit()
    monkeypatch.setattr("app.routers.resumes.extract_content", _never_called)

    client.post(f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"})

    rows = db_session.query(ChatMessage).filter(ChatMessage.resume_id == rid).all()
    system_rows = [r for r in rows if r.role == "system"]
    assert len(system_rows) == 1
    payload = json.loads(system_rows[0].content)
    assert payload == {
        "type": "template_switch",
        "name": "acorn-resume",
        "version": "0.1.0",
        "dropped": ["projects[].dates"],
        "degraded": ["education[].bullets"],
    }
    # _history is what the model actually sees -- the system row must not
    # reach it, the same way a `tool` row already does not. See chat.py.
    assert system_rows[0].id not in {row.id for row in _history(db_session, rid)}


@_needs_typst
def test_switch_extracts_once_and_reuses_the_snapshot(client, db_session, monkeypatch):
    """Two consecutive switches, one extraction -- no rollback in between.

    `store_content`/`restamp_content` re-key the snapshot to each
    successfully-emitted document's own hash, so a *second* switch right
    after the first still finds a fresh snapshot without re-extracting --
    not because the source happens to match what it originally was, but
    because the snapshot was deliberately re-stamped to describe the
    document the first switch actually wrote.
    """
    rid = _create_resume(client)
    calls = {"n": 0}

    async def _fake_extract(source):
        calls["n"] += 1
        return ExtractResult(content=GOLDEN, model="m", usage={}, source_chars=len(source))

    monkeypatch.setattr("app.routers.resumes.extract_content", _fake_extract)

    first = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()
    assert first["mode"] == "applied"
    assert calls["n"] == 1
    # acorn-resume drops a project's dates -- confirmed gone from the
    # document this switch actually wrote.
    assert "2021-03" not in db_session.get(Resume, rid).typst_source

    second = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "basic-resume", "version": "0.2.9"}
    ).json()
    assert second["mode"] == "applied"
    assert calls["n"] == 1
    # The snapshot never lost the dates -- only acorn-resume's own document
    # had nowhere to put them -- so switching again restores them.
    assert "2021-03" in db_session.get(Resume, rid).typst_source


@_needs_typst
def test_switch_applies_from_a_fresh_snapshot_even_when_quota_is_exhausted(
    client, db_session, monkeypatch
):
    """Critical 1: `preflight` must not gate the zero-cost, no-model-call
    path. A subject already over their weekly ceiling still gets a free,
    deterministic switch when the snapshot is fresh -- that is the
    feature's entire premise, and it must not become reachable only with
    quota to spare.
    """
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    user = _signed_in(client, db_session, sub="sub-quota-exhausted")
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    store_content(db_session, resume, GOLDEN, model="m")
    db_session.commit()
    monkeypatch.setattr("app.routers.resumes.extract_content", _never_called)

    tier = usage.TIERS[usage.REGISTERED]
    Meter(system_guard(user=user, purpose="chat", reason="test")).record(
        {
            "model": "gpt-5.6-terra",
            "purpose": "chat",
            "round_index": 0,
            "input_tokens": tier.credits * usage.CREDIT_UUSD,
        }
    )
    assert usage.balance(db_session, user, None).exhausted

    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()

    assert body["mode"] == "applied"


def test_switch_does_not_write_when_the_emitted_source_fails_to_compile(
    client, db_session, monkeypatch
):
    """Unmarked on purpose -- but it proves less without the binary.

    With no typst installed, `compile_typst` would raise
    `HTTPException(503)` before `_boom` below is ever reached, so this
    test's "no write, model path" assertions come out true for the wrong
    reason: the monkeypatched failure is never what fails. It still
    passes, which is why it carries no skip marker, but in a binary-less
    checkout it is not evidence that the *emit* path's own compile gate
    aborts the write -- only the marked tests above establish that the
    applied path runs at all.
    """
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    before = resume.typst_source
    store_content(db_session, resume, GOLDEN, model="m")
    db_session.commit()
    monkeypatch.setattr("app.routers.resumes.extract_content", _never_called)

    def _boom(*args, **kwargs):
        raise HTTPException(status_code=400, detail="bad typst")

    monkeypatch.setattr("app.routers.resumes.compile_typst", _boom)

    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()

    assert body["mode"] == "llm"
    assert db_session.get(Resume, rid).typst_source == before


def test_switch_refuses_when_the_source_changed_after_extraction(client, db_session, monkeypatch):
    rid = _create_resume(client)
    edited = {"source": None}

    async def _fake_extract(source):
        # Simulate a chat edit landing in the gap between the extraction
        # call starting and this request's own write -- Review Focus 2.
        other = db_session.get(Resume, rid)
        other.typst_source = source + "\n// edited mid-flight"
        db_session.commit()
        edited["source"] = other.typst_source
        return ExtractResult(content=GOLDEN, model="m", usage={}, source_chars=len(source))

    monkeypatch.setattr("app.routers.resumes.extract_content", _fake_extract)

    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()

    assert body["mode"] == "llm"
    assert edited["source"] is not None
    assert db_session.get(Resume, rid).typst_source == edited["source"]


def test_switch_falls_back_to_the_model_path_when_extraction_fails(client, db_session, monkeypatch):
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    before = resume.typst_source

    async def _fake_extract(source):
        return ExtractResult(error="provider_error", model="m", usage={}, source_chars=len(source))

    monkeypatch.setattr("app.routers.resumes.extract_content", _fake_extract)

    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()

    assert body["mode"] == "llm"
    assert "@preview/acorn-resume:0.1.0" in body["prompt"]
    assert db_session.get(Resume, rid).typst_source == before
    # Telemetry still fires on a failed extraction -- see
    # resume_content.record_extract_spend and decision 2 on this task.
    row = db_session.query(LlmCall).filter(LlmCall.purpose == "extract").one()
    assert row.resume_id == rid


# --- billing: exactly one of user/guest is set, matching llm_calls' own ---
# --- CHECK constraint (llm_call_subject_not_both) --------------------------


@_needs_typst
def test_switch_bills_a_guest_to_the_guest_column_on_extraction(client, db_session, monkeypatch):
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    assert resume.guest_session_id is not None

    async def _fake_extract(source):
        return ExtractResult(content=GOLDEN, model="m", usage={}, source_chars=len(source))

    monkeypatch.setattr("app.routers.resumes.extract_content", _fake_extract)

    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()
    assert body["mode"] == "applied"

    row = db_session.query(LlmCall).filter(LlmCall.purpose == "extract").one()
    assert row.user_id is None
    assert row.guest_session_id == resume.guest_session_id


@_needs_typst
def test_switch_bills_a_signed_in_user_to_the_user_column_on_extraction(
    client, db_session, monkeypatch
):
    user = _signed_in(client, db_session)
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    assert resume.user_id == user.id

    async def _fake_extract(source):
        return ExtractResult(content=GOLDEN, model="m", usage={}, source_chars=len(source))

    monkeypatch.setattr("app.routers.resumes.extract_content", _fake_extract)

    body = client.post(
        f"/v1/resumes/{rid}/template", json={"name": "acorn-resume", "version": "0.1.0"}
    ).json()
    assert body["mode"] == "applied"

    row = db_session.query(LlmCall).filter(LlmCall.purpose == "extract").one()
    assert row.guest_session_id is None
    assert row.user_id == user.id


def test_mentioned_template_matches_a_package_name_in_any_language(monkeypatch, tmp_path):
    # acorn-resume is not on the real, committed support list (its vendor
    # example needs a font this host lacks), but this test is about
    # language-independent name matching, not about which packages happen to
    # be approved today -- so it supplies its own fixture list.
    support = tmp_path / "template_support.json"
    support.write_text(
        json.dumps(
            {
                "rows": [
                    {"name": "acorn-resume", "version": "0.1.0", "ok": True},
                    {"name": "gloat", "version": "0.1.0", "ok": True},
                ]
            }
        )
    )
    monkeypatch.setattr(templates, "SUPPORT_PATH", support)
    assert mentioned_template("幫我換成 acorn-resume") == ("acorn-resume", "0.1.0")
    assert mentioned_template("switch to @preview/gloat:0.1.0") == ("gloat", "0.1.0")
    assert mentioned_template("give me something prettier") is None


def test_mentioned_template_prefers_the_longer_name(monkeypatch, tmp_path):
    support = tmp_path / "template_support.json"
    support.write_text(
        json.dumps(
            {
                "rows": [
                    {"name": "basic-resume", "version": "0.2.9", "ok": True},
                    {"name": "basic-resume-plus", "version": "1.0.0", "ok": True},
                ]
            }
        )
    )
    monkeypatch.setattr(templates, "SUPPORT_PATH", support)
    assert mentioned_template("use basic-resume-plus") == ("basic-resume-plus", "1.0.0")
    assert mentioned_template("use basic-resume") == ("basic-resume", "0.2.9")


def test_mentioned_template_spec_wins_even_for_an_unsupported_package(monkeypatch, tmp_path):
    # Review Focus 3: the explicit @preview/name:version pin must be honoured
    # even when the named package is not on the support list -- the endpoint
    # rejects it, but a message merely mentioning it should still steer the
    # model path's extras.
    support = tmp_path / "template_support.json"
    support.write_text(json.dumps({"rows": []}))
    monkeypatch.setattr(templates, "SUPPORT_PATH", support)
    assert mentioned_template("switch to @preview/acorn-resume:0.1.0") == (
        "acorn-resume",
        "0.1.0",
    )


def test_role_card_names_the_template_the_document_uses(client, db_session):
    rid = _create_resume(client)
    resume = db_session.get(Resume, rid)
    card = build_role_card(db_session, resume)
    assert "@preview/basic-resume:0.2.9" in card


def test_role_card_says_nothing_when_the_source_names_no_package(db_session):
    guest = GuestSession(key_hash="role-card-no-template", locale="en")
    db_session.add(guest)
    db_session.commit()
    resume = Resume(
        title="R",
        typst_source="= No import here",
        source="upload",
        guest_session_id=guest.id,
    )
    db_session.add(resume)
    db_session.commit()
    card = build_role_card(db_session, resume)
    assert "@preview/" not in card
