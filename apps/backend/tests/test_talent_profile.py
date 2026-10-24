import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import GUEST_COOKIE, SESSION_COOKIE, _sign
from app.models import LlmCall, MemoryItem, Resume, TalentProfile, User
from app.services.profile import _derived
from app.services.revisions import pin_current_draft, set_draft_source
from app.schemas import TalentProfilePayload

OWNER_ROUTES = (
    ("get", "/v1/me/talent-profile", None),
    ("put", "/v1/me/talent-profile/source", {"resume_id": None}),
    ("post", "/v1/me/talent-profile/refresh", None),
    ("delete", "/v1/me/talent-profile", None),
)


class StubProfileLlm:
    """Stands in for httpx.AsyncClient inside services.profile."""

    def __init__(self, payload: dict | None = None, status_code: int = 200, text=None):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "body": json or {}})
        return self

    # The response object is this same stub, so one class covers both.
    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload

    @property
    def prompt(self) -> str:
        return self.calls[0]["body"]["input"][0]["content"]


def _said(profile: dict, usage: dict | None = None) -> dict:
    """A Responses-API envelope carrying one profile payload."""
    body = {"output_text": json.dumps(profile)}
    if usage is not None:
        body["usage"] = usage
    return body


def _profile(**overrides) -> dict:
    base = {
        "display_name": "Chen Wei",
        "headline": "Backend engineer",
        "summary": "Builds payment systems.",
        "years_experience": 6,
        "locations": ["Taipei"],
        "languages": ["Mandarin", "English"],
        "skills": ["Python", "Go"],
        "roles": [
            {
                "employer": "Acme",
                "title": "Engineer",
                "start": "2021-03",
                "end": "Present",
                "highlights": ["Cut latency in half"],
            }
        ],
        "education": [
            {
                "institution": "NTU",
                "credential": "BSc",
                "field": "Information Management",
                "end": "2019-06",
            }
        ],
    }
    base.update(overrides)
    return base


def _signed_in(client, db_session, sub="sub-talent", name="Chen Wei"):
    user = User(
        google_sub=sub, email=f"{sub}@example.com", locale="en", name=name
    )
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _resume(db_session, user, *, source="= CV\nChen Wei", publish=True, title="Master"):
    resume = Resume(
        title=title,
        typst_source=source,
        source="create",
        locale="en",
        user_id=user.id,
        kind="master",
    )
    db_session.add(resume)
    db_session.flush()
    set_draft_source(db_session, resume, source)
    if publish:
        pin_current_draft(db_session, resume)
    db_session.commit()
    return resume


def _grant(client):
    response = client.put(
        "/v1/me/consents/talent_introduction", json={"granted": True}
    )
    assert response.status_code == 200


def _point_at(client, resume_id):
    return client.put("/v1/me/talent-profile/source", json={"resume_id": resume_id})


def _stub(monkeypatch, stub):
    monkeypatch.setattr("app.services.profile.httpx.AsyncClient", stub)
    return stub


def _ready(client, db_session, monkeypatch, sub="sub-ready", **kwargs):
    """A consenting user with a published resume and a stubbed provider."""
    user = _signed_in(client, db_session, sub=sub, name=kwargs.pop("name", "Chen Wei"))
    resume = _resume(db_session, user, **kwargs)
    _grant(client)
    return user, resume


# --- refusals ----------------------------------------------------------------


def test_refresh_requires_consent(client: TestClient, db_session, openai_enabled):
    user = _signed_in(client, db_session, sub="sub-no-consent")
    _resume(db_session, user)
    user.profile_resume_id = user.profile_resume_id  # no pointer set yet
    response = client.post("/v1/me/talent-profile/refresh")
    assert response.status_code == 409
    assert "Open to matching" in response.json()["detail"]


def test_refresh_requires_a_chosen_resume(
    client: TestClient, db_session, openai_enabled
):
    _signed_in(client, db_session, sub="sub-no-pointer")
    _grant(client)
    response = client.post("/v1/me/talent-profile/refresh")
    assert response.status_code == 409
    assert response.json()["detail"] == "Choose a resume first"


