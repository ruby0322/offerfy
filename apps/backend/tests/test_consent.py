import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import ConsentEvent, GuestSession, Resume, TalentProfile, User
from app.services.consent import (
    POLICY_VERSION,
    has_consent,
    has_consent_many,
    record_consent,
)

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


class _FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _fake_httpx(sub="g-consent", email="consent@example.com"):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None):
            return _FakeResp(200, {"access_token": "tok"})

        def get(self, url, headers=None):
            return _FakeResp(200, {"sub": sub, "email": email})

    return _Client


def _google_login(client, monkeypatch, **kwargs):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _fake_httpx(**kwargs))
    return client.get(
        "/v1/auth/google/callback", params={"code": "abc"}, follow_redirects=False
    )


def _signed_in(client, db_session, sub="sub-consent"):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    return user


def _events(db_session, **filters):
    query = db_session.query(ConsentEvent)
    for column, value in filters.items():
        query = query.filter(getattr(ConsentEvent, column) == value)
    return query.order_by(ConsentEvent.created_at, ConsentEvent.id).all()


def test_signup_records_service_consent(client: TestClient, db_session, monkeypatch):
    assert _google_login(client, monkeypatch).status_code == 302
    rows = _events(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert (row.purpose, row.action, row.source) == ("service", "granted", "signup")
    assert row.policy_version == POLICY_VERSION
    assert row.locale == "zh-TW"
    assert row.user_id is not None
    assert row.guest_session_id is None


def test_repeat_login_does_not_duplicate_consent(
    client: TestClient, db_session, monkeypatch
):
    _google_login(client, monkeypatch)
    _google_login(client, monkeypatch)
    # The account consented once, under one policy version. Signing in again is
    # not a new decision.
    assert len(_events(db_session, source="signup")) == 1


def test_claim_records_consent_covering_the_claimed_resumes(
    client: TestClient, db_session, monkeypatch
):
    client.post("/v1/resumes", json={"locale": "en", "title": "M"})
    _signed_in(client, db_session, sub="sub-claim")
    assert client.post("/v1/auth/claim").json()["claimed"] == 1
    rows = _events(db_session, source="claim")
    assert len(rows) == 1
    assert rows[0].detail == "claimed=1"
    # A guest is shown no notice, so nothing may be recorded against one.
    assert _events(db_session, source="guest_landing") == []
    assert [r for r in _events(db_session) if r.guest_session_id is not None] == []


def test_claim_with_nothing_to_claim_records_nothing(
    client: TestClient, db_session, monkeypatch
):
    _signed_in(client, db_session, sub="sub-empty-claim")
    assert client.post("/v1/auth/claim").json()["claimed"] == 0
    assert _events(db_session, source="claim") == []


def test_has_consent_reads_the_latest_row_not_any_row(client: TestClient, db_session):
    user = _signed_in(client, db_session, sub="sub-latest")
    for action in ("granted", "withdrawn"):
        record_consent(
            db_session,
            user=user,
            purpose="talent_introduction",
            action=action,
            locale="en",
            source="settings",
        )
        db_session.commit()
    assert has_consent(db_session, user=user, purpose="talent_introduction") is False
    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action="granted",
        locale="en",
        source="settings",
    )
    db_session.commit()
    assert has_consent(db_session, user=user, purpose="talent_introduction") is True


