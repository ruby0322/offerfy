import os

# Tests must never inherit the repo .env Postgres URL. setdefault is not
# enough: a exported DATABASE_URL makes drop_all wipe the live database.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_ENV"] = "development"
os.environ.setdefault("AUTH_TOKEN_SECRET", "test-secret-for-unit-tests")
# The model pin blanked, so tests run on model_catalog.DEFAULT_MODEL rather
# than on whatever the repo .env pins. setdefault is not enough: the .env is
# read by app.config, and a pin naming a model the guest tier may not have
# would quietly change which model half these tests exercise.
os.environ["CHAT_MODEL"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OFFERFY_SKIP_TEMPLATE_PREFETCH"] = "1"

# Same reasoning as DATABASE_URL above, for object storage. Without these the
# repo .env wins and every test run PUTs fixtures into the live offerfy-private
# bucket (473 objects accumulated this way before 2026-10-22). Blanking the
# endpoint makes s3_configured() false, so put_object/delete_object no-op.
os.environ["S3_ENDPOINT"] = ""
os.environ["S3_ACCESS_KEY"] = ""
os.environ["S3_SECRET_KEY"] = ""
os.environ["S3_BUCKET"] = "test-bucket-never-real"

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
def anthropic_enabled(monkeypatch):
    """A credential for the second provider, and nothing else.

    Separate from `openai_enabled` on purpose: the two keys are independent,
    and services.model_choice.offered() filters the picker by which one is
    present. A fixture that set both would make it impossible to test that.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
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
    # Self-referencing resume cascades make SQLite resolve resume_revisions after it is dropped.
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        Base.metadata.drop_all(conn)
        conn.commit()
    reset_engine()


@pytest.fixture
def db_session(client) -> Session:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
