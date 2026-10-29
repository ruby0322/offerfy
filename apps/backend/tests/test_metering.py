"""The metering seam, and the inventory that replaces its missing guarantee.

`metered()` is a context manager at the call site, chosen over a required meter
argument threaded down to the provider call. That choice gives up a
compile-time guarantee: a new caller can skip the wrap, and the failure is
silent -- free turns, found from the invoice. `app/jobs/refresh_profiles.py`
did exactly that before this module existed.

`test_billable_call_sites_are_wrapped` is what replaces the guarantee. It is
load-bearing, not hygiene.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import get_settings
from app.models import GuestSession, UsageCounter, User
from app.services import usage
from app.services.metering import Meter, preflight, system_guard

APP = Path(__file__).resolve().parents[1] / "app"

# Every function that spends money with the provider. Adding one here without
# also wrapping its call sites is how this test earns its keep.
BILLABLE = ("aiter_chat_edit", "build_profile", "vision_report", "read_layout_report")

# The (module, function) pairs that exist today, each inside a `metered()`
# region or reached from one. Pairs, not individual lines: this is a set, so
# chat.py calling aiter_chat_edit twice (the turn and the compile-repair loop)
# is one entry. That is the right granularity -- a *new module* spending money,
# or a new billable function, is the mistake worth catching; a second call in a
# module that already meters correctly is not.
#
# `read_layout_report` and `vision_report` are reached from inside
# `aiter_chat_edit`, which the chat router already wraps, so they are covered
# transitively rather than wrapped again -- recording twice would double-bill.
EXPECTED_CALL_SITES = {
    ("routers/chat.py", "aiter_chat_edit"),       # the turn + the repair loop
    ("routers/talent_me.py", "build_profile"),    # pointer change + refresh
    ("jobs/refresh_profiles.py", "build_profile"),  # nightly, system_guard
    ("services/llm/turn.py", "read_layout_report"),  # inside aiter_chat_edit
    ("services/layout.py", "vision_report"),      # inside read_layout_report
}


def _call_sites() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(APP).as_posix()
        for name in BILLABLE:
            # A call, not the definition and not an import.
            for match in re.finditer(rf"(?<![\w.]){name}\(", text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line = text[line_start : text.find("\n", match.start())]
                if line.lstrip().startswith(("def ", "async def ", "from ", "import ")):
                    continue
                found.add((rel, name))
    return found


def test_billable_call_sites_are_wrapped():
    """Fails when a new provider call site appears.

    **Do not make this pass by editing EXPECTED_CALL_SITES alone.** Wrap the
    call in `metered()` first -- or, if it is genuinely exempt, in
    `system_guard()` with a written reason -- and only then record it here.
    Editing the expectation without wrapping the call is the single shortcut
    that defeats the whole design, because nothing else checks.
    """
    found = _call_sites()
    new = found - EXPECTED_CALL_SITES
    gone = EXPECTED_CALL_SITES - found
    assert not new, (
        f"new billable call site(s): {sorted(new)} — wrap in metered() "
        "(or system_guard() with a reason), then add to EXPECTED_CALL_SITES"
    )
    assert not gone, f"billable call site(s) removed: {sorted(gone)} — prune the list"


def test_every_module_that_calls_a_billable_function_imports_the_seam():
    """The weaker but sharper check: a module that spends must know the meter.

    Catches the realistic version of the mistake — a new router or job calling
    `build_profile` — without depending on where exactly the wrap sits.
    """
    exempt = {
        # Reached from inside aiter_chat_edit, which its caller wraps.
        "services/llm/turn.py",
        "services/layout.py",
    }
    for rel, _name in _call_sites():
        if rel in exempt:
            continue
        text = (APP / rel).read_text(encoding="utf-8")
        assert "from app.services.metering import" in text, (
            f"{rel} calls a billable function but does not import the metering "
            "seam — it is spending money without a meter"
        )


# --------------------------------------------------------------------------
# the meter itself
# --------------------------------------------------------------------------


def _user(db_session, sub="sub-meter"):
    user = User(google_sub=sub, email=f"{sub}@x.test", locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def _event(**kwargs):
    base = {
        "model": "gpt-5.6-terra",
        "purpose": "chat",
        "round_index": 0,
        "input_tokens": 10_000,
        "cached_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
    }
    return {**base, **kwargs}


def test_record_increments_the_counter_atomically(client, db_session):
    user = _user(db_session)
    guard = system_guard(user=user, purpose="chat", reason="test")
    meter = Meter(guard)
    meter.record(_event())
    meter.record(_event(round_index=1))

    start = usage.window_start()
    uusd, turns, unmeasured = usage.counter_totals(
        db_session, kind="user", subject_id=user.id, window_start=start
    )
    # 10k fresh input tokens at $2.00/1M is 20_000 uusd, twice.
    assert uusd == 40_000
    # Only round_index=0 of a chat turn is a turn.
    assert turns == 1
    assert unmeasured == 0
    # One row, not two: the UPSERT is an increment.
    assert db_session.query(UsageCounter).count() == 1


def test_an_unmeasured_round_is_counted_and_flagged(client, db_session):
    user = _user(db_session)
    meter = Meter(system_guard(user=user, purpose="chat", reason="test"))
    meter.record(_event(input_tokens=None))
    uusd, _turns, unmeasured = usage.counter_totals(
        db_session, kind="user", subject_id=user.id, window_start=usage.window_start()
    )
    assert uusd == usage.FALLBACK_ROUND_UUSD > 0
    assert unmeasured == 1


def test_a_guest_is_billed_against_its_network_key_too(client, db_session):
    """Task 8 needs no table of its own: 'network' is a third subject kind."""
    guest = GuestSession(key_hash="m" * 64)
    db_session.add(guest)
    db_session.commit()
    guard = preflight(
        db_session, user=None, guest=guest, purpose="chat", network_key="deadbeef"
    )
    Meter(guard).record(_event())

    start = usage.window_start()
    for kind, subject_id in (("guest", guest.id), ("network", "deadbeef")):
        uusd, turns, _ = usage.counter_totals(
            db_session, kind=kind, subject_id=subject_id, window_start=start
        )
        assert (uusd, turns) == (20_000, 1), kind


def test_spend_lands_in_the_window_it_happened_in(client, db_session):
    user = _user(db_session)
    last_week = usage.window_start() - timedelta(days=1)
    Meter(system_guard(user=user, purpose="chat", reason="test", now=last_week)).record(
        _event()
    )
    assert usage.counter_totals(
        db_session, kind="user", subject_id=user.id, window_start=usage.window_start()
    ) == (0, 0, 0)
    assert usage.balance(db_session, user, None).used_uusd == 0


def test_system_guard_demands_a_reason():
    with pytest.raises(ValueError):
        system_guard(purpose="profile", reason="  ")


def test_system_guard_does_not_enforce(client, db_session, monkeypatch):
    """The cron needs to record without being able to refuse itself."""
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    user = _user(db_session)
    tier = usage.TIERS[usage.REGISTERED]
    Meter(system_guard(user=user, purpose="chat", reason="test")).record(
        _event(input_tokens=tier.credits * usage.CREDIT_UUSD)
    )
    assert usage.balance(db_session, user, None).exhausted
    # preflight would refuse; system_guard still hands back a meter.
    with pytest.raises(Exception):
        preflight(db_session, user=user, guest=None, purpose="chat")
    guard = system_guard(user=user, purpose="profile", reason="nightly refresh")
    assert guard.enforced is False
    assert guard.exempt_reason == "nightly refresh"


def test_a_failed_increment_does_not_raise(client, db_session, monkeypatch):
    """The tokens are already spent, so failing the response would lose the
    person's work *and* the money. Unbilled, logged, and caught by the
    LlmCall reconciliation instead."""
    user = _user(db_session)
    meter = Meter(system_guard(user=user, purpose="chat", reason="test"))
    monkeypatch.setattr(
        "app.services.metering._upsert",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    meter.record(_event())  # must not raise
    assert usage.counter_totals(
        db_session, kind="user", subject_id=user.id, window_start=usage.window_start()
    ) == (0, 0, 0)


def test_reconcile_spots_a_counter_with_no_telemetry_behind_it(client, db_session):
    """A gap is how a missed wrap surfaces when a grep cannot see it."""
    user = _user(db_session)
    Meter(system_guard(user=user, purpose="chat", reason="test")).record(_event())
    gaps = usage.reconcile(db_session, window_start=usage.window_start())
    # The counter moved but no LlmCall row was written, so they disagree.
    assert gaps == [("user", user.id, 20_000, 0)]
