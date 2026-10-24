import pytest

from app.config import get_settings
from app.models import GuestSession
from app.services.rate_limit import try_consume_guest_rate


def _guest(db):
    guest = GuestSession(key_hash="a" * 64)
    db.add(guest)
    db.flush()
    return guest


def test_guest_export_not_rate_limited_in_development(db_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    guest = _guest(db_session)
    assert all(try_consume_guest_rate(db_session, guest.id, "export") for _ in range(25))


def test_guest_export_rate_limited_in_production(db_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    guest = _guest(db_session)
    assert all(try_consume_guest_rate(db_session, guest.id, "export") for _ in range(20))
    assert try_consume_guest_rate(db_session, guest.id, "export") is False


def test_chat_is_no_longer_a_rate_event_kind(db_session, monkeypatch):
    """Chat metering lives in app.services.usage now.

    Asserted rather than assumed: a 'chat' key quietly reappearing here would
    give chat two independent limiters that disagree.
    """
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    guest = _guest(db_session)
    with pytest.raises(KeyError):
        try_consume_guest_rate(db_session, guest.id, "chat")
