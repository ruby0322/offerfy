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
from app.models import GuestSession, LlmCall, UsageCounter, User
from app.services import usage
from app.services.metering import (
    Guard,
    Meter,
    _subjects_for,
    deferred_guard,
    preflight,
    system_guard,
)

APP = Path(__file__).resolve().parents[1] / "app"

# Every function that spends money with the provider. Adding one here without
# also wrapping its call sites is how this test earns its keep.
BILLABLE = (
    "aiter_chat_edit",
    "build_profile",
    "build_facts",
    "extract_facts",
    "vision_report",
    "read_layout_report",
    "extract_content",
    "_score_pair",
    "_write_call",
)

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
    ("services/profile.py", "extract_facts"),     # inside build_profile; its callers wrap
    ("services/resume_facts.py", "extract_facts"),  # inside build_facts; its caller wraps
    ("routers/preferences.py", "build_facts"),    # resume-facts refresh
    ("services/import_runner.py", "aiter_chat_edit"),  # one turn, deferred_guard
    ("routers/talent_me.py", "build_profile"),    # pointer change + refresh
    ("jobs/refresh_profiles.py", "build_profile"),  # nightly, system_guard
    ("services/llm/turn.py", "read_layout_report"),  # inside aiter_chat_edit
    ("services/layout.py", "vision_report"),      # inside read_layout_report
    ("routers/resumes.py", "extract_content"),    # deterministic template switch
    ("services/jobs/score.py", "_score_pair"),    # inside score_candidates, system_guard(purpose="match")
    ("services/jobs/reasons.py", "_write_call"),  # inside write_reasons, system_guard(purpose="digest")
    # The weekly digest rebuilds stale facts before it scores against them, so
    # the build is a third spender -- system_guard(purpose="profile"), the same
    # purpose and the same wrap as the two callers above it in this list.
    ("jobs/build_digests.py", "build_facts"),
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
        # extract_facts is the provider call itself. These two modules reach it
        # only from inside build_profile (whose callers carry the metered()
        # wrap; wrapping here too would record twice) and build_facts, whose
        # caller (routers/preferences.py) carries the metered() wrap.
        "services/profile.py",
        "services/resume_facts.py",
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


def _own_spend_guard(user=None, guest=None, purpose="chat", now=None):
    """A Guard that bills this PERSON, without enforcing.

    `system_guard` deliberately bills subject kind 'system' so background work
    never spends the person's allowance, which makes it the wrong shim for a
    test that is simulating the person's own spend. This builds the subjects
    `preflight` would have, without preflight's 429.
    """
    return Guard(
        subjects=_subjects_for(user, guest),
        purpose=purpose,
        window_start=usage.window_start(now),
    )