def test_a_row_cannot_name_both_a_user_and_a_guest(client: TestClient, db_session):
    user = _signed_in(client, db_session, sub="sub-both")
    guest = GuestSession(key_hash="deadbeef", locale="en")
    db_session.add(guest)
    db_session.commit()
    db_session.add(
        ConsentEvent(
            user_id=user.id,
            guest_session_id=guest.id,
            purpose="service",
            action="granted",
            policy_version=POLICY_VERSION,
            locale="en",
            source="signup",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_get_consents_requires_sign_in(client: TestClient):
    assert client.get("/v1/me/consents").status_code == 401


def test_talent_introduction_is_off_by_default(client: TestClient, db_session):
    _signed_in(client, db_session, sub="sub-default")
    body = client.get("/v1/me/consents").json()
    assert body["consents"] == {"talent_introduction": False}
    assert body["policy_version"] == POLICY_VERSION


def test_put_consent_appends_instead_of_updating(client: TestClient, db_session):
    user = _signed_in(client, db_session, sub="sub-toggle")
    path = "/v1/me/consents/talent_introduction"
    for granted in (True, False, True):
        body = client.put(path, json={"granted": granted}).json()
        assert body["consents"]["talent_introduction"] is granted
    rows = _events(db_session, user_id=user.id, purpose="talent_introduction")
    assert [r.action for r in rows] == ["granted", "withdrawn", "granted"]
    assert {r.source for r in rows} == {"settings"}
    assert client.get(path.rsplit("/", 1)[0]).json()["consents"][
        "talent_introduction"
    ] is True


def test_put_unknown_purpose_is_404(client: TestClient, db_session):
    _signed_in(client, db_session, sub="sub-unknown")
    # 'service' is a real purpose but not one a toggle may withdraw.
    for purpose in ("service", "nonsense"):
        response = client.put(f"/v1/me/consents/{purpose}", json={"granted": False})
        assert response.status_code == 404


def test_policy_version_matches_the_frontend_constant():
    """The policy text and the version recorded against it must not drift.

    The text lives in the frontend; the ledger lives here. Nothing else ties
    the two together, so this test does.
    """
    source = (_FRONTEND / "components/legal/LegalDocument.tsx").read_text()
    match = re.search(r'LEGAL_UPDATED\s*=\s*"([^"]+)"', source)
    assert match, "LEGAL_UPDATED not found in LegalDocument.tsx"
    assert match.group(1) == POLICY_VERSION


@pytest.mark.parametrize("locale", ["en", "zh-TW", "zh-CN"])
def test_every_locale_carries_the_same_legal_and_settings_keys(locale):
    """Messages types are derived from zh-TW only, so en/zh-CN gaps compile.

    The privacy policy is the notice itself -- a locale missing a section is a
    person who was never told. There is no frontend test runner, so it is
    checked here.
    """

    def flat(node, prefix=""):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from flat(value, f"{prefix}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from flat(value, f"{prefix}[{index}]")
        else:
            yield prefix

    def load(name):
        return json.loads((_FRONTEND / "messages" / f"{name}.json").read_text())

    reference, other = load("zh-TW"), load(locale)
    for namespace in ("legal", "settings"):
        assert set(flat(other[namespace])) == set(flat(reference[namespace])), namespace


def _profile_for(db_session, user, *, title="Master"):
    """A talent_profiles row, built by hand: step 2 has no builder yet."""
    resume = Resume(
        title=title,
        typst_source="= CV",
        source="create",
        locale="en",
        user_id=user.id,
        kind="master",
    )
    db_session.add(resume)
    db_session.flush()
    user.profile_resume_id = resume.id
    profile = TalentProfile(
        user_id=user.id,
        source_resume_id=resume.id,
        source_revision_id="rev-1",
        input_hash="h" * 64,
        profile_json='{"display_name": "Chen Wei"}',
        content_hash="c" * 64,
        display_name="Chen Wei",
        search_text="chen wei",
        skills_text="|python|",
    )
    db_session.add(profile)
    db_session.commit()
    return profile


def test_withdrawing_talent_introduction_deletes_the_profile(
    client: TestClient, db_session
):
    """The policy says we stop passing it on. The cheapest way is to not hold it."""
    user = _signed_in(client, db_session, sub="sub-withdraw-profile")
    _profile_for(db_session, user)
    client.put("/v1/me/consents/talent_introduction", json={"granted": True})
    assert db_session.query(TalentProfile).count() == 1

    response = client.put(
        "/v1/me/consents/talent_introduction", json={"granted": False}
    )
    assert response.status_code == 200
    assert response.json()["consents"]["talent_introduction"] is False
    assert db_session.query(TalentProfile).count() == 0


def test_granting_again_does_not_resurrect_the_old_profile(
    client: TestClient, db_session
):
    user = _signed_in(client, db_session, sub="sub-regrant")
    _profile_for(db_session, user)
    client.put("/v1/me/consents/talent_introduction", json={"granted": False})
    client.put("/v1/me/consents/talent_introduction", json={"granted": True})
    assert db_session.query(TalentProfile).count() == 0
    # The pointer survives, so turning it back on costs one rebuild and not a
    # second decision about which resume represents them.
    db_session.refresh(user)
    assert user.profile_resume_id is not None


def test_withdrawing_keeps_the_consent_rows_that_came_before(
    client: TestClient, db_session
):
    user = _signed_in(client, db_session, sub="sub-ledger-intact")
    _profile_for(db_session, user)
    client.put("/v1/me/consents/talent_introduction", json={"granted": True})
    client.put("/v1/me/consents/talent_introduction", json={"granted": False})
    actions = [
        row.action for row in _events(db_session, purpose="talent_introduction")
    ]
    assert actions == ["granted", "withdrawn"]


def test_has_consent_many_reads_the_latest_row_per_user(client: TestClient, db_session):
    granted = _signed_in(client, db_session, sub="sub-many-granted")
    withdrawn = _signed_in(client, db_session, sub="sub-many-withdrawn")
    silent = _signed_in(client, db_session, sub="sub-many-silent")
    for user, actions in ((granted, ["granted"]), (withdrawn, ["granted", "withdrawn"])):
        for action in actions:
            record_consent(
                db_session,
                user=user,
                purpose="talent_introduction",
                action=action,
                locale="en",
                source="settings",
            )
            db_session.commit()

    allowed = has_consent_many(
        db_session,
        [granted.id, withdrawn.id, silent.id],
        purpose="talent_introduction",
    )
    assert allowed == {granted.id}
    assert has_consent_many(db_session, [], purpose="talent_introduction") == set()


def test_has_consent_many_prefers_the_withdrawal_when_timestamps_tie(
    client: TestClient, db_session
):
    """Two rows can land in one transaction with identical created_at.

    A SQL max(created_at) join cannot break that tie and would read the
    withdrawal as a grant, which is the one mistake that cannot be taken back.
    """
    user = _signed_in(client, db_session, sub="sub-tie")
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

    assert has_consent(db_session, user=user, purpose="talent_introduction") is False
    assert (
        has_consent_many(db_session, [user.id], purpose="talent_introduction") == set()
    )
