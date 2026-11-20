"""A ceiling on in-flight requests, so the DB pool can never be the thing that fails.

Measured on this host 2026-11-20 against the live catalogue, one worker, pool
30+10:

    in-flight   thread limit 40        thread limit 16
    60          100% ok, 40 req/s      98.8% ok, 43 req/s
    150         68.3% ok, 19 req/s     21.2% ok,  8 req/s
    300         24.3% ok, 15 req/s      0% ok,   10 req/s

Two things in that table matter. Failures track **in-flight requests**, not
threads; and *lowering* the thread limit made every number worse. So the
threadpool was never the right lever -- see "Why not the thread limit" below.

## What actually breaks

Every router here is a sync `def`, so FastAPI runs it via `run_in_threadpool`,
and a request with a `Depends(get_db)` session acquires a thread **twice**:

1. the `get_db` generator runs to its `yield` in a thread (creates the
   `Session`; no connection yet, SQLAlchemy connects lazily);
2. the handler runs in a thread -- the first query checks a connection **out**,
   the handler returns, the thread is released, and **the connection stays
   checked out** because the session is still open;
3. the generator's `finally` (commit + close) runs in a thread *again*, and only
   then does the connection go back to the pool.

Between (2) and (3) a request holds a pool connection while holding no thread.
Instrumented at 2ms on 2026-11-20 with the thread limit at 16: one connection
per request (`checkouts_per_request = 1.0`) and yet
`max_checkedout = 40` -- the whole pool -- so 24 connections were held by
requests that held no thread at all. An HTTP probe sampling twice a second saw
`overlap = 0` and missed it entirely; the peak only shows up at millisecond
resolution.

So "connections in use" is bounded by requests *in flight*, not by threads. Once
in-flight exceeds the pool, handlers block in checkout holding threads, the
step-3 cleanups that would free a connection queue behind them for a thread that
will not come, and `pool_timeout=3` turns the jam into a flood of
`QueuePool limit of size 30 overflow 10 reached` 500s -- ~10k an hour, a third
to a half of all job board traffic on 2026-11-20. Postgres was idle throughout
(76 of 250 connections), and `pg_stat_activity` showed exactly 40 sessions
`idle in transaction`: one worker's entire pool, held by requests waiting for a
thread to close them.

## Why not the thread limit

Lowering it makes it worse, which the table above shows and which follows from
(3): cleanup needs a thread too. Starve threads and connections are held
*longer*, so the pool empties just as fast with less work getting done. The
thread limit is left at anyio's default; if anything it wants to be **above**
`MAX_IN_FLIGHT`, so a cleanup is never stuck behind a full set of handlers.

## The fix

Cap in-flight requests below the pool, and make the excess *wait* instead of
fail. A queued request is slow; a request that cannot get a connection is a 500.
Because a request holds its gate slot for its whole life -- including the step-3
cleanup, which FastAPI unwinds inside the route call -- checked-out connections
can never exceed `MAX_IN_FLIGHT`, so the pool has headroom by construction:

    MAX_IN_FLIGHT < db.pool_capacity()

`tests/test_pool_thread_headroom.py` asserts that against both numbers read from
source, and load-tests the gate end to end.

Requests that wait longer than `GATE_TIMEOUT` get a 503 with `Retry-After`
rather than queueing forever: past that point the client has usually given up,
and holding the request only deepens the queue. 503 is also honest about what
happened, where the old failure surfaced as a 500 with a SQLAlchemy traceback.

Health checks skip the gate: `/healthz` and `/readyz` exist to say whether this
worker is alive, and they are the first thing you want answered when it is
saturated. `/healthz` touches no database. `/readyz` does, but one connection
for a liveness probe is worth the pool slot -- the gate leaves margin for it.
The SSE streams skip it too, for the reason given at `UNGATED_PATTERN`.
"""

import os
import re

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

import anyio

from app import db

#: Concurrent in-flight requests per worker. Must stay under the pool (see
#: above). With the pool at 30+10 this leaves 8 slots of margin for the
#: ungated health checks and anything that opens its own session.
DEFAULT_MAX_IN_FLIGHT = 32

#: How long a request may wait for a slot before it is shed with a 503.
GATE_TIMEOUT = 10.0