def test_refresh_requires_a_published_revision(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    """A draft-only resume is not something we may show an employer."""
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    user = _signed_in(client, db_session, sub="sub-draft-only")
    resume = _resume(db_session, user, publish=False)
    _grant(client)
    _point_at(client, resume.id)

    response = client.post("/v1/me/talent-profile/refresh")
    assert response.status_code == 409
    assert response.json()["detail"] == "Publish this resume first"
    assert db_session.query(TalentProfile).count() == 0


def test_pointing_at_someone_elses_resume_is_404(client: TestClient, db_session):
    other = _signed_in(client, db_session, sub="sub-other")
    theirs = _resume(db_session, other)
    _signed_in(client, db_session, sub="sub-thief")
    assert _point_at(client, theirs.id).status_code == 404


def test_guest_cannot_reach_any_owner_route(client: TestClient):
    client.cookies.clear()
    client.cookies.set(GUEST_COOKIE, "guest-key")
    for method, path, body in OWNER_ROUTES:
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 401, path


def test_build_without_a_configured_provider_is_503(
    client: TestClient, db_session, monkeypatch
):
    user, resume = _ready(client, db_session, monkeypatch, sub="sub-no-key")
    _point_at(client, resume.id)
    response = client.post("/v1/me/talent-profile/refresh")
    assert response.status_code == 503
    assert db_session.query(TalentProfile).count() == 0


# --- building ----------------------------------------------------------------


def test_refresh_builds_a_profile_and_derives_its_columns(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    user, resume = _ready(client, db_session, monkeypatch, sub="sub-build")
    _point_at(client, resume.id)

    response = client.post("/v1/me/talent-profile/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["headline"] == "Backend engineer"
    assert body["profile"]["roles"][0]["employer"] == "Acme"

    profile = db_session.query(TalentProfile).one()
    assert profile.user_id == user.id
    assert profile.source_resume_id == resume.id
    assert profile.source_revision_id == resume.published_revision_id
    assert profile.display_name == "Chen Wei"
    assert profile.years_experience == 6
    assert profile.skills_text == "|python|go|"
    assert "backend engineer" in profile.search_text
    # The reader is handed the profile id, never the user id.
    assert body["profile_id"] == profile.id
    assert user.id not in json.dumps(body)


def test_build_reads_the_published_revision_not_the_draft(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    stub = _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    user, resume = _ready(
        client, db_session, monkeypatch, sub="sub-published", source="= Published"
    )
    set_draft_source(db_session, resume, "= Draft nobody published")
    db_session.commit()
    _point_at(client, resume.id)

    assert "= Published" in stub.prompt
    assert "Draft nobody published" not in stub.prompt


def test_accepted_memory_items_reach_the_prompt_and_pending_ones_do_not(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    stub = _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    user, resume = _ready(client, db_session, monkeypatch, sub="sub-memory")
    db_session.add_all(
        [
            MemoryItem(
                master_resume_id=resume.id, text="Open to Taipei only", status="accepted"
            ),
            MemoryItem(
                master_resume_id=resume.id, text="Maybe wants to relocate", status="pending"
            ),
        ]
    )
    db_session.commit()
    _point_at(client, resume.id)

    assert "Open to Taipei only" in stub.prompt
    assert "Maybe wants to relocate" not in stub.prompt


def test_the_prompt_forbids_contact_details(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    stub = _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-prompt")
    _point_at(client, resume.id)
    assert "Never include an email address" in stub.prompt


def test_contact_details_are_scrubbed_from_every_field(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    """The prompt says not to. This is what happens when it does anyway."""
    leaky = _profile(
        display_name="Chen Wei @chenwei",
        headline="Engineer, chenwei.dev",
        summary="Reach me at chen@example.com or +886 912 345 678.",
        locations=["Taipei 10617"],
        roles=[
            {
                "employer": "Acme",
                "title": "Engineer",
                "start": "2021-03",
                "end": "Present",
                "highlights": ["Wrote https://acme.example/docs"],
            }
        ],
    )
    _stub(monkeypatch, StubProfileLlm(_said(leaky)))
    user, resume = _ready(
        client, db_session, monkeypatch, sub="sub-leak", name=None
    )
    _point_at(client, resume.id)

    body = client.post("/v1/me/talent-profile/refresh").json()
    blob = json.dumps(body)
    assert "@chenwei" not in blob
    assert "chenwei.dev" not in blob
    assert "chen@example.com" not in blob
    assert "912" not in blob
    assert "acme.example" not in blob
    assert "10617" not in blob
    # What is left is still a profile, not an empty shell.
    assert body["profile"]["display_name"] == "Chen Wei"
    assert body["profile"]["locations"] == ["Taipei"]


def test_display_name_prefers_the_google_name(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile(display_name="C. Wei"))))
    _, resume = _ready(
        client, db_session, monkeypatch, sub="sub-name", name="Chen Wei"
    )
    _point_at(client, resume.id)
    body = client.post("/v1/me/talent-profile/refresh").json()
    assert body["profile"]["display_name"] == "Chen Wei"


def test_extracted_name_is_used_when_the_account_has_none(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile(display_name="C. Wei"))))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-noname", name=None)
    _point_at(client, resume.id)
    body = client.post("/v1/me/talent-profile/refresh").json()
    assert body["profile"]["display_name"] == "C. Wei"


def test_the_build_is_recorded_as_spend(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(
        monkeypatch,
        StubProfileLlm(
            _said(_profile(), usage={"input_tokens": 1200, "output_tokens": 300})
        ),
    )
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-spend")
    _point_at(client, resume.id)

    call = db_session.query(LlmCall).filter(LlmCall.purpose == "profile").one()
    assert call.resume_id == resume.id
    assert call.input_tokens == 1200
    assert call.output_tokens == 300
    assert call.source_chars > 0


# --- failures never write ----------------------------------------------------


def test_provider_failure_writes_nothing(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(None, status_code=500))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-500")
    _point_at(client, resume.id)
    assert client.post("/v1/me/talent-profile/refresh").status_code == 502
    assert db_session.query(TalentProfile).count() == 0


def test_provider_failure_does_not_overwrite_a_good_profile(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    good = _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-keep")
    _point_at(client, resume.id)
    kept = db_session.query(TalentProfile).one().content_hash

    _stub(monkeypatch, StubProfileLlm(None, status_code=503))
    # Republish so the rebuild is not a no-op for any other reason.
    set_draft_source(db_session, resume, "= CV v2")
    pin_current_draft(db_session, resume)
    db_session.commit()
    assert client.post("/v1/me/talent-profile/refresh").status_code == 502

    db_session.expire_all()
    assert db_session.query(TalentProfile).one().content_hash == kept
    assert good is not None


def test_unparseable_output_is_not_a_profile(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm({"output_text": "not json at all"}))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-garbage")
    _point_at(client, resume.id)
    assert client.post("/v1/me/talent-profile/refresh").status_code == 502
    assert db_session.query(TalentProfile).count() == 0


def test_an_empty_payload_is_rejected(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    """Never hand an employer a card with nothing on it."""
    empty = _profile(display_name=None, headline=None, roles=[])
    _stub(monkeypatch, StubProfileLlm(_said(empty)))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-empty", name=None)
    _point_at(client, resume.id)
    response = client.post("/v1/me/talent-profile/refresh")
    assert response.status_code == 422
    assert db_session.query(TalentProfile).count() == 0


# --- staleness and lifecycle -------------------------------------------------


def test_profile_is_stale_after_the_resume_is_republished(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-stale")
    _point_at(client, resume.id)
    assert client.get("/v1/me/talent-profile").json()["stale"] is False

    set_draft_source(db_session, resume, "= CV with a new job")
    pin_current_draft(db_session, resume)
    db_session.commit()
    assert client.get("/v1/me/talent-profile").json()["stale"] is True


def test_profile_is_stale_after_a_memory_item_is_accepted(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    """input_hash covers the facts, not just the revision."""
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-stale-memory")
    _point_at(client, resume.id)
    assert client.get("/v1/me/talent-profile").json()["stale"] is False

    db_session.add(
        MemoryItem(master_resume_id=resume.id, text="Prefers remote", status="accepted")
    )
    db_session.commit()
    assert client.get("/v1/me/talent-profile").json()["stale"] is True


def test_moving_the_pointer_drops_the_old_profile(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    user, first = _ready(client, db_session, monkeypatch, sub="sub-move")
    _point_at(client, first.id)
    original = db_session.query(TalentProfile).one().id

    second = _resume(db_session, user, title="Other master", source="= Other CV")
    _point_at(client, second.id)

    profiles = db_session.query(TalentProfile).all()
    assert len(profiles) == 1
    assert profiles[0].source_resume_id == second.id
    assert profiles[0].id != original


def test_clearing_the_pointer_drops_the_profile(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-clear")
    _point_at(client, resume.id)
    assert _point_at(client, None).status_code == 200
    assert db_session.query(TalentProfile).count() == 0


def test_deleting_the_source_resume_deletes_the_profile(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-del-resume")
    _point_at(client, resume.id)
    assert client.delete(f"/v1/resumes/{resume.id}").status_code in (200, 204)
    db_session.expire_all()
    assert db_session.query(TalentProfile).count() == 0


def test_owner_can_delete_the_profile_without_answering_consent(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-owner-del")
    _point_at(client, resume.id)

    assert client.delete("/v1/me/talent-profile").status_code == 204
    assert db_session.query(TalentProfile).count() == 0
    # Consent is their answer to give, not ours to revoke on their behalf.
    assert client.get("/v1/me/consents").json()["consents"]["talent_introduction"] is True


def test_rebuilding_an_unchanged_profile_keeps_the_same_hash(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-idempotent")
    _point_at(client, resume.id)
    first = db_session.query(TalentProfile).one().content_hash

    assert client.post("/v1/me/talent-profile/refresh").status_code == 200
    db_session.expire_all()
    assert db_session.query(TalentProfile).one().content_hash == first


def test_refresh_is_rate_limited(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-rate")
    _point_at(client, resume.id)
    for _ in range(4):
        assert client.post("/v1/me/talent-profile/refresh").status_code == 200
    # The pointer build plus four refreshes is five.
    assert client.post("/v1/me/talent-profile/refresh").status_code == 429


# --- storage shape -----------------------------------------------------------


def test_content_hash_is_stable_across_a_read_round_trip(
    client: TestClient, db_session, monkeypatch, openai_enabled
):
    """Why profile_json is Text and not JSONB.

    disclosure_events cites content_hash as evidence that a recipient holds
    this exact payload, so the stored bytes must hash to the same digest after
    a write and a read.
    """
    import hashlib

    _stub(monkeypatch, StubProfileLlm(_said(_profile())))
    _, resume = _ready(client, db_session, monkeypatch, sub="sub-hash")
    _point_at(client, resume.id)

    db_session.expire_all()
    profile = db_session.query(TalentProfile).one()
    assert (
        hashlib.sha256(profile.profile_json.encode("utf-8")).hexdigest()
        == profile.content_hash
    )


@pytest.mark.parametrize(
    "skills,expected",
    [
        (["Python", "Go"], "|python|go|"),
        (["JavaScript"], "|javascript|"),
        ([], ""),
    ],
)
def test_skills_text_is_pipe_delimited(skills, expected):
    assert _derived(TalentProfilePayload(skills=skills))["skills_text"] == expected


def test_a_skill_filter_cannot_match_a_longer_skill():
    """'|java|' must not find 'javascript'."""
    text = _derived(TalentProfilePayload(skills=["JavaScript"]))["skills_text"]
    assert "|java|" not in text
    assert "|javascript|" in text
