import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RateEvent

# Exports only. Chat moved to app.services.usage, which meters cost over a
# weekly window and keeps its own hourly burst guard -- counted off the LlmCall
# rows a turn writes anyway, so no RateEvent row is needed for it. An export is
# a Typst compile: CPU, not tokens, so it does not belong in a cost meter and
# stays here as a plain abuse guard.
#
# 'chat' is still permitted by RateEvent's check constraint on purpose: rows
# written before the move exist and must stay readable.
LIMITS = {"export": 20}
WINDOW = timedelta(hours=1)


def guest_rate_limits_enabled() -> bool:
    return get_settings().app_env == "production"


def try_consume_guest_rate(db: Session, guest_session_id: str, kind: str) -> bool:
    if not guest_rate_limits_enabled():
        return True
    limit = LIMITS[kind]
    since = datetime.now(timezone.utc) - WINDOW
    count = (
        db.query(RateEvent)
        .filter(
            RateEvent.guest_session_id == guest_session_id,
            RateEvent.kind == kind,
            RateEvent.created_at >= since,
        )
        .count()
    )
    if count >= limit:
        return False
    db.add(RateEvent(guest_session_id=guest_session_id, kind=kind))
    db.flush()
    return True


def enforce_guest_rate(db: Session, guest_session_id: str, kind: str) -> None:
    if not try_consume_guest_rate(db, guest_session_id, kind):
        raise HTTPException(status_code=429, detail="Too many requests")


# The agent surface's signed export link is public, carries no credential, and
# every hit is a Typst compile -- so a leaked link is an amplifier for its whole
# ten minute life, and the number of compiles one link can buy is the only lever
# there is. That is what this bounds.
#
# Three deliberate differences from the guest limits above:
#
# * Keyed on the resume id, not the token. `export_resume` mints a fresh token on
#   every call, so a token-keyed bucket would reopen with each mint and bound
#   nothing.
# * In memory, not in `rate_events`. What this protects is one box's CPU and its
#   in-flight slots (app/concurrency.py), and a database round trip on the path
#   whose problem is load is the wrong direction. `RateEvent.guest_session_id` is
#   also a NOT NULL foreign key to `guest_sessions`, which a resume id is not.
# * On in every environment. A staging box compiles PDFs on the same CPU as
#   production, and a guard that only runs in production is a guard no test runs.
#
# The arithmetic: 10 a minute per worker, and ops/systemd/offerfy-api.service runs
# 2 workers, so one link buys at most 20 compiles a minute -- around 3 seconds of
# CPU, against the ~100 compiles a second this box could otherwise be made to do.
# Honest use of a link is a single download, so the ceiling is some 20x generous.
# **If the worker count there changes, this number is part of the arithmetic that
# changes with it.**
EXPORT_DOWNLOAD_LIMIT = 10
EXPORT_DOWNLOAD_WINDOW = 60.0

#: Sweep the whole table only once it has grown past this. The key in hand is
#: pruned on every call; without the sweep a flood spread over many links would
#: leave one list per resume behind for the life of the process.
_EXPORT_SWEEP_AT = 4096

_export_lock = threading.Lock()
#: resume id -> the monotonic stamps of its hits still inside the window.
_export_hits: dict[str, list[float]] = {}


def try_consume_export_download(resume_id: str, *, now: float | None = None) -> bool:
    """False once this resume's link has been fetched EXPORT_DOWNLOAD_LIMIT times
    inside the window.

    A refusal spends nothing: the stamps are left as they were, so the budget
    reopens EXPORT_DOWNLOAD_WINDOW after the hit that filled it rather than after
    the last attempt. A client hammering a spent link cannot hold it shut.
    """
    moment = time.monotonic() if now is None else now
    cutoff = moment - EXPORT_DOWNLOAD_WINDOW
    with _export_lock:
        if len(_export_hits) > _EXPORT_SWEEP_AT:
            for key in [
                key
                for key, stamps in _export_hits.items()
                if not any(stamp > cutoff for stamp in stamps)
            ]:
                del _export_hits[key]
        hits = [stamp for stamp in _export_hits.get(resume_id, ()) if stamp > cutoff]
        if len(hits) >= EXPORT_DOWNLOAD_LIMIT:
            _export_hits[resume_id] = hits
            return False
        hits.append(moment)
        _export_hits[resume_id] = hits
        return True


def enforce_export_download_rate(resume_id: str) -> None:
    """429 once the link's budget for this window is spent.

    `Retry-After` is the window rather than the exact reopening: the stamp that
    reopens it is this process's, two workers keep separate tables, and a number
    that pretended to more precision than that would be a lie.
    """
    if not try_consume_export_download(resume_id):
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(int(EXPORT_DOWNLOAD_WINDOW))},
        )


def reset_export_downloads() -> None:
    """Forget every link's budget. For tests, which share one process."""
    with _export_lock:
        _export_hits.clear()
