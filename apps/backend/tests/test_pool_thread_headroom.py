"""In-flight requests must stay under the DB pool, or the worker collapses.

`app/concurrency.py` has the full account and the measurements. The short
version: every router is a sync `def`, and a request with a `Depends(get_db)`
session holds its pool connection from its first query until the dependency's
`finally` gets a threadpool thread again -- a window in which it holds a
connection but no thread. So checked-out connections are bounded by requests
*in flight*, not by threads, and once in-flight passes the pool's 40 slots the
worker jams: handlers block in checkout holding threads, the cleanups that
would free a connection wait behind them, and `pool_timeout` turns it into a
flood of 500s.

`test_ungated_load_exhausts_the_pool` is the control: it reproduces that on
purpose, so the gated tests cannot pass for the wrong reason.
"""

import time

import anyio
import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app import db
from app.concurrency import (
    DEFAULT_MAX_IN_FLIGHT,
    InFlightGate,
    check_headroom,
    headroom_ok,
    is_ungated,
)
from app.main import app as real_app

# A miniature of production's ratio: far more requests in flight than the pool
# has slots, which is the only condition under which this fails.
POOL = 6
IN_FLIGHT = 80
# Long enough that a queued request waits past POOL_TIMEOUT: with 80 requests
# sharing 6 slots the tail waits ~(80-6)/6 * HOLD, which must exceed it. Too
# short a hold and the pool cycles fast enough that nothing ever times out,
# which is how an earlier version of this file "passed" while proving nothing.
HOLD_SECONDS = 0.15
POOL_TIMEOUT = 0.5


