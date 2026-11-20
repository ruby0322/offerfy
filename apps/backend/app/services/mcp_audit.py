"""Audit and hourly quota for the MCP surface, on their own transaction.

Every tool call passes through `audited`:

    with audited(key_id, user_id, name) as call:
        call.resume_id = args.get("resume_id")
        ...

**Ids, not the ORM object.** The caller hands over `key.id` and `key.user_id`,
read while the request's transaction was still open, because by the time this
runs that transaction is deliberately closed (app/routers/mcp.py explains why).
Touching an expired `AgentApiKey` here would re-acquire a pool connection and
hold it across all three audit sessions, which is exactly the overlap the split
exists to prevent.

**Why the write does not ride the request's session.** `get_db` rolls back on
any exception. A row written on the request session would therefore vanish with
exactly the refusals and errors the user most wants to see in their audit list,
and a key that produced only errors would never accumulate rows and so would
never reach its quota. `record` and the `last_used_at` stamp open a session from
`get_session_factory()`, write, commit and close, the same way
app/services/metering.py does for the same reason.

`check_quota` counts first and refuses before anything runs, following
app/routers/talent.py::_check_budget, so a refused call does no work and writes
nothing but its own audit row. rate_limit.RateEvent is deliberately not used:
it requires a guest session and no-ops outside production.

No tool arguments are recorded: they carry resume content. Only the tool name,
which resume it was about, and the outcome: ok, refused (an HTTPException
escaped), error (anything else) or throttled (the quota refusal itself).

**Why throttled is its own outcome.** The quota counts rows, and a refused
call is recorded. If the quota's own refusal were an ordinary `refused` row it
would feed the count that caused it, so a client looping past 300 would keep
its window full forever: a permanent lockout of the user by their own script.
check_quota therefore counts every row except `throttled`. Not every `refused`
row: a client hammering invalid resume ids must still be throttled.

**And at most one throttled row per key per window.** Excluding them from the
count is not enough: they are still rows in the range the count walks, so a
looping client wrote one per request forever, made every later quota check
slower, and pushed everything the user wants to see out of the 50-row audit
view. One row discloses "your assistant hit the limit"; the refusal message
carries the reopen time, so nothing is lost by not writing the rest.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import HTTPException
from sqlalchemy import func

from app.db import get_session_factory
from app.models import AgentApiKey, AgentToolCall

QUOTA_PER_HOUR = 300

THROTTLED = "throttled"

# AgentToolCall.resume_id is String(36). The caller assigns it from raw tool
# arguments, so an oversized value must not turn the audit write into a failure
# (record deliberately swallows nothing). This coercion is only a backstop: the
# caller (the MCP dispatch) is expected to validate resume_id where it reads the
# arguments, and to reject a malformed one before it gets here.
_RESUME_ID_MAX = 36
_TOOL_MAX = 64  # AgentToolCall.tool is String(64)


def _aware(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; Postgres hands back aware ones.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _new_row(
    key_id: str, user_id: str, tool: str, resume_id: str | None, outcome: str
) -> AgentToolCall:
    if not isinstance(resume_id, str) or len(resume_id) > _RESUME_ID_MAX:
        resume_id = None
    # `tool` is a client-supplied string for an unknown name, and the column is
    # String(64): on Postgres an over-long value is a failed audit write, the
    # same failure the resume_id coercion above prevents.
    return AgentToolCall(
        key_id=key_id,
        user_id=user_id,
        tool=tool[:_TOOL_MAX],
        resume_id=resume_id,
        outcome=outcome,
    )


def record(
    key_id: str, user_id: str, tool: str, resume_id: str | None, outcome: str
) -> None:
    """Write one audit row on its own session. Raises if the write fails: a
    silently lost audit row is the failure this module exists to prevent."""
    db = get_session_factory()()
    try:
        db.add(_new_row(key_id, user_id, tool, resume_id, outcome))
        db.commit()
    finally:
        db.close()


def record_throttled(
    key_id: str, user_id: str, tool: str, *, now: datetime | None = None
) -> None:
    """One `throttled` row per key per window, and no more.

    Two concurrent refusals can both find nothing and both write, so this is a
    bound of "a couple", not exactly one; what it rules out is the unbounded
    row-per-request a looping client used to produce.
    """
    now = now or datetime.now(timezone.utc)
    db = get_session_factory()()
    try:
        already = (
            db.query(AgentToolCall.id)
            .filter(
                AgentToolCall.key_id == key_id,
                AgentToolCall.outcome == THROTTLED,
                AgentToolCall.created_at >= now - timedelta(hours=1),
            )
            .first()
        )
        if already is not None:
            return
        db.add(_new_row(key_id, user_id, tool, None, THROTTLED))
        db.commit()
    finally:
        db.close()


def check_quota(key_id: str, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    db = get_session_factory()()
    try:
        used, oldest = (
            db.query(func.count(AgentToolCall.id), func.min(AgentToolCall.created_at))
            .filter(
                AgentToolCall.key_id == key_id,
                AgentToolCall.outcome != THROTTLED,
                AgentToolCall.created_at >= now - timedelta(hours=1),
            )
            .one()
        )
    finally:
        db.close()
    if (used or 0) < QUOTA_PER_HOUR:
        return
    # The window slides, so the first slot frees an hour after the oldest call
    # still being counted. Say so: an agent that is told only "too many calls"
    # has no way to pick between retrying now and giving up, and retrying now
    # is what writes the rows record_throttled refuses to write.
    reopens = (_aware(oldest) if oldest is not None else now) + timedelta(hours=1)
    raise HTTPException(
        status_code=429,
        detail=(
            f"Too many calls: the limit is {QUOTA_PER_HOUR} an hour for one key. "
            f"The quota reopens at {reopens.isoformat()}."
        ),
    )


def _stamp_last_used(key_id: str) -> None:
    db = get_session_factory()()
    try:
        row = db.get(AgentApiKey, key_id)
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@dataclass
class Call:
    """Mutable handle `audited` yields; the caller assigns resume_id once the
    arguments have been read."""

    resume_id: str | None = None


@contextmanager
def audited(key_id: str, user_id: str, tool: str) -> Iterator[Call]:
    # The ids are read by the caller while its transaction is still open; see
    # the module docstring. A refused call is itself recorded, as throttled so
    # it does not feed its own count, and at most once per window.
    call = Call()
    try:
        check_quota(key_id)
    except HTTPException:
        record_throttled(key_id, user_id, tool)
        raise
    _stamp_last_used(key_id)
    try:
        yield call
    except HTTPException:
        record(key_id, user_id, tool, call.resume_id, "refused")
        raise
    except Exception:
        record(key_id, user_id, tool, call.resume_id, "error")
        raise
    else:
        record(key_id, user_id, tool, call.resume_id, "ok")
