import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.jobs.store import JOB_FRESHNESS_DAYS
from app.deps import SESSION_COOKIE, _sign
from app.models import (
    ChatMessage,
    GuestSession,
    Job,
    JobIngestRun,
    LlmCall,
    Resume,
    TalentApiKey,
    User,
)
from app.services.talent_keys import hash_talent_key


def _session_cookie(user: User) -> dict[str, str]:
    return {SESSION_COOKIE: _sign(user.id, get_settings().auth_token_secret)}


def _allow(monkeypatch, email: str = "ops@example.com") -> None:
    monkeypatch.setenv("ADMIN_EMAILS", email)
    get_settings.cache_clear()


def test_user_and_resume_have_created_at(client: TestClient, db_session):
    user = User(google_sub="sub-ts", email="ts@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.created_at is not None
    created_at = user.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    assert created_at <= datetime.now(timezone.utc)

    posted = client.post("/v1/resumes", json={"locale": "en", "title": "T"}).json()
    resume = db_session.get(Resume, posted["id"])
    assert resume is not None
    assert resume.created_at is not None


def test_admin_overview_unauthenticated_401(client: TestClient, monkeypatch):
    _allow(monkeypatch)
    assert client.get("/v1/admin/overview").status_code == 401


def test_admin_overview_non_admin_404(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    user = User(google_sub="sub-user", email="user@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    assert client.get("/v1/admin/overview", cookies=_session_cookie(user)).status_code == 404


def test_admin_overview_empty_allowlist_404(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "")
    get_settings.cache_clear()
    user = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    assert client.get("/v1/admin/overview", cookies=_session_cookie(user)).status_code == 404


def test_admin_overview_allowlisted_counts(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    monkeypatch.setenv("S3_ENDPOINT", "")
    monkeypatch.setenv("S3_ACCESS_KEY", "")
    monkeypatch.setenv("S3_SECRET_KEY", "")
    monkeypatch.setenv("S3_BUCKET", "")
    get_settings.cache_clear()
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    other = User(google_sub="sub-o", email="o@example.com", locale="zh-TW")
    db_session.add_all([ops, other])
    db_session.commit()
    client.post("/v1/resumes", json={"locale": "en", "title": "Guest one"})
    old = ChatMessage(
        resume_id=client.get("/v1/resumes").json()[0]["id"],
        role="user",
        content="old",
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    db_session.add(old)
    db_session.commit()
    res = client.get("/v1/admin/overview", cookies=_session_cookie(ops))
    assert res.status_code == 200
    body = res.json()
    assert body["health"]["api"] == "ok"
    assert body["health"]["database"] == "ok"
    assert body["health"]["s3_configured"] is False
    assert body["counts"]["users"] == 2
    assert body["counts"]["resumes"] >= 1
    assert body["counts"]["chat_messages_24h"] == 0
    assert body["counts"]["chat_messages_7d"] >= 1
    assert isinstance(body["recent_users"], list)
    assert isinstance(body["recent_resumes"], list)
    series = body["series"]
    assert len(series) == 14
    assert series[-1]["users"] >= 2
    assert series[-1]["resumes_create"] >= 1
    chat_days = [row["chats"] for row in series]
    assert sum(chat_days) >= 1
    assert series[-1]["chats"] == 0


def test_admin_users_search_and_detail(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    ada = User(google_sub="sub-ada", email="ada@example.com", locale="en")
    db_session.add_all([ops, ada])
    db_session.commit()
    cookies = _session_cookie(ops)
    listed = client.get("/v1/admin/users", params={"q": "ada@"}, cookies=cookies)
    assert listed.status_code == 200
    emails = [row["email"] for row in listed.json()["items"]]
    assert emails == ["ada@example.com"]
    assert listed.json()["total"] == 1
    detail = client.get(f"/v1/admin/users/{ada.id}", cookies=cookies)
    assert detail.status_code == 200
    assert detail.json()["google_sub"] == "sub-ada"
    assert detail.json()["resumes"] == []
    missing = client.get("/v1/admin/users/not-a-user", cookies=cookies)
    assert missing.status_code == 404


def test_admin_reads_other_user_and_guest_resume(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    ada = User(google_sub="sub-ada", email="ada@example.com", locale="en")
    db_session.add_all([ops, ada])
    db_session.commit()
    guest_resume = client.post("/v1/resumes", json={"locale": "en", "title": "Guest CV"}).json()
    db_session.add(
        ChatMessage(resume_id=guest_resume["id"], role="user", content="hello from guest")
    )
    db_session.commit()
    client.cookies.clear()
    other = client.get(
        f"/v1/resumes/{guest_resume['id']}",
        cookies=_session_cookie(ops),
    )
    assert other.status_code == 404

    cookies = _session_cookie(ops)
    listed = client.get("/v1/admin/resumes", params={"owner": "guest"}, cookies=cookies)
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()["items"]]
    assert guest_resume["id"] in ids
    detail = client.get(f"/v1/admin/resumes/{guest_resume['id']}", cookies=cookies)
    assert detail.status_code == 200
    assert "typst_source" in detail.json()
    assert detail.json()["owner_kind"] == "guest"
    msgs = client.get(f"/v1/admin/resumes/{guest_resume['id']}/messages", cookies=cookies)
    assert msgs.status_code == 200
    assert any(m["content"] for m in msgs.json()["items"])


def test_admin_resume_invalid_filter_422(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "OPS@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    res = client.get(
        "/v1/admin/resumes",
        params={"owner": "nope"},
        cookies=_session_cookie(ops),
    )
    assert res.status_code == 422


def test_admin_preview_and_ats_for_foreign_resume(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    guest = client.post("/v1/resumes", json={"locale": "en"}).json()
    cookies = _session_cookie(ops)
    preview = client.get(f"/v1/admin/resumes/{guest['id']}/preview", cookies=cookies)
    if preview.status_code != 503:
        assert preview.status_code == 200
        assert "pages" in preview.json()
    ats = client.get(f"/v1/admin/resumes/{guest['id']}/ats", cookies=cookies)
    if ats.status_code != 503:
        assert ats.status_code == 200
        assert "checks" in ats.json()
        assert "score" not in ats.json()


class _FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttpx:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, data=None):
        return _FakeResp(200, {"access_token": "tok"})

    def get(self, url, headers=None):
        return _FakeResp(
            200,
            {
                "sub": "g-admin-next",
                "email": "nav-admin@example.com",
                "picture": "https://lh3.googleusercontent.com/a/from-google",
            },
        )


def _safe_next_cases(client, monkeypatch, next_value, expected_location):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _FakeHttpx)
    start = client.get(
        "/v1/auth/google/start",
        params={"next": next_value},
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query).get("state", [""])[0]
    cb = client.get(
        "/v1/auth/google/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == expected_location


def test_google_next_admin_allowed(client, monkeypatch):
    _safe_next_cases(client, monkeypatch, "/admin", "/admin")


def test_google_next_admin_nested_allowed(client, monkeypatch):
    _safe_next_cases(client, monkeypatch, "/admin/resumes/x", "/admin/resumes/x")


def test_google_next_evil_ignored(client, monkeypatch):
    _safe_next_cases(client, monkeypatch, "https://evil.test", "/dashboard")


def _admin_client(client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    admin = User(google_sub="sub-ops-keys", email="ops@example.com", locale="en")
    db_session.add(admin)
    db_session.commit()
    client.cookies.set(
        SESSION_COOKIE, _sign(admin.id, get_settings().auth_token_secret)
    )
    return admin


def test_mint_talent_key_returns_the_token_once(
    client: TestClient, db_session, monkeypatch
):
    _admin_client(client, db_session, monkeypatch)
    created = client.post(
        "/v1/admin/talent/keys",
        json={"label": "Acme Talent", "contact": "talent@acme.example"},
    )
    assert created.status_code == 200
    body = created.json()
    token = body["token"]
    assert token.startswith("ofk_")
    assert body["created_by"] == "ops@example.com"

    # Only the hash is stored, so the raw token appears nowhere again.
    key = db_session.query(TalentApiKey).one()
    assert key.key_hash == hash_talent_key(token)
    listed = client.get("/v1/admin/talent/keys").json()
    assert listed["total"] == 1
    assert "token" not in listed["items"][0]
    assert token not in json.dumps(listed)


def test_mint_talent_key_requires_a_label_and_contact(
    client: TestClient, db_session, monkeypatch
):
    _admin_client(client, db_session, monkeypatch)
    assert (
        client.post(
            "/v1/admin/talent/keys", json={"label": " ", "contact": "a@b.example"}
        ).status_code
        == 422
    )


def test_revoking_a_key_keeps_the_row(client: TestClient, db_session, monkeypatch):
    _admin_client(client, db_session, monkeypatch)
    key_id = client.post(
        "/v1/admin/talent/keys",
        json={"label": "Acme", "contact": "a@acme.example"},
    ).json()["id"]

    assert client.delete(f"/v1/admin/talent/keys/{key_id}").status_code == 204
    db_session.expire_all()
    key = db_session.query(TalentApiKey).one()
    assert key.revoked_at is not None
    assert client.delete("/v1/admin/talent/keys/nope").status_code == 404


def test_talent_key_routes_are_invisible_to_everyone_else(
    client: TestClient, db_session, monkeypatch
):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@example.com")
    get_settings.cache_clear()
    user = User(google_sub="sub-plain", email="plain@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    client.cookies.set(SESSION_COOKIE, _sign(user.id, get_settings().auth_token_secret))
    assert client.get("/v1/admin/talent/keys").status_code == 404
    assert (
        client.post(
            "/v1/admin/talent/keys",
            json={"label": "Mine", "contact": "me@example.com"},
        ).status_code
        == 404
    )

def _ops(db_session) -> User:
    user = User(google_sub="sub-ingest-ops", email="ops@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _ingest_row(db_session, *, run_id, source, slug, status, upserted=0, started=None, error=None, fetched=None):
    db_session.add(
        JobIngestRun(
            run_id=run_id,
            source=source,
            board_slug=slug,
            started_at=started or datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
            finished_at=(started or datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc))
            + timedelta(seconds=30),
            status=status,
            ok_count=(fetched if fetched is not None else (1 if status == "ok" else 0)),
            error_count=0 if status == "ok" else 1,
            upserted_count=upserted,
            expired_count=0,
            error_snippet=error,
        )
    )


def _job(db_session, *, source_id, last_seen_at):
    db_session.add(
        Job(
            source="greenhouse",
            source_id=source_id,
            company="Stripe",
            title="Engineer",
            location="Remote",
            remote=True,
            apply_url="https://example.com/a",
            source_url="https://example.com/a",
            description_text="x",
            description_html="<p>x</p>",
            posted_at=last_seen_at,
            first_seen_at=last_seen_at,
            last_seen_at=last_seen_at,
            is_active=True,
        )
    )


def test_admin_jobs_ingest_unauthenticated_401(client: TestClient, monkeypatch):
    _allow(monkeypatch)
    assert client.get("/v1/admin/jobs/ingest").status_code == 401


def test_admin_jobs_ingest_non_admin_404(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    user = User(google_sub="sub-user-ingest", email="user@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    assert client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(user)).status_code == 404


def test_admin_jobs_ingest_empty_allowlist_404(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "")
    get_settings.cache_clear()
    user = _ops(db_session)
    assert client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(user)).status_code == 404


def test_admin_jobs_ingest_folds_rows_into_passes(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    early = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)
    late = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    # One clean pass, one that lost a board but still refreshed the catalog.
    _ingest_row(db_session, run_id="run-1", source="greenhouse", slug="a", status="ok", upserted=5, started=early)
    _ingest_row(db_session, run_id="run-1", source="lever", slug="b", status="ok", upserted=3, started=early)
    _ingest_row(db_session, run_id="run-2", source="greenhouse", slug="a", status="ok", upserted=7, started=late)
    _ingest_row(
        db_session, run_id="run-2", source="ashby", slug="dead", status="error",
        started=late, error="board not found",
    )
    db_session.commit()

    body = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()
    passes = body["passes"]
    assert [item["run_id"] for item in passes] == ["run-2", "run-1"]

    newest = passes[0]
    assert newest["sources_ok"] == 1
    assert newest["sources_failed"] == 1
    assert newest["upserted"] == 7
    assert newest["status"] == "degraded"
    assert newest["duration_seconds"] == 30
    assert [b["board_slug"] for b in newest["failing_boards"]] == ["dead"]
    assert newest["failing_boards"][0]["error_snippet"] == "board not found"

    assert passes[1]["status"] == "ok"
    assert passes[1]["sources_failed"] == 0
    assert passes[1]["upserted"] == 8
    assert passes[1]["failing_boards"] == []


def test_admin_jobs_ingest_marks_a_pass_that_refreshed_nothing_failed(
    client: TestClient, db_session, monkeypatch
):
    """Must match the rule the systemd unit exits non-zero on."""
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    _ingest_row(db_session, run_id="run-dead", source="greenhouse", slug="a", status="error", error="boom")
    _ingest_row(db_session, run_id="run-dead", source="ashby", slug="b", status="error", error="boom")
    # Every board answered, none had anything to give: still a stale catalog.
    _ingest_row(db_session, run_id="run-empty", source="greenhouse", slug="a", status="ok", upserted=0)
    db_session.commit()

    body = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()
    status_by_run = {item["run_id"]: item["status"] for item in body["passes"]}
    assert status_by_run == {"run-dead": "failed", "run-empty": "failed"}
    assert body["health"]["last_failure_at"] is not None


def test_admin_jobs_ingest_degraded_pass_is_not_a_failure(
    client: TestClient, db_session, monkeypatch
):
    """A pass that lost some boards but refreshed the catalog is not a failure.

    63 of 118 boards fail every pass, so if degraded counted here the dashboard
    would show a permanent "last failed pass" and stop meaning anything.
    """
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    _ingest_row(db_session, run_id="run-1", source="greenhouse", slug="a", status="ok", upserted=9)
    _ingest_row(db_session, run_id="run-1", source="ashby", slug="dead", status="error", error="board not found")
    db_session.commit()

    body = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()
    assert body["passes"][0]["status"] == "degraded"
    assert body["health"]["last_failure_at"] is None


def test_admin_jobs_ingest_health_splits_fresh_from_stale(
    client: TestClient, db_session, monkeypatch
):
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    now = datetime.now(timezone.utc)
    _job(db_session, source_id="fresh", last_seen_at=now - timedelta(hours=2))
    _job(db_session, source_id="stale", last_seen_at=now - timedelta(days=JOB_FRESHNESS_DAYS + 1))
    db_session.commit()

    health = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()["health"]
    assert health["fresh_job_count"] == 1
    assert health["stale_job_count"] == 1
    assert health["freshness_days"] == JOB_FRESHNESS_DAYS
    assert health["expected_interval_hours"] == 6
    assert health["catalog_age_hours"] is not None
    assert health["catalog_age_hours"] < 3
    assert health["overdue"] is False


def test_admin_jobs_ingest_overdue_when_catalog_is_old(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    _job(
        db_session,
        source_id="old",
        last_seen_at=datetime.now(timezone.utc) - timedelta(hours=30),
    )
    db_session.commit()

    health = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()["health"]
    assert health["overdue"] is True


def test_admin_jobs_ingest_overdue_when_catalog_is_empty(client: TestClient, db_session, monkeypatch):
    """Nothing to serve is not healthy just because nothing is old."""
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    health = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()["health"]
    assert health["freshest_last_seen_at"] is None
    assert health["catalog_age_hours"] is None
    assert health["overdue"] is True


def test_admin_jobs_ingest_counts_rows_with_no_run_id_separately(
    client: TestClient, db_session, monkeypatch
):
    """Pre-migration rows cannot be grouped, so they are reported, not guessed."""
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    _ingest_row(db_session, run_id=None, source="greenhouse", slug="a", status="ok", upserted=4)
    _ingest_row(db_session, run_id="run-1", source="greenhouse", slug="a", status="ok", upserted=4)
    db_session.commit()

    body = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()
    assert body["legacy_row_count"] == 1
    assert [item["run_id"] for item in body["passes"]] == ["run-1"]


def test_admin_jobs_ingest_pass_drilldown_returns_every_board_row(
    client: TestClient, db_session, monkeypatch
):
    """The old endpoint capped at 20; a pass has ~119 rows."""
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    for i in range(25):
        _ingest_row(db_session, run_id="run-1", source="greenhouse", slug=f"b{i}", status="ok", upserted=1)
    db_session.commit()

    body = client.get("/v1/admin/jobs/ingest/run-1", cookies=_session_cookie(ops)).json()
    assert len(body["items"]) == 25
    assert body["limit"] >= 119


def test_admin_jobs_ingest_reports_what_the_scope_rules_dropped(
    client: TestClient, db_session, monkeypatch
):
    """fetched - upserted is how many postings we chose not to carry.

    Derived from the counts record_run already stores, so no column was added
    for it -- but it has to actually surface, or a filter eating a whole board
    would take weeks to notice.
    """
    _allow(monkeypatch, "ops@example.com")
    ops = _ops(db_session)
    _ingest_row(
        db_session, run_id="run-1", source="greenhouse", slug="a", status="ok",
        fetched=120, upserted=70,
    )
    db_session.commit()

    body = client.get("/v1/admin/jobs/ingest", cookies=_session_cookie(ops)).json()
    pass_row = body["passes"][0]
    assert pass_row["fetched"] == 120
    assert pass_row["upserted"] == 70


# --------------------------------------------------------------------------
# usage tracking (Task 9)
# --------------------------------------------------------------------------


def _user(db_session, *, sub):
    user = User(google_sub=sub, email=f"{sub}@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    return user


def _spend_as(db_session, user=None, guest=None, *, turns=1, input_tokens=20_000):
    from app.services.metering import Meter, system_guard

    meter = Meter(system_guard(user=user, guest=guest, purpose="chat", reason="test"))
    for _ in range(turns):
        db_session.add(
            LlmCall(
                user_id=user.id if user else None,
                guest_session_id=guest.id if guest else None,
                purpose="chat",
                model="gpt-5.6-terra",
                round_index=0,
                input_tokens=input_tokens,
            )
        )
        db_session.commit()
        meter.record(
            {
                "model": "gpt-5.6-terra",
                "purpose": "chat",
                "round_index": 0,
                "input_tokens": input_tokens,
            }
        )


def test_usage_overview_ranks_the_biggest_spenders_first(
    client: TestClient, db_session, monkeypatch
):
    """The question this page exists to answer: who is costing the most."""
    admin = _admin_client(client, db_session, monkeypatch)
    small = _user(db_session, sub="sub-small")
    big = _user(db_session, sub="sub-big")
    _spend_as(db_session, user=small, turns=1, input_tokens=10_000)
    _spend_as(db_session, user=big, turns=3, input_tokens=50_000)

    body = client.get("/v1/admin/usage").json()
    ranked = [row["subject_id"] for row in body["top_subjects"]]
    assert ranked[0] == big.id
    assert small.id in ranked
    top = body["top_subjects"][0]
    assert top["email"] == big.email
    assert top["turns_used"] == 3
    assert top["credits_used"] > 0
    assert top["tier"] == "registered"
    assert admin is not None


def test_usage_overview_names_a_guest_without_inventing_an_email(
    client: TestClient, db_session, monkeypatch
):
    _admin_client(client, db_session, monkeypatch)
    guest = GuestSession(key_hash="u" * 64)
    db_session.add(guest)
    db_session.commit()
    _spend_as(db_session, guest=guest)
    row = client.get("/v1/admin/usage").json()["top_subjects"][0]
    assert (row["subject_kind"], row["email"], row["tier"]) == ("guest", None, "guest")
    assert row["turn_limit"] is not None and row["limit_credits"] is None


def test_usage_overview_shows_both_ceilings_and_the_share_used(
    client: TestClient, db_session, monkeypatch
):
    """A ceiling nobody is told about is an outage with no explanation."""
    _admin_client(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-ceil")
    _spend_as(db_session, user=user, input_tokens=100_000)
    ceilings = {c["name"]: c for c in client.get("/v1/admin/usage").json()["ceilings"]}
    assert set(ceilings) == {"global", "guest_pool"}
    assert ceilings["global"]["spent_uusd"] > 0
    assert 0 < ceilings["global"]["share"] < 1


def test_a_disabled_ceiling_reports_no_share_instead_of_dividing_by_zero(
    client: TestClient, db_session, monkeypatch
):
    _admin_client(client, db_session, monkeypatch)
    monkeypatch.setenv("GLOBAL_WEEKLY_CEILING_UUSD", "0")
    get_settings.cache_clear()
    ceilings = {c["name"]: c for c in client.get("/v1/admin/usage").json()["ceilings"]}
    assert ceilings["global"]["ceiling_uusd"] is None
    assert ceilings["global"]["share"] is None


def test_usage_overview_surfaces_a_reconciliation_gap(
    client: TestClient, db_session, monkeypatch
):
    """A counter with no telemetry behind it means a path billed unmetered."""
    from app.services.metering import Meter, system_guard

    _admin_client(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-gap")
    # Counter only, no LlmCall row -- the shape a missed wrap would leave.
    Meter(system_guard(user=user, purpose="chat", reason="test")).record(
        {"model": "gpt-5.6-terra", "purpose": "chat", "round_index": 0,
         "input_tokens": 10_000}
    )
    gaps = client.get("/v1/admin/usage").json()["gaps"]
    assert [(g["subject_id"], g["llm_call_uusd"]) for g in gaps] == [(user.id, 0)]


def test_the_user_list_can_be_ordered_by_spend_without_hiding_idle_users(
    client: TestClient, db_session, monkeypatch
):
    """An inner join would make "sort by usage" also mean "hide the idle"."""
    _admin_client(client, db_session, monkeypatch)
    idle = _user(db_session, sub="sub-idle")
    spender = _user(db_session, sub="sub-spender")
    _spend_as(db_session, user=spender, turns=2, input_tokens=40_000)

    page = client.get("/v1/admin/users?order=usage").json()
    ids = [row["id"] for row in page["items"]]
    assert ids[0] == spender.id
    assert idle.id in ids, "an idle user fell out of the usage ordering"
    top = next(row for row in page["items"] if row["id"] == spender.id)
    assert top["turns_used"] == 2 and top["credits_used"] > 0
    assert next(row for row in page["items"] if row["id"] == idle.id)["credits_used"] == 0


def test_user_detail_carries_this_week_and_lifetime(
    client: TestClient, db_session, monkeypatch
):
    _admin_client(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-detail")
    _spend_as(db_session, user=user, turns=2, input_tokens=20_000)
    usage = client.get(f"/v1/admin/users/{user.id}").json()["usage"]
    assert usage["tier"] == "registered"
    assert usage["turns_used"] == 2
    assert usage["lifetime_turns"] == 2
    assert usage["lifetime_uusd"] > 0
    assert usage["limit_credits"] is not None
    assert usage["reset_at"]


def test_the_day_series_prices_rows_through_the_enforced_function(
    client: TestClient, db_session, monkeypatch
):
    """Admin and enforcement must not disagree about what a round cost."""
    from app.services.usage import round_cost_uusd

    _admin_client(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-series")
    _spend_as(db_session, user=user, turns=1, input_tokens=1_000_000)
    rows = db_session.query(LlmCall).all()
    expected = sum(round_cost_uusd(r)[0] for r in rows)
    series = client.get("/v1/admin/overview").json()["series"]
    assert sum(point["llm_cost_uusd"] for point in series) == expected


def test_a_correctly_metered_user_is_not_reported_as_a_gap(
    client: TestClient, db_session, monkeypatch
):
    """Regression. The counter truncates integer micro-USD once per round while
    a per-model aggregate truncates once at the end, so pricing the two
    differently made every correctly metered subject show up as a gap -- a
    reconciliation that cries wolf is worse than none. Caught by running the
    admin page against seeded data, not by a test, which is why this exists.
    """
    _admin_client(client, db_session, monkeypatch)
    user = _user(db_session, sub="sub-nogap")
    # Several rounds, so per-round truncation would accumulate a difference.
    _spend_as(db_session, user=user, turns=6, input_tokens=90_000)
    gaps = client.get("/v1/admin/usage").json()["gaps"]
    assert gaps == [], f"correctly metered user reported as a gap: {gaps}"