def _build_app(
    tmp_path, *, gate_limit: int | None, gate_timeout: float = 30.0
) -> FastAPI:
    """A faithful miniature: QueuePool + sync generator dep + sync handler."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gate.db'}",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=POOL,
        max_overflow=0,
        # Short, like production: a starved request should fail fast rather
        # than park a thread for 30s.
        pool_timeout=POOL_TIMEOUT,
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_db():
        # The shape of app.db.get_db: a sync generator, so FastAPI runs its
        # open and its close as two separate threadpool tasks, and the
        # connection is held across the gap between them.
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    built = FastAPI()

    @built.get("/healthz")
    def healthz():  # ungated, and touches no database
        return {"ok": True}

    @built.get("/row")
    def row(session: Session = Depends(get_db)):  # sync def == threadpool
        session.execute(text("select 1")).scalar()  # checks a connection out
        time.sleep(HOLD_SECONDS)  # stands in for a board aggregate
        return {"ok": True}

    if gate_limit is not None:
        # Both via add_middleware: the middleware is not constructed until the
        # stack is built on the first request, so setting attributes on
        # InFlightGate.current beforehand would configure a previous test's
        # instance.
        built.add_middleware(InFlightGate, limit=gate_limit, timeout=gate_timeout)
    return built


def _hammer(built: FastAPI, *, path: str = "/row", total: int = IN_FLIGHT) -> list[int]:
    """Fire `total` genuinely concurrent requests and collect the statuses."""
    codes: list[int] = []

    async def main() -> None:
        transport = httpx.ASGITransport(app=built, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:

            async def one() -> None:
                response = await client.get(path)
                codes.append(response.status_code)

            async with anyio.create_task_group() as tg:
                for _ in range(total):
                    tg.start_soon(one)

    anyio.run(main)
    return codes


@pytest.fixture
def small_pool(monkeypatch):
    """Validate gate limits against the miniature's pool, not production's."""
    monkeypatch.setattr(db, "POOL_SIZE", POOL)
    monkeypatch.setattr(db, "POOL_MAX_OVERFLOW", 0)
    return POOL


def test_ungated_load_exhausts_the_pool(tmp_path):
    """The control: this is the 2026-11-20 failure, reproduced."""
    codes = _hammer(_build_app(tmp_path, gate_limit=None))
    failed = [c for c in codes if c != 200]
    assert failed, (
        "expected pool-checkout failures with more requests in flight than the "
        "pool has slots, but every request succeeded -- if this stops "
        "reproducing, the gated test below is no longer proving anything"
    )
    assert all(c == 500 for c in failed), (
        f"expected the ungated failure to be a 500 from QueuePool; saw {sorted(set(failed))}"
    )


def test_gate_serves_every_request_under_the_same_load(tmp_path, small_pool):
    """With in-flight capped below the pool, the same load cannot fail."""
    built = _build_app(tmp_path, gate_limit=POOL - 2)
    codes = _hammer(built)
    assert codes.count(200) == IN_FLIGHT, (
        f"{IN_FLIGHT - codes.count(200)} of {IN_FLIGHT} requests failed behind "
        f"the gate; statuses seen: {sorted(set(codes))}"
    )


def test_gate_sheds_with_503_rather_than_500(tmp_path, small_pool):
    """Past the wait timeout a request is shed honestly, not as a 500."""
    # One slot and a timeout far shorter than the queue needs to drain.
    built = _build_app(tmp_path, gate_limit=1, gate_timeout=0.05)
    codes = _hammer(built, total=40)
    assert 503 in codes, f"expected some requests to be shed; saw {sorted(set(codes))}"
    assert 500 not in codes, (
        "a shed request must not surface as a 500 -- that is the failure mode "
        f"this replaces; saw {sorted(set(codes))}"
    )
    gate = InFlightGate.current
    assert gate is not None and gate.shed > 0


def test_health_checks_bypass_the_gate(tmp_path, small_pool):
    """A saturated worker must still be able to say it is alive."""
    built = _build_app(tmp_path, gate_limit=1, gate_timeout=0.05)
    codes = _hammer(built, path="/healthz", total=40)
    assert codes.count(200) == 40, (
        f"/healthz must never be gated; statuses seen: {sorted(set(codes))}"
    )


def test_configured_ceiling_has_pool_headroom():
    """The real numbers, both read from source rather than restated here."""
    capacity = db.pool_capacity()
    assert DEFAULT_MAX_IN_FLIGHT < capacity, (
        f"MAX_IN_FLIGHT={DEFAULT_MAX_IN_FLIGHT} must be below the pool's "
        f"{capacity} slots ({db.POOL_SIZE}+{db.POOL_MAX_OVERFLOW}). Lower it or "
        "raise the pool -- and mind the Postgres connection budget in "
        "AGENTS.md §10 before raising the pool."
    )
    assert headroom_ok(DEFAULT_MAX_IN_FLIGHT) is True


def test_headroom_check_rejects_a_ceiling_at_or_above_the_pool():
    assert headroom_ok(40, capacity=40) is False
    assert headroom_ok(41, capacity=40) is False
    assert headroom_ok(39, capacity=40) is True
    with pytest.raises(ValueError, match="must be below the pool"):
        check_headroom(db.pool_capacity())


def test_real_app_installs_the_gate():
    """A ceiling nothing enforces is just a comment."""
    assert any(
        middleware.cls is InFlightGate for middleware in real_app.user_middleware
    ), "app.main must install InFlightGate, or nothing bounds in-flight requests"


def _all_api_routes():
    """Every APIRoute, walking the _IncludedRouter wrappers this FastAPI uses.

    `app.routes` is not flat here -- it holds `_IncludedRouter` objects whose
    real routes hang off `original_router`. A walk that only iterated
    `app.routes` would silently find nothing and make this test vacuous, which
    is exactly what an earlier version of it did.
    """
    from fastapi.routing import APIRoute

    found: dict[tuple, APIRoute] = {}

    def walk(obj, depth=0):
        if depth > 4:
            return
        for route in getattr(obj, "routes", None) or []:
            if isinstance(route, APIRoute):
                found[(tuple(sorted(route.methods or [])), route.path)] = route
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(original, depth + 1)
            walk(route, depth + 1)

    walk(real_app)
    assert found, "route walk found nothing -- this test would prove nothing"
    return found


def test_streaming_routes_bypass_the_gate():
    """A stream holds its slot until it ends, so it must never take one.

    Walks the real routes rather than trusting a hardcoded list, so a new SSE
    endpoint fails here instead of quietly starving the gate in production.
    """
    import inspect

    streaming = []
    for (_methods, path), route in _all_api_routes().items():
        try:
            source = inspect.getsource(route.endpoint)
        except Exception:
            source = ""
        if "text/event-stream" in source or "StreamingResponse" in source:
            streaming.append(path)

    assert streaming, "found no streaming routes -- the detection above is broken"

    # Path templates carry {param}; the gate sees a concrete path.
    concrete = [p.replace("{resume_id}", "abc-123") for p in streaming]
    not_excluded = [p for p in concrete if not is_ungated(p)]
    assert not not_excluded, (
        f"these streaming routes would take a gate slot for the whole stream: "
        f"{not_excluded} -- add them to UNGATED_PATTERN in app/concurrency.py"
    )


def test_ordinary_routes_are_still_gated():
    """The control for the exclusions: normal traffic must go through them."""
    assert is_ungated("/v1/jobs") is False
    assert is_ungated("/v1/jobs/facets") is False
    assert is_ungated("/v1/resumes/abc-123") is False
    assert is_ungated("/v1/resumes/abc-123/messages") is False
    # and the exclusions themselves
    assert is_ungated("/healthz") is True
    assert is_ungated("/readyz") is True
    assert is_ungated("/healthz/sse") is True
    assert is_ungated("/v1/resumes/abc-123/chat") is True
    assert is_ungated("/v1/resumes/abc-123/import/stream") is True
    assert is_ungated("/v1/resumes/abc-123/tailor-run/stream") is True


def test_readyz_reports_gate_saturation():
    """A saturated worker must be able to say so in one curl.

    The 2026-11-20 incident was invisible from /readyz: it answered a bare
    "ok" while a third of traffic 500'd.
    """
    from fastapi.testclient import TestClient

    with TestClient(real_app) as client:
        body = client.get("/readyz").json()

    assert body["status"] == "ok"
    gate = body["gate"]
    assert gate["limit"] == DEFAULT_MAX_IN_FLIGHT
    for key in ("in_flight", "waiting", "shed"):
        assert key in gate, f"/readyz gate block is missing {key!r}"
        assert isinstance(gate[key], int)
