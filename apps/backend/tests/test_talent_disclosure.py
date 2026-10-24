from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import (
    DisclosureEvent,
    Resume,
    StopUseNotice,
    TalentApiKey,
    TalentProfile,
    User,
)
from app.services.consent import record_consent
from app.services.disclosure import (
    disclosure_count,
    pending_obligations,
    record_disclosure,
    record_disclosures,
    record_stop_use_notice,
    recipients_of,
)
from app.services.talent_keys import hash_talent_key


def _user(db_session, sub="sub-disc", email=None):
    user = User(
        google_sub=sub, email=email or f"{sub}@example.com", locale="en", name="Chen Wei"
    )
    db_session.add(user)
    db_session.commit()
    return user


def _sign_in(client, user):
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))


def _profile(db_session, user, *, content_hash="a" * 64):
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
    user.profile_resume_id = resume.id
    profile = TalentProfile(
        user_id=user.id,
        source_resume_id=resume.id,
        source_revision_id="rev-1",
        input_hash="h" * 64,
        profile_json='{"display_name": "Chen Wei"}',
        content_hash=content_hash,
        display_name="Chen Wei",
        search_text="chen wei",
        skills_text="|python|",
    )
    db_session.add(profile)
    db_session.commit()
    return profile


def _key(db_session, label="Acme Talent", contact="talent@acme.example"):
    key = TalentApiKey(
        key_hash=hash_talent_key(f"ofk_{label}"),
        label=label,
        contact=contact,
        created_by="ops@example.com",
    )
    db_session.add(key)
    db_session.commit()
    return key


def _consent(db_session, user, action):
    record_consent(
        db_session,
        user=user,
        purpose="talent_introduction",
        action=action,
        locale="en",
        source="settings",
    )
    db_session.commit()


def _identities(db_session):
    return {
        (row.id, row.created_at, row.profile_content_hash, row.recipient_contact)
        for row in db_session.query(DisclosureEvent).all()
    }


# --- writing ----------------------------------------------------------------


def test_a_disclosure_names_the_recipient_and_the_payload(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)

    record_disclosure(
        db_session, profile=profile, key=key, scope="full", route="GET /v1/talent/x"
    )
    db_session.commit()

    row = db_session.query(DisclosureEvent).one()
    assert row.user_id == user.id
    assert row.api_key_id == key.id
    assert row.recipient_label == "Acme Talent"
    assert row.recipient_contact == "talent@acme.example"
    assert row.scope == "full"
    assert row.profile_content_hash == profile.content_hash
    assert row.purpose == "talent_introduction"


def test_a_list_read_writes_one_row_per_returned_profile(client: TestClient, db_session):
    """The obligation is per (recipient, subject), not per request."""
    first = _profile(db_session, _user(db_session, sub="sub-one"), content_hash="1" * 64)
    second = _profile(db_session, _user(db_session, sub="sub-two"), content_hash="2" * 64)
    key = _key(db_session)

    written = record_disclosures(
        db_session,
        profiles=[first, second],
        key=key,
        scope="summary",
        route="GET /v1/talent/profiles",
    )
    db_session.commit()
    assert written == 2
    assert db_session.query(DisclosureEvent).count() == 2


