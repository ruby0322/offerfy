from app.config import get_settings
from app.models import GuestSession
from app.services.rate_limit import try_consume_guest_rate


def _guest(db):
    guest = GuestSession(key_hash="a" * 64)
    db.add(guest)
    db.flush()
    return guest


def test_guest_chat_not_rate_limited_in_development(db_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    guest = _guest(db_session)
    assert all(try_consume_guest_rate(db_session, guest.id, "chat") for _ in range(15))


def test_guest_chat_rate_limited_in_production(db_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    guest = _guest(db_session)
    assert all(try_consume_guest_rate(db_session, guest.id, "chat") for _ in range(10))
    assert try_consume_guest_rate(db_session, guest.id, "chat") is False
