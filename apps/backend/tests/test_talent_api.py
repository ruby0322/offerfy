import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import GUEST_COOKIE, SESSION_COOKIE, _sign
from app.models import ConsentEvent, DisclosureEvent, Resume, TalentProfile, User
from app.routers.talent import DISCLOSURE_LIMIT_PER_HOUR
from app.services.consent import POLICY_VERSION, record_consent
from app.services.revisions import pin_current_draft, set_draft_source
from app.services.talent_keys import mint_talent_key

LIST = "/v1/talent/profiles"


def _bearer(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def _key(db_session, label="Acme Talent", contact="talent@acme.example", **kwargs):
    key, raw = mint_talent_key(
        db_session,
        label=label,
        contact=contact,
        created_by="ops@example.com",
        **kwargs,
    )
    db_session.commit()
    return key, raw


def _candidate(
    db_session,
    sub="sub-candidate",
    *,
    consent="granted",
    publish=True,
    point=True,
    name="Chen Wei",
    headline="Backend engineer",
    skills=("python", "go"),
    years=6,
    locations=("Taipei",),
):
    """A person with a profile, assembled directly.

    Built by hand rather than through the build endpoint so these tests are
    about the read path and not about the model call.
    """
    user = User(
        google_sub=sub, email=f"{sub}@example.com", locale="en", name=name
    )
    db_session.add(user)
    db_session.flush()
    resume = Resume(
        title="Master",
        typst_source="= CV",
        source="create",
        locale="en",
        user_id=user.id,
        kind="master",
    )
    db_session.add(resume)
    db_session.flush()
    set_draft_source(db_session, resume, "= CV")
    if publish:
        pin_current_draft(db_session, resume)
    if point:
        user.profile_resume_id = resume.id
    payload = {
        "schema_version": 1,
        "display_name": name,
        "headline": headline,
        "summary": "Builds payment systems.",
        "years_experience": years,
        "locations": list(locations),
        "languages": ["English"],
        "skills": [skill.title() for skill in skills],
        "roles": [],
        "education": [],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    profile = TalentProfile(
        user_id=user.id,
        source_resume_id=resume.id,
        source_revision_id=resume.published_revision_id or "unpublished",
        input_hash="h" * 64,
        profile_json=blob,
        content_hash=f"{sub[:8]:x<64}",
        display_name=name,
        headline=headline,
        years_experience=years,
        search_text=f"{name} {headline} {' '.join(locations)}".lower(),
        skills_text=("|" + "|".join(skills) + "|") if skills else "",
    )
    db_session.add(profile)
    if consent:
        record_consent(
            db_session,
            user=user,
            purpose="talent_introduction",
            action=consent,
            locale="en",
            source="settings",
        )
    db_session.commit()
    return user, resume, profile


# --- credentials ------------------------------------------------------------


def test_no_credential_is_401(client: TestClient, db_session):
    _candidate(db_session)
    response = client.get(LIST)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_an_unknown_credential_is_401(client: TestClient, db_session):
    _candidate(db_session)
    assert client.get(LIST, headers=_bearer("ofk_nope")).status_code == 401
    # A token without our prefix never reaches the database.
    assert client.get(LIST, headers=_bearer("whatever")).status_code == 401


def test_a_revoked_credential_is_rejected(client: TestClient, db_session):
    _candidate(db_session)
    key, raw = _key(db_session)
    assert client.get(LIST, headers=_bearer(raw)).status_code == 200
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert client.get(LIST, headers=_bearer(raw)).status_code == 401


def test_an_expired_credential_is_rejected(client: TestClient, db_session):
    _candidate(db_session)
    key, raw = _key(db_session)
    key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert client.get(LIST, headers=_bearer(raw)).status_code == 401


def test_a_session_cookie_alone_cannot_read_the_api(client: TestClient, db_session):
    """No fallthrough: being signed in, or even being an admin, is not access."""
    user, _resume, _profile = _candidate(db_session)
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    assert client.get(LIST).status_code == 401
    client.cookies.clear()
    client.cookies.set(GUEST_COOKIE, "guest-key")
    assert client.get(LIST).status_code == 401


def test_using_a_key_records_that_it_was_used(client: TestClient, db_session):
    _candidate(db_session)
    key, raw = _key(db_session)
    assert key.last_used_at is None
    client.get(LIST, headers=_bearer(raw))
    db_session.expire_all()
    assert db_session.get(type(key), key.id).last_used_at is not None


# --- who is visible ---------------------------------------------------------


def test_a_consenting_candidate_is_listed_and_readable(client: TestClient, db_session):
    _user, _resume, profile = _candidate(db_session)
    _key_row, raw = _key(db_session)

    listed = client.get(LIST, headers=_bearer(raw))
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert [item["profile_id"] for item in body["items"]] == [profile.id]
    assert body["items"][0]["headline"] == "Backend engineer"

    detail = client.get(f"{LIST}/{profile.id}", headers=_bearer(raw))
    assert detail.status_code == 200
    assert detail.json()["profile"]["summary"] == "Builds payment systems."


def test_withdrawal_makes_the_profile_unreadable(client: TestClient, db_session):
    """The headline case. Read it, withdraw, and it is gone."""
    user, _resume, profile = _candidate(db_session)
    _key_row, raw = _key(db_session)
    assert client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 200

    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    assert (
        client.put(
            "/v1/me/consents/talent_introduction", json={"granted": False}
        ).status_code
        == 200
    )
    client.cookies.clear()

    assert client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 404
    assert client.get(LIST, headers=_bearer(raw)).json()["items"] == []
    # And the derived copy is gone, not merely hidden.
    assert db_session.query(TalentProfile).count() == 0


def test_a_profile_that_outlived_its_consent_is_still_excluded(
    client: TestClient, db_session
):
    """Gate 2 alone, with gate 1 deliberately broken.

    The row is inserted directly and the withdrawal appended after it, so the
    only thing that can hide this person is the live consent check.
    """
    user, _resume, profile = _candidate(db_session)
    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action="withdrawn",
        locale="en",
        source="settings",
    )
    db_session.commit()
    assert db_session.query(TalentProfile).count() == 1

    _key_row, raw = _key(db_session)
    assert client.get(LIST, headers=_bearer(raw)).json()["items"] == []
    assert client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 404


def test_the_withdrawal_wins_when_two_answers_share_a_timestamp(
    client: TestClient, db_session
):
    """Pins the tie rule: an arbitrary winner could expose a withdrawn person."""
    user, _resume, profile = _candidate(db_session, consent=None)
    same = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    for action in ("granted", "withdrawn"):
        db_session.add(
            ConsentEvent(
                user_id=user.id,
                purpose="talent_introduction",
                action=action,
                policy_version=POLICY_VERSION,
                locale="en",
                source="settings",
                created_at=same,
            )
        )
        db_session.flush()
    db_session.commit()

    _key_row, raw = _key(db_session)
    assert client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 404


def test_a_candidate_who_never_consented_is_404(client: TestClient, db_session):
    _user, _resume, profile = _candidate(db_session, consent=None)
    _key_row, raw = _key(db_session)
    assert client.get(LIST, headers=_bearer(raw)).json()["items"] == []
    assert client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 404


def test_a_profile_with_no_pointer_is_404(client: TestClient, db_session):
    """Gate 3: they stopped pointing at the resume this was built from."""
    _user, _resume, profile = _candidate(db_session, point=False)
    _key_row, raw = _key(db_session)
    assert client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 404


def test_a_profile_whose_resume_is_unpublished_is_404(client: TestClient, db_session):
    user, resume, profile = _candidate(db_session)
    _key_row, raw = _key(db_session)
    resume.published_revision_id = None
    db_session.commit()
    assert client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 404
    assert user is not None


def test_an_unknown_profile_id_is_404(client: TestClient, db_session):
    _key_row, raw = _key(db_session)
    assert client.get(f"{LIST}/does-not-exist", headers=_bearer(raw)).status_code == 404


# --- what the response carries ----------------------------------------------


def test_no_response_carries_contact_details_or_a_user_id(
    client: TestClient, db_session
):
    user, _resume, profile = _candidate(db_session)
    _key_row, raw = _key(db_session)

    for path in (LIST, f"{LIST}/{profile.id}"):
        blob = json.dumps(client.get(path, headers=_bearer(raw)).json())
        assert user.id not in blob
        assert user.email not in blob
        for field in ("email", "phone", "url", "contact", "handle"):
            assert field not in blob


def test_the_list_carries_summaries_and_not_histories(client: TestClient, db_session):
    _user, _resume, _profile = _candidate(db_session)
    _key_row, raw = _key(db_session)
    item = client.get(LIST, headers=_bearer(raw)).json()["items"][0]
    assert set(item) == {
        "profile_id",
        "display_name",
        "headline",
        "years_experience",
        "locations",
        "skills",
        "updated_at",
    }


# --- filtering --------------------------------------------------------------


def test_a_skill_filter_does_not_match_a_longer_skill(client: TestClient, db_session):
    _candidate(db_session, sub="sub-java", skills=("java",))
    _candidate(db_session, sub="sub-js", skills=("javascript",))
    _key_row, raw = _key(db_session)

    body = client.get(LIST, params={"skill": "java"}, headers=_bearer(raw)).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["skills"] == ["Java"]


def test_filters_combine(client: TestClient, db_session):
    _candidate(db_session, sub="sub-senior", skills=("go",), years=9)
    _candidate(db_session, sub="sub-junior", skills=("go",), years=2)
    _key_row, raw = _key(db_session)

    body = client.get(
        LIST, params={"skill": "go", "min_years": 5}, headers=_bearer(raw)
    ).json()
    assert [item["years_experience"] for item in body["items"]] == [9]


def test_the_q_filter_escapes_like_wildcards(client: TestClient, db_session):
    _candidate(db_session, sub="sub-q")
    _key_row, raw = _key(db_session)
    # '%' must be matched literally, not as "anything".
    assert client.get(LIST, params={"q": "%"}, headers=_bearer(raw)).json()["items"] == []
    assert client.get(LIST, params={"q": "_"}, headers=_bearer(raw)).json()["items"] == []
    assert len(client.get(LIST, params={"q": "backend"}, headers=_bearer(raw)).json()["items"]) == 1


def test_pagination_is_validated(client: TestClient, db_session):
    _candidate(db_session)
    _key_row, raw = _key(db_session)
    for params in ({"limit": 0}, {"limit": 51}, {"offset": -1}):
        assert client.get(LIST, params=params, headers=_bearer(raw)).status_code == 422


# --- the ledger and the budget ----------------------------------------------


def test_a_list_read_logs_one_disclosure_per_person(client: TestClient, db_session):
    _candidate(db_session, sub="sub-a")
    _candidate(db_session, sub="sub-b")
    _key_row, raw = _key(db_session)

    client.get(LIST, headers=_bearer(raw))
    rows = db_session.query(DisclosureEvent).all()
    assert len(rows) == 2
    assert {row.scope for row in rows} == {"summary"}
    assert {row.route for row in rows} == {"GET /v1/talent/profiles"}
    assert {row.recipient_contact for row in rows} == {"talent@acme.example"}


def test_a_detail_read_logs_a_full_disclosure(client: TestClient, db_session):
    _user, _resume, profile = _candidate(db_session)
    _key_row, raw = _key(db_session)
    client.get(f"{LIST}/{profile.id}", headers=_bearer(raw))
    row = db_session.query(DisclosureEvent).one()
    assert row.scope == "full"
    assert row.profile_content_hash == profile.content_hash
    assert row.policy_version == POLICY_VERSION


def test_the_hourly_budget_refuses_and_writes_nothing(client: TestClient, db_session):
    _user, _resume, profile = _candidate(db_session)
    key, raw = _key(db_session)
    # Fill the budget with rows for a payload nobody is about to read again.
    for index in range(DISCLOSURE_LIMIT_PER_HOUR):
        db_session.add(
            DisclosureEvent(
                user_id=None,
                api_key_id=key.id,
                recipient_label=key.label,
                recipient_contact=key.contact,
                purpose="talent_introduction",
                scope="summary",
                profile_content_hash=f"{index:064d}",
                policy_version=POLICY_VERSION,
                route="GET /v1/talent/profiles",
            )
        )
    db_session.commit()
    before = db_session.query(DisclosureEvent).count()

    assert client.get(LIST, headers=_bearer(raw)).status_code == 429
    assert (
        client.get(f"{LIST}/{profile.id}", headers=_bearer(raw)).status_code == 429
    )
    db_session.expire_all()
    assert db_session.query(DisclosureEvent).count() == before


# --- and nothing new on the open web ----------------------------------------


def test_the_talent_routes_are_absent_from_the_openapi_document(client: TestClient):
    """The reason include_in_schema=False is on the router.

    /openapi.json is served without authentication and the Next rewrite
    publishes it, so a path in here is a path described on the open web.
    """
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/v1/talent")]
    assert not [path for path in paths if path.startswith("/v1/admin/talent")]
