import pytest

from app.config import get_settings
from app.models import GuestSession
from app.services.rate_limit import (
    EXPORT_DOWNLOAD_LIMIT,
    EXPORT_DOWNLOAD_WINDOW,
    try_consume_export_download,
    try_consume_guest_rate,
)


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


def test_the_export_budget_reopens_once_the_window_passes():
    """The clock is monotonic, so drive it directly rather than sleeping. Both
    edges are pinned: a window read with the wrong sign, or one off by its own
    length, fails one of these four."""
    key = "resume-under-test"
    assert all(
        try_consume_export_download(key, now=1000.0)
        for _ in range(EXPORT_DOWNLOAD_LIMIT)
    )
    assert try_consume_export_download(key, now=1000.0) is False
    just_inside = 1000.0 + EXPORT_DOWNLOAD_WINDOW - 0.1
    assert try_consume_export_download(key, now=just_inside) is False
    just_past = 1000.0 + EXPORT_DOWNLOAD_WINDOW + 0.1
    assert try_consume_export_download(key, now=just_past) is True


def test_the_export_budget_is_on_outside_production(monkeypatch):
    """Deliberately unlike the guest limits above. A staging box compiles PDFs
    on the same CPU as production, and a guard that only exists in production
    is a guard no test ever runs."""
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    key = "resume-in-development"
    assert all(
        try_consume_export_download(key, now=2000.0)
        for _ in range(EXPORT_DOWNLOAD_LIMIT)
    )
    assert try_consume_export_download(key, now=2000.0) is False
