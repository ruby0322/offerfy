import os

# Tests must never inherit the repo .env Postgres URL. setdefault is not
# enough: a exported DATABASE_URL makes drop_all wipe the live database.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_ENV"] = "development"
os.environ.setdefault("AUTH_TOKEN_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("OPENAI_MODEL", "gpt-5.6-terra")
os.environ["OPENAI_API_KEY"] = ""
os.environ["OFFERFY_SKIP_TEMPLATE_PREFETCH"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base, get_engine, get_session_factory, reset_engine
from app.main import app


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def openai_enabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()



@pytest.fixture
def client():
    reset_engine()
    engine = get_engine()
    Base.metadata.create_all(engine)
    factory = get_session_factory()

    def _db():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Refusing to drop_all on a non-sqlite test database")
    Base.metadata.drop_all(engine)
    reset_engine()


@pytest.fixture
def db_session(client) -> Session:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