#: Override for measuring a different ceiling against real traffic without
#: editing this file in the shared checkout (which `AGENTS.md` §3 warns is read
#: live by the running units). Being an environment variable it needs a full
#: restart, not a HUP -- see `AGENTS.md` §4. A value with no pool headroom is
#: refused at startup rather than quietly accepted.
MAX_IN_FLIGHT_ENV = "OFFERFY_MAX_IN_FLIGHT"

#: Routes that must answer even when every slot is taken.
UNGATED_PATHS = frozenset({"/healthz", "/readyz"})

#: Streams, which must never take a gate slot.
#:
#: A slot is held for the whole life of a request, and an SSE response does not
#: finish until the stream does -- so a handful of open streams would hold slots
#: for minutes and starve every other route. That is the same shape as the bug
#: `tests/test_sse_sessions.py` pins for *pool connections*, one layer up, and
#: it is why these bypass the gate rather than getting a bigger ceiling.
#:
#: Letting them through is safe for the pool: the streams were changed on
#: 2026-11-15 to open a short-lived session per poll instead of holding a
#: request-scoped one, so an open stream holds no connection between polls.
#: `test_streaming_routes_bypass_the_gate` walks the real routes and fails if a
#: new stream appears that this does not cover.
UNGATED_PATTERN = re.compile(
    r"^(?:/healthz/sse|/v1/resumes/[^/]+/(?:chat|import/stream|tailor-run/stream))$"
)


def is_ungated(path: str) -> bool:
    return path in UNGATED_PATHS or UNGATED_PATTERN.match(path) is not None


def configured_max_in_flight() -> int:
    raw = os.environ.get(MAX_IN_FLIGHT_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_IN_FLIGHT
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{MAX_IN_FLIGHT_ENV}={raw!r} is not an integer") from exc
    if value < 1:
        raise ValueError(f"{MAX_IN_FLIGHT_ENV}={value} must be at least 1")
    return value


def headroom_ok(max_in_flight: int, capacity: int | None = None) -> bool:
    """Can the pool serve a connection to every request the gate admits?"""
    if capacity is None:
        capacity = db.pool_capacity()
    return max_in_flight < capacity


def check_headroom(max_in_flight: int) -> None:
    """Refuse a ceiling the pool cannot cover. Called at startup, loudly."""
    if not headroom_ok(max_in_flight):
        raise ValueError(
            f"MAX_IN_FLIGHT={max_in_flight} must be below the pool's "
            f"{db.pool_capacity()} slots ({db.POOL_SIZE}+{db.POOL_MAX_OVERFLOW}); "
            "see app/concurrency.py for the 2026-11-20 incident this prevents"
        )


class InFlightGate:
    """Admit at most `limit` requests at once; shed the rest with a 503.

    A plain ASGI middleware rather than `@app.middleware("http")`: that
    decorator wraps every request in an extra `BaseHTTPMiddleware` task group,
    and the point here is to add a bound, not more machinery per request.
    """

    #: The installed gate, for diagnostics. One per worker process.
    current: "InFlightGate | None" = None

    def __init__(self, app: ASGIApp, limit: int | None = None, timeout: float = GATE_TIMEOUT):
        if limit is None:
            limit = configured_max_in_flight()
        check_headroom(limit)
        self.app = app
        self.limit = limit
        self.timeout = timeout
        self._semaphore = anyio.Semaphore(limit)
        # Diagnostics, so a saturated worker can say so rather than being
        # inferred from latency.
        self.waiting = 0
        self.shed = 0
        InFlightGate.current = self

    @property
    def in_flight(self) -> int:
        return self.limit - self._semaphore.value

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or is_ungated(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        self.waiting += 1
        try:
            with anyio.fail_after(self.timeout):
                await self._semaphore.acquire()
        except TimeoutError:
            self.shed += 1
            response: Response = JSONResponse(
                {
                    "detail": "Server is at capacity, please retry.",
                    "in_flight": self.limit,
                },
                status_code=503,
                headers={"Retry-After": "2"},
            )
            await response(scope, receive, send)
            return
        finally:
            self.waiting -= 1

        try:
            await self.app(scope, receive, send)
        finally:
            self._semaphore.release()


def gate_state() -> dict[str, int | None]:
    """What the gate is doing right now, for /readyz."""
    gate = InFlightGate.current
    if gate is None:
        return {"limit": None, "in_flight": None, "waiting": None, "shed": None}
    return {
        "limit": gate.limit,
        "in_flight": gate.in_flight,
        "waiting": gate.waiting,
        "shed": gate.shed,
    }