def test_record_increments_the_counter_atomically(client, db_session):
    user = _user(db_session)
    guard = _own_spend_guard(user=user)
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
    meter = Meter(_own_spend_guard(user=user))
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
    Meter(_own_spend_guard(user=user, now=last_week)).record(
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
    Meter(_own_spend_guard(user=user)).record(
        _event(input_tokens=tier.credits * usage.CREDIT_UUSD)
    )
    assert usage.balance(db_session, user, None).exhausted
    # preflight would refuse; system_guard still hands back a meter.
    with pytest.raises(Exception):
        preflight(db_session, user=user, guest=None, purpose="chat")
    guard = system_guard(user=user, purpose="profile", reason="nightly refresh")
    assert guard.enforced is False
    assert guard.exempt_reason == "nightly refresh"


def _system_meter(user, purpose="profile"):
    """A meter for background work. `profile` is the nightly refresh's purpose
    and the one the digest's facts rebuild uses -- and, unlike `match` and
    `digest`, one `llm_calls.purpose` admits (see models.LlmCall)."""
    return Meter(
        system_guard(user=user, purpose=purpose, reason="background work, no HTTP caller")
    )


def test_system_spend_is_billed_to_the_system_with_the_persons_id(client, db_session):
    """Subject KIND moves, subject ID does not.

    The kind is what decides whose allowance it is. The id is what keeps the
    cost attributable -- "this digest cost 20,000 uUSD and here is whose it
    was" stays answerable, it is just not charged to them.
    """
    user = _user(db_session, sub="sub-sys-subject")
    _system_meter(user).record(_event(purpose="profile"))
    start = usage.window_start()
    assert usage.counter_totals(
        db_session, kind="system", subject_id=user.id, window_start=start
    ) == (20_000, 0, 0)
    assert usage.counter_totals(
        db_session, kind="user", subject_id=user.id, window_start=start
    ) == (0, 0, 0)


def test_system_spend_does_not_move_the_persons_balance(client, db_session, monkeypatch):
    """Property 2: a digest build costs the person nothing they can feel.

    Every number the editor shows them -- credits used, credits left, "about N
    edits left" -- is identical either side of real background spend. This is
    the defect the subject kind exists to fix: `balance()` reads kind='user',
    so billing background work there quietly took resume edits away from
    someone for work they never asked for.
    """
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    user = _user(db_session, sub="sub-sys-balance")
    before = usage.balance(db_session, user, None)
    edits_before = usage.approx_edits_left(db_session, user, None, before)

    for _ in range(5):
        _system_meter(user).record(_event(purpose="profile", input_tokens=200_000))

    after = usage.balance(db_session, user, None)
    assert (after.used_uusd, after.remaining_uusd) == (
        before.used_uusd, before.remaining_uusd
    )
    assert after.turns_used == before.turns_used
    assert usage.approx_edits_left(db_session, user, None, after) == edits_before
    # And they are still allowed to edit, which is the point.
    assert usage.enforce_usage(db_session, user, None, purpose="chat") is not None


def test_system_spend_still_trips_the_global_ceiling(client, db_session, monkeypatch):
    """Property 1: taken off the person's bill, not off the books.

    `window_totals` sums every subject kind, so the ceiling that stops the
    instance spending the month's budget in a day sees background work exactly
    as it did before. Here it is the ONLY spend there is, and it still bites.
    """
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    monkeypatch.setenv("GLOBAL_WEEKLY_CEILING_UUSD", "30000")
    get_settings.cache_clear()
    spender = _user(db_session, sub="sub-sys-ceiling")
    _system_meter(spender).record(_event(purpose="profile"))
    _system_meter(spender).record(_event(purpose="profile"))

    start = usage.window_start()
    assert usage.window_totals(db_session, window_start=start) == 40_000
    # Somebody else's HTTP turn, refused by the ceiling the digest filled.
    other = _user(db_session, sub="sub-sys-bystander")
    with pytest.raises(Exception) as caught:
        usage.enforce_usage(db_session, other, None, purpose="chat")
    assert caught.value.detail["limit"] == "global_ceiling"


def test_enforcement_for_real_callers_is_unchanged(client, db_session, monkeypatch):
    """Property 3: only the system half moved.

    `preflight` still bills the person, still counts their turns and still
    refuses them when they are out -- a guest is still billed twice, against
    the cookie session and the network key. Nothing about the enforced path
    reads or writes the new kind.
    """
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    user = _user(db_session, sub="sub-enforced-unchanged")
    Meter(preflight(db_session, user=user, guest=None, purpose="chat")).record(_event())
    start = usage.window_start()
    assert usage.counter_totals(
        db_session, kind="user", subject_id=user.id, window_start=start
    ) == (20_000, 1, 0)
    assert usage.balance(db_session, user, None).used_uusd == 20_000
    assert db_session.query(UsageCounter).filter(
        UsageCounter.subject_kind == "system"
    ).count() == 0

    # And the refusal still arrives when the allowance is gone.
    tier = usage.TIERS[usage.REGISTERED]
    Meter(_own_spend_guard(user=user)).record(
        _event(input_tokens=tier.credits * usage.CREDIT_UUSD)
    )
    with pytest.raises(Exception):
        preflight(db_session, user=user, guest=None, purpose="chat")


def test_deferred_guard_bills_the_person_without_enforcing(client, db_session, monkeypatch):
    """The third door: their work, their bill, no 429.

    `preflight` would refuse an import halfway through with nobody watching;
    `system_guard` would take the cost off their allowance for work they asked
    for. This is the corner where the two axes disagree.
    """
    monkeypatch.setenv("USAGE_LIMITS_ENABLED", "true")
    get_settings.cache_clear()
    user = _user(db_session, sub="sub-deferred")
    tier = usage.TIERS[usage.REGISTERED]
    guard = deferred_guard(user=user, purpose="chat", reason="import they asked for")
    assert guard.subjects == (("user", user.id),)
    assert guard.enforced is False and guard.exempt_reason == "import they asked for"

    Meter(guard).record(_event(input_tokens=tier.credits * usage.CREDIT_UUSD))
    # Billed to them, counted as their turn, and now out of credits -- all the
    # things system_guard deliberately does not do.
    assert usage.balance(db_session, user, None).exhausted
    assert usage.balance(db_session, user, None).turns_used == 1
    # But it never refused itself on the way.
    assert deferred_guard(
        user=user, purpose="chat", reason="still allowed to run"
    ).enforced is False


def test_deferred_guard_bills_a_guest_against_the_network_key_too():
    """Same two subjects `preflight` produces: the pool ceiling needs both."""
    guest = GuestSession(id="g-1", key_hash="q" * 64)
    guard = deferred_guard(
        guest=guest, purpose="chat", reason="import", network_key="cafe"
    )
    assert guard.subjects == (("guest", "g-1"), ("network", "cafe"))


def test_deferred_guard_demands_a_reason():
    with pytest.raises(ValueError):
        deferred_guard(purpose="chat", reason="   ")


def test_reconciliation_still_sees_background_spend(client, db_session):
    """A 'system' counter is folded onto its person for the gap report.

    The counters and the LlmCall rows are compared per subject, and the rows a
    background call writes carry the person's id like any other. Comparing the
    kinds separately would invent a gap on both at once, and a gap that is
    always there is an alarm nobody can read.
    """
    user = _user(db_session, sub="sub-sys-reconcile")
    _system_meter(user).record(_event(purpose="profile"))
    db_session.add(
        LlmCall(
            user_id=user.id, purpose="profile", model="gpt-5.6-terra", round_index=0,
            input_tokens=10_000, cached_tokens=0, output_tokens=0,
            cost_uusd=20_000,
        )
    )
    db_session.commit()
    assert usage.reconcile(db_session, window_start=usage.window_start()) == []


def test_a_failed_increment_does_not_raise(client, db_session, monkeypatch):
    """The tokens are already spent, so failing the response would lose the
    person's work *and* the money. Unbilled, logged, and caught by the
    LlmCall reconciliation instead."""
    user = _user(db_session)
    meter = Meter(_own_spend_guard(user=user))
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
    Meter(_own_spend_guard(user=user)).record(_event())
    gaps = usage.reconcile(db_session, window_start=usage.window_start())
    # The counter moved but no LlmCall row was written, so they disagree.
    assert gaps == [("user", user.id, 20_000, 0)]