def test_identical_reads_within_a_day_are_not_duplicated(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    for _ in range(3):
        record_disclosure(
            db_session, profile=profile, key=key, scope="full", route="GET /x"
        )
        db_session.commit()
    assert db_session.query(DisclosureEvent).count() == 1


def test_a_changed_payload_writes_a_new_row_within_the_day(
    client: TestClient, db_session
):
    """Dedup is on the payload hash: a rebuilt profile is a new disclosure."""
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()

    profile.content_hash = "b" * 64
    db_session.commit()
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()

    assert db_session.query(DisclosureEvent).count() == 2


def test_a_different_scope_is_its_own_disclosure(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    for scope in ("summary", "full"):
        record_disclosure(
            db_session, profile=profile, key=key, scope=scope, route="GET /x"
        )
        db_session.commit()
    assert db_session.query(DisclosureEvent).count() == 2


# --- the ledger outlives everything -----------------------------------------


def test_disclosure_rows_are_never_updated_or_deleted(client: TestClient, db_session):
    """Withdraw, notify, revoke the key, delete the profile. Nothing changes."""
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    _consent(db_session, user, "granted")
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()
    before = _identities(db_session)
    assert len(before) == 1

    _sign_in(client, user)
    client.put("/v1/me/consents/talent_introduction", json={"granted": False})
    record_stop_use_notice(
        db_session,
        user=user,
        recipient_contact=key.contact,
        sent_by="ops@example.com",
    )
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    db_session.expire_all()

    assert db_session.query(TalentProfile).count() == 0
    assert _identities(db_session) == before


def test_deleting_the_key_leaves_the_row_naming_its_recipient(
    client: TestClient, db_session
):
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()

    db_session.delete(key)
    db_session.commit()
    db_session.expire_all()

    row = db_session.query(DisclosureEvent).one()
    assert row.api_key_id is None
    assert row.recipient_label == "Acme Talent"
    assert row.recipient_contact == "talent@acme.example"


def test_erasing_the_account_leaves_the_row_with_no_subject(
    client: TestClient, db_session
):
    """The ConsentEvent property: we must be able to show what we disclosed
    about data we no longer hold.

    Erasure is done in SQL here because the product has no account deletion
    yet (the privacy policy says so) and the ORM relationship would try to
    orphan the resume rather than remove it. What is being checked is the
    database's own behaviour: SET NULL on the subject, CASCADE on the copy.
    """
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()
    user_id = user.id

    user.profile_resume_id = None
    db_session.commit()
    db_session.execute(text("DELETE FROM resumes WHERE user_id = :id"), {"id": user_id})
    db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    db_session.commit()
    db_session.expire_all()

    row = db_session.query(DisclosureEvent).one()
    assert row.user_id is None
    assert row.recipient_contact == "talent@acme.example"
    # The profile was a copy of personal data, so it went with the account.
    assert db_session.query(TalentProfile).count() == 0


# --- the obligation queue ---------------------------------------------------


def test_withdrawal_queues_every_prior_recipient(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    acme = _key(db_session, label="Acme", contact="a@acme.example")
    beta = _key(db_session, label="Beta", contact="b@beta.example")
    _consent(db_session, user, "granted")
    for key in (acme, beta):
        record_disclosure(
            db_session, profile=profile, key=key, scope="full", route="GET /x"
        )
    db_session.commit()

    _consent(db_session, user, "withdrawn")
    rows = pending_obligations(db_session)
    assert {row["recipient_contact"] for row in rows} == {
        "a@acme.example",
        "b@beta.example",
    }
    assert all(row["user_id"] == user.id for row in rows)
    assert all(row["disclosure_count"] == 1 for row in rows)


def test_a_user_who_never_withdrew_owes_nothing(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    _consent(db_session, user, "granted")
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()
    assert pending_obligations(db_session) == []


def test_a_disclosure_after_the_withdrawal_is_not_an_obligation(
    client: TestClient, db_session
):
    """Only what they already had when they withdrew has to be recalled."""
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    _consent(db_session, user, "granted")
    _consent(db_session, user, "withdrawn")

    # Inserted with a later timestamp than the withdrawal.
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.add(
        DisclosureEvent(
            user_id=user.id,
            api_key_id=key.id,
            recipient_label=key.label,
            recipient_contact=key.contact,
            purpose="talent_introduction",
            scope="full",
            profile_content_hash=profile.content_hash,
            policy_version="2026-10-24",
            route="GET /x",
            created_at=later,
        )
    )
    db_session.commit()
    assert pending_obligations(db_session) == []


def test_recording_a_notice_clears_that_recipient_only(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    acme = _key(db_session, label="Acme", contact="a@acme.example")
    beta = _key(db_session, label="Beta", contact="b@beta.example")
    _consent(db_session, user, "granted")
    for key in (acme, beta):
        record_disclosure(
            db_session, profile=profile, key=key, scope="full", route="GET /x"
        )
    db_session.commit()
    _consent(db_session, user, "withdrawn")

    record_stop_use_notice(
        db_session,
        user=user,
        recipient_contact="a@acme.example",
        sent_by="ops@example.com",
        api_key_id=acme.id,
    )
    db_session.commit()

    rows = pending_obligations(db_session)
    assert [row["recipient_contact"] for row in rows] == ["b@beta.example"]
    notice = db_session.query(StopUseNotice).one()
    assert notice.channel == "manual_email"
    assert notice.sent_by == "ops@example.com"
    assert notice.withdrawn_consent_event_id is not None


def test_re_granting_then_withdrawing_again_reopens_the_obligation(
    client: TestClient, db_session
):
    """A notice discharges one withdrawal, not the person forever."""
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    _consent(db_session, user, "granted")
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()
    _consent(db_session, user, "withdrawn")
    record_stop_use_notice(
        db_session,
        user=user,
        recipient_contact=key.contact,
        sent_by="ops@example.com",
    )
    db_session.commit()
    assert pending_obligations(db_session) == []

    _consent(db_session, user, "granted")
    _consent(db_session, user, "withdrawn")
    rows = pending_obligations(db_session)
    assert [row["recipient_contact"] for row in rows] == [key.contact]


def test_recipients_of_lists_everyone_who_ever_read_it(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    record_disclosure(
        db_session, profile=profile, key=key, scope="summary", route="GET /list"
    )
    record_disclosure(
        db_session, profile=profile, key=key, scope="full", route="GET /one"
    )
    db_session.commit()

    rows = recipients_of(db_session, user)
    assert len(rows) == 1
    assert rows[0]["recipient_contact"] == key.contact
    assert rows[0]["disclosure_count"] == 2


# --- what the owner and the operator see ------------------------------------


def test_owner_status_reports_the_disclosure_count(client: TestClient, db_session):
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    _consent(db_session, user, "granted")
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()

    _sign_in(client, user)
    body = client.get("/v1/me/talent-profile").json()
    assert body["disclosure_count"] == 1
    assert body["last_disclosed_at"] is not None
    assert disclosure_count(db_session, user) == 1


def test_the_count_survives_a_withdrawal(client: TestClient, db_session):
    """The profile goes; the record of who had it does not."""
    user = _user(db_session)
    profile = _profile(db_session, user)
    key = _key(db_session)
    _consent(db_session, user, "granted")
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()

    _sign_in(client, user)
    client.put("/v1/me/consents/talent_introduction", json={"granted": False})
    body = client.get("/v1/me/talent-profile").json()
    assert body["profile"] is None
    assert body["disclosure_count"] == 1


def test_obligation_routes_require_admin(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    user = _user(db_session, sub="sub-not-admin")
    _sign_in(client, user)
    assert client.get("/v1/admin/talent/obligations").status_code == 404
    assert (
        client.post(
            f"/v1/admin/talent/obligations/{user.id}/notices",
            json={"recipient_contact": "a@acme.example"},
        ).status_code
        == 404
    )
    assert client.get("/v1/admin/talent/disclosures").status_code == 404


def test_an_admin_can_work_the_queue(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    user = _user(db_session, sub="sub-subject")
    profile = _profile(db_session, user)
    key = _key(db_session)
    _consent(db_session, user, "granted")
    record_disclosure(db_session, profile=profile, key=key, scope="full", route="GET /x")
    db_session.commit()
    _consent(db_session, user, "withdrawn")

    admin = _user(db_session, sub="sub-ops", email="ops@example.com")
    _sign_in(client, admin)

    queue = client.get("/v1/admin/talent/obligations").json()
    assert queue["total"] == 1
    assert queue["items"][0]["recipient_contact"] == key.contact

    created = client.post(
        f"/v1/admin/talent/obligations/{user.id}/notices",
        json={"recipient_contact": key.contact, "api_key_id": key.id},
    )
    assert created.status_code == 200
    assert created.json()["sent_by"] == "ops@example.com"
    assert client.get("/v1/admin/talent/obligations").json()["total"] == 0

    ledger = client.get("/v1/admin/talent/disclosures").json()
    assert ledger["total"] == 1
    assert ledger["items"][0]["profile_content_hash"] == profile.content_hash


def test_talent_admin_routes_are_absent_from_the_openapi_document(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/v1/admin/talent")]
