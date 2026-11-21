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
from app.services import api_keys, rate_limit


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    api_keys.reset()
    rate_limit.reset_export_downloads()
    yield
    get_settings.cache_clear()
    # The key-cooldown table is module state in one process, so a test that
    # throttles a key would otherwise make the next test's first attempt start
    # on the second key -- or, with one key configured, look cooled down.
    api_keys.reset()
    # Same reason: the export link's budget is module state too, and a test that
    # spends one resume's would otherwise be spending it for whatever runs next.
    rate_limit.reset_export_downloads()


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


# ---------------------------------------------------------------------------
# Shared fixtures for the self-serve MCP surface (tests/test_agent_*.py and
# the cookie-authenticated key routes). Built by hand, as test_talent_api.py
# and test_jobs_api.py do, so a test about auth does not depend on a model call.
# ---------------------------------------------------------------------------


def _user(db, sub: str):
    from app.models import User

    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en", name=sub)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _resume_for(db, user, title="Untitled"):
    """A committed master resume the real compiler accepts."""
    from app.models import Resume
    from app.services.starter import generate_starter

    resume = Resume(
        title=title,
        typst_source=generate_starter("zh-TW"),
        source="create",
        locale="zh-TW",
        user_id=user.id,
        kind="master",
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return resume


def _job_with_facets(db, source_id: str, title: str):
    from app.models import Job, JobFacets, _utcnow

    # Relative to now: a catalogue posting is live by default, inside the
    # freshness window search_jobs enforces. A fixed date would age out.
    now = _utcnow()
    job = Job(
        source="greenhouse",
        source_id=source_id,
        company="Stripe",
        title=title,
        location="Remote",
        remote=True,
        apply_url="https://example.com/apply",
        source_url="https://example.com/job",
        description_text="Build things",
        description_html="<p>Build things</p>",
        posted_at=now,
        first_seen_at=now,
        last_seen_at=now,
        is_active=True,
    )
    db.add(job)
    db.flush()
    db.add(
        JobFacets(
            job_id=job.id,
            seniority="senior",
            function="engineering",
            description_hash="h" * 64,
        )
    )
    db.commit()
    db.refresh(job)
    return job


@pytest.fixture
def a_user(db_session):
    return _user(db_session, "agent-user-a")


@pytest.fixture
def other_user(db_session):
    return _user(db_session, "agent-user-b")


@pytest.fixture
def signed_in(client, a_user):
    from app.deps import SESSION_COOKIE, _sign

    client.cookies.set(SESSION_COOKIE, _sign(a_user.id, get_settings().auth_token_secret))
    yield a_user


@pytest.fixture
def my_resume(db_session, a_user):
    return _resume_for(db_session, a_user)


@pytest.fixture
def a_job(db_session):
    return _job_with_facets(db_session, "stripe:agent-1", "Payments Engineer")


@pytest.fixture
def another_job(db_session):
    return _job_with_facets(db_session, "stripe:agent-2", "Platform Engineer")
