from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

#: Per-worker Postgres pool. Read by `ops/verify-capacity.sh` for the connection
#: budget, and by `app.concurrency` for the thread-headroom invariant -- so these
#: are constants rather than literals buried in the `create_engine` call.
POOL_SIZE = 30
POOL_MAX_OVERFLOW = 10


def pool_capacity() -> int:
    """Most connections one worker can ever hold open at once."""
    return POOL_SIZE + POOL_MAX_OVERFLOW


class Base(DeclarativeBase):
    pass


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if ":memory:" in url:
            engine = create_engine(
                url,
                connect_args=connect_args,
                poolclass=StaticPool,
            )
        else:
            engine = create_engine(url, connect_args=connect_args)

        @event.listens_for(engine, "connect")
        def _sqlite_on_connect(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    # A single board page view fans out ~10 backend calls *in parallel*
    # (components/jobs/board-route.tsx), so a view checks out several
    # connections at once rather than one. On SQLAlchemy's defaults
    # (pool_size=5, max_overflow=10) three concurrent views exhausted a
    # worker's 15 slots; every later request then queued for the full
    # pool_timeout, and because the routers are sync `def` each queued request
    # also held an anyio threadpool thread. The threadpool filled with blocked
    # handlers and the API stopped answering *everything*, /healthz included,
    # while systemd and the :8001 listener both still looked healthy.
    #
    # **Raised 2026-11-16 from 15+10 to 30+10.**
    #
    # FastAPI runs every sync `def` handler via `run_in_threadpool`, and anyio's
    # default limiter is **40 threads per process** (measured, not assumed:
    # `anyio.to_thread.current_default_thread_limiter().total_tokens`). At 25
    # slots per worker the pool ran dry while threads were still free, so
    # connections -- not CPU, not threads -- were what capped concurrency.
    #
    # **That raise matched the pool to the thread limiter, 40 and 40, on the
    # reasoning that "neither resource is silently the next wall". Matching them
    # is what broke the API on 2026-11-20 -- see `app/concurrency.py`. The pool
    # must stay strictly larger than the thread limit, because a sync request
    # holds a connection across two *separate* threadpool acquisitions and so
    # spends part of its life holding a connection while holding no thread.**
    # The headroom invariant is asserted by `tests/test_pool_thread_headroom.py`;
    # do not re-match these two numbers.
    #
    # The budget, against `max_connections=250` (docker-compose.yml):
    #
    #     API        2 workers x 40          =  80
    #     ingest     3 timers  x 40 worst    = 120   (a batch pass really uses a
    #                                                 handful; this is the ceiling)
    #     reserved   superuser_reserved_connections =   3
    #                                           ---
    #                                           203  of 250
    #
    # `pool_size` is a ceiling, not a reservation -- SQLAlchemy opens
    # connections lazily -- so steady state sits far below this. Plan against
    # the ceiling anyway: the ceiling is what gets reached under exactly the
    # load that matters.
    #
    # Deployable with `kill -HUP` and no sudo, which is why the concurrency went
    # here rather than into the unit's `--workers`.
    #
    # pool_timeout is deliberately short: a saturated pool should fail fast and
    # visibly rather than park a threadpool thread for 30s and take the whole
    # API down quietly with it.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=POOL_MAX_OVERFLOW,
        pool_timeout=3,
    )


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
        _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def ensure_schema() -> None:
    """Create missing tables. Additive only — never drops."""
    Base.metadata.create_all(get_engine())


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
