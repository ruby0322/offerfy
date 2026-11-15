from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


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
    # 25 per worker x 2 workers = 50, against Postgres max_connections=100.
    # pool_timeout is deliberately short: a saturated pool should fail fast and
    # visibly rather than park a threadpool thread for 30s and take the whole
    # API down quietly with it.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=15,
        max_overflow=10,
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
