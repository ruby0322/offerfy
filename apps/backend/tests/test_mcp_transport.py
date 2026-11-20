"""The agent surface's door, and (from Task 4) the transport behind it.

The auth cases run against a standalone probe app, not app.main.app: a route
registered on the shared module-level app outlives the test that added it and
pollutes every later one. The `client` fixture is used only to stand up the
test database.
"""

from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_db, get_session_factory
from app.deps import SESSION_COOKIE, _sign, require_agent_caller
from app.services.agent_keys import mint_agent_key
from app.services.guest import GUEST_COOKIE
from app.services.talent_keys import mint_talent_key

PROBE = "/probe"


def _bearer(raw: str) -> dict:
    return {"Authorization": f"Bearer {raw}"}


@pytest.fixture
def probe(client):
    """A throwaway app whose one route sits behind the door."""
    app = FastAPI()

    @app.post(PROBE)
    def _probe(caller=Depends(require_agent_caller)):
        user, key = caller
        return {"email": user.email, "key_id": key.id}

    factory = get_session_factory()

    def _db():
        db = factory()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c


def test_no_authorization_header_is_401(probe):
    r = probe.post(PROBE)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_a_session_cookie_is_401(probe, a_user):
    probe.cookies.set(SESSION_COOKIE, _sign(a_user.id, get_settings().auth_token_secret))
    assert probe.post(PROBE).status_code == 401


def test_a_guest_cookie_is_401(probe):
    probe.cookies.set(GUEST_COOKIE, "whatever")
    assert probe.post(PROBE).status_code == 401


def test_a_talent_token_is_401(probe, db_session):
    _, raw = mint_talent_key(
        db_session, label="Acme", contact="a@b.example", created_by="ops@example.com"
    )
    db_session.commit()
    assert probe.post(PROBE, headers=_bearer(raw)).status_code == 401


def test_every_refusal_says_the_same_thing(probe, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    key, dead_raw = mint_agent_key(db_session, user=a_user, label="dead")
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    responses = [
        probe.post(PROBE),
        probe.post(PROBE, headers=_bearer("ofa_invented")),
        probe.post(PROBE, headers=_bearer(dead_raw)),
        probe.post(PROBE, headers={"Authorization": raw}),
    ]
    assert {r.status_code for r in responses} == {401}
    assert {r.json()["detail"] for r in responses} == {"Credential required"}
    assert {r.headers["www-authenticate"] for r in responses} == {"Bearer"}


def test_a_live_token_resolves_its_own_user(probe, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    r = probe.post(PROBE, headers=_bearer(raw))
    assert r.status_code == 200
    assert r.json()["email"] == a_user.email


def test_revoking_between_two_calls_closes_the_door(probe, db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    assert probe.post(PROBE, headers=_bearer(raw)).status_code == 200
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert probe.post(PROBE, headers=_bearer(raw)).status_code == 401


def test_a_key_whose_user_vanished_is_the_same_401(probe, db_session, a_user):
    from app.models import User

    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    # SQLite does not enforce the FK here, so the orphan can exist.
    db_session.query(User).filter(User.id == a_user.id).delete(synchronize_session=False)
    db_session.commit()
    r = probe.post(PROBE, headers=_bearer(raw))
    assert r.status_code == 401
    assert r.json()["detail"] == "Credential required"
