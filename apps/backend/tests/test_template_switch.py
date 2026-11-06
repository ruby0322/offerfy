"""The picker's switch target as a parameter, not a guessed sentence.

`mentioned_template` is the one place that still has to guess -- a message
typed by hand rather than sent by the picker's own request -- so its tests
live here rather than in test_chat.py, alongside the endpoint that makes the
parameter the normal path.
"""

import json

import pytest

from app.models import GuestSession, Resume
from app.services import templates
from app.services.role_card import build_role_card
from app.services.templates import mentioned_template, reset_template_index_cache


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
