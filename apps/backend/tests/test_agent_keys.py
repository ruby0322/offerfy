from datetime import datetime, timedelta, timezone

import pytest

from app.services.agent_keys import (
    MAX_LIVE_KEYS,
    hash_agent_key,
    is_live,
    key_for_token,
    live_keys,
    mint_agent_key,
)
from app.services.talent_keys import mint_talent_key


def test_a_minted_key_is_live_and_found_by_its_token(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="laptop")
    db_session.commit()
    assert raw.startswith("ofa_")
    assert is_live(key)
    assert key_for_token(db_session, raw).id == key.id


def test_the_raw_token_is_not_stored(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="laptop")
    assert raw not in (key.key_hash or "")
    assert key.key_hash == hash_agent_key(raw)


def test_a_revoked_key_is_not_found(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="laptop")
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert key_for_token(db_session, raw) is None


def test_an_expired_key_is_not_found(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="x", expires_in_days=1)
    key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert key_for_token(db_session, raw) is None


def test_a_token_without_the_prefix_never_reaches_the_database(db_session, a_user):
    mint_agent_key(db_session, user=a_user, label="laptop")
    db_session.commit()
    assert key_for_token(db_session, "ofk_whatever") is None
    assert key_for_token(db_session, "nonsense") is None


def test_a_talent_token_is_not_an_agent_token(db_session, a_user):
    _, talent_raw = mint_talent_key(
        db_session, label="Acme", contact="a@b.example", created_by="ops@example.com"
    )
    db_session.commit()
    assert key_for_token(db_session, talent_raw) is None


def test_the_sixth_live_key_is_refused(db_session, a_user):
    for n in range(MAX_LIVE_KEYS):
        mint_agent_key(db_session, user=a_user, label=f"key-{n}")
    db_session.commit()
    with pytest.raises(ValueError):
        mint_agent_key(db_session, user=a_user, label="one too many")


def test_revoking_frees_a_slot(db_session, a_user):
    keys = [mint_agent_key(db_session, user=a_user, label=f"k{n}")[0]
            for n in range(MAX_LIVE_KEYS)]
    keys[0].revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert mint_agent_key(db_session, user=a_user, label="replacement")


def test_the_limit_is_per_user(db_session, a_user, other_user):
    for n in range(MAX_LIVE_KEYS):
        mint_agent_key(db_session, user=a_user, label=f"k{n}")
    db_session.commit()
    assert mint_agent_key(db_session, user=other_user, label="mine")
    assert len(live_keys(db_session, a_user)) == MAX_LIVE_KEYS
    assert len(live_keys(db_session, other_user)) == 1
