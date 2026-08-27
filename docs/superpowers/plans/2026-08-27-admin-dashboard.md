# Admin Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only founder ops console at `/{locale}/admin` so an `ADMIN_EMAILS` allowlisted Google user can list users/resumes, inspect Typst/chat/preview/ATS, and see health plus chat volume.

**Architecture:** Same Next app and `offerfy_session` cookie. FastAPI `/v1/admin/*` with `require_admin` (401 unsigned, 404 non-admin or empty allowlist). Owner `/v1/resumes/*` stay owner-only. Sidebar `AdminShell`; inspect page uses tabs Source | Chat | Preview | ATS.

**Tech Stack:** Next 16.1.6, FastAPI, SQLAlchemy 2, Alembic, pytest, next-intl, existing CodeMirror Typst editor and ATS strip.

**Spec:** [docs/superpowers/specs/2026-08-27-admin-dashboard-design.md](../specs/2026-08-27-admin-dashboard-design.md)

## Global Constraints

- Product name is Offerfy. Never CareerOS, Roleloop, Offerly, Offerloop, or RenderResume in UI copy.
- Locales: `en`, `zh-TW`, `zh-CN` only. Default `zh-TW`.
- Read-only. No delete, impersonate, edit, export PDF endpoint, or token/cost pipeline.
- Non-admins see 404, not 403. Empty `ADMIN_EMAILS` → 404 for everyone.
- Owner routes must not start working for other people's resumes just because the cookie is an admin.
- No public Nav/Footer link to `/admin`. Admin pages `robots: noindex`.
- Do not commit unless the user asks.
- Verify admin UI in the browser before claiming complete (login as allowlisted email, 404 as a normal user, inspect a guest resume).
- Backend tests: `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q`

## File map

- Create: `apps/backend/alembic/versions/004_created_at.py` — `users.created_at`, `resumes.created_at`
- Modify: `apps/backend/app/models.py` — those columns
- Modify: `apps/backend/app/config.py`, `.env.example` — `ADMIN_EMAILS`
- Modify: `apps/backend/app/deps.py` — `require_admin`, `admin_email_set`, `load_resume_for_admin`
- Create: `apps/backend/app/schemas_admin.py`
- Create: `apps/backend/app/routers/admin.py`
- Modify: `apps/backend/app/main.py` — include admin router
- Modify: `apps/backend/app/routers/auth.py` — `next` / OAuth `state`
- Create: `apps/backend/tests/test_admin.py`
- Modify: `apps/backend/tests/test_auth.py` — OAuth next cases (or keep them in `test_admin.py`)
- Modify: `apps/frontend/lib/api.ts` — admin types + fetches; `googleStartUrl(next?)`
- Modify: `apps/frontend/components/editor/TypstSourceEditor.tsx` — `readOnly?: boolean`
- Create: `apps/frontend/components/admin/AdminShell.tsx`, `AdminGate.tsx`
- Create: `apps/frontend/app/[locale]/admin/layout.tsx` and page routes
- Modify: `apps/frontend/app/[locale]/login/page.tsx` — pass `next`
- Modify: `apps/frontend/messages/{en,zh-TW,zh-CN}.json` — `admin.*`
- Modify: `apps/frontend/app/[locale]/globals.css` — `.admin-shell` sidebar

---

### Task 1: created_at columns

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/004_created_at.py`
- Test: `apps/backend/tests/test_admin.py` (started here; grow in later tasks)

**Interfaces:**
- Consumes: existing `User`, `Resume`, `_utcnow`
- Produces: `User.created_at: datetime`, `Resume.created_at: datetime` (timezone-aware, default `_utcnow`)

- [ ] **Step 1: Write a failing test that new users/resumes expose created_at on the model**

```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import SESSION_COOKIE, _sign
from app.models import Resume, User


def test_user_and_resume_have_created_at(client: TestClient, db_session):
    user = User(google_sub="sub-ts", email="ts@example.com", locale="en")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    assert user.created_at.tzinfo is not None
    assert user.created_at <= datetime.now(timezone.utc)

    created = client.post("/v1/resumes", json={"locale": "en", "title": "T"}).json()
    resume = db_session.get(Resume, created["id"])
    assert resume is not None
    assert resume.created_at.tzinfo is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest tests/test_admin.py::test_user_and_resume_have_created_at -v`

Expected: FAIL (`created_at` missing on model)

- [ ] **Step 3: Add columns on models**

On `User` and `Resume`, same pattern as `GuestSession.created_at`:

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), nullable=False, default=_utcnow
)
```

- [ ] **Step 4: Alembic `004_created_at`**

`down_revision = "003_user_picture"`. Add `created_at` on `users` and `resumes` with `server_default=sa.text("CURRENT_TIMESTAMP")`, `nullable=False`, via `batch_alter_table` (SQLite-safe, same as `003`).

- [ ] **Step 5: Re-run the test**

Expected: PASS

---

### Task 2: ADMIN_EMAILS + require_admin

**Files:**
- Modify: `apps/backend/app/config.py` — `admin_emails: str = ""`
- Modify: `.env.example` — `ADMIN_EMAILS=`
- Modify: `apps/backend/app/deps.py`
- Test: `apps/backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `User.email`, `get_current_user`, `get_settings`
- Produces:
  - `admin_email_set() -> set[str]` — split `settings.admin_emails` on commas, strip, casefold; drop empties
  - `require_admin(user: User | None = Depends(get_current_user)) -> User`
  - `load_resume_for_admin(resume_id: str, db: Session) -> Resume`

- [ ] **Step 1: Write failing authz tests against `GET /v1/admin/overview`**

Helper in `test_admin.py`:

```python
def _session_cookie(user: User) -> dict[str, str]:
    return {SESSION_COOKIE: _sign(user.id, get_settings().auth_token_secret)}


def _allow(monkeypatch, email: str = "ops@example.com") -> None:
    monkeypatch.setenv("ADMIN_EMAILS", email)
    get_settings.cache_clear()
```

Tests (all hit `/v1/admin/overview` once the router exists; until then they fail with 404 from FastAPI — after Task 3 they must keep these statuses):

```python
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
```

- [ ] **Step 2: Run tests — 401 test may fail with 404 until router exists; keep going**

- [ ] **Step 3: Implement settings + deps**

`config.py`: `admin_emails: str = ""`

```python
def admin_email_set() -> set[str]:
    raw = get_settings().admin_emails or ""
    return {part.strip().casefold() for part in raw.split(",") if part.strip()}


def require_admin(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    allowed = admin_email_set()
    if not allowed or (user.email or "").strip().casefold() not in allowed:
        raise HTTPException(status_code=404, detail="Not found")
    return user


def load_resume_for_admin(resume_id: str, db: Session) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Not found")
    return resume
```

`.env.example`:

```
# Comma-separated Google emails that may use /admin. Empty = nobody.
ADMIN_EMAILS=
```

- [ ] **Step 4: Do not ship a public 200 yet; Task 3 adds the router**

---

### Task 3: Overview API

**Files:**
- Create: `apps/backend/app/schemas_admin.py`
- Create: `apps/backend/app/routers/admin.py`
- Modify: `apps/backend/app/main.py` — `application.include_router(admin.router)`
- Test: `apps/backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `require_admin`, models, `get_settings().s3_configured()`
- Produces: `GET /v1/admin/overview` JSON exactly as in the spec (`health`, `counts`, `recent_users`, `recent_resumes`)

- [ ] **Step 1: Failing test — allowlisted 200 + counts**

```python
from datetime import datetime, timedelta, timezone

from app.models import ChatMessage


def test_admin_overview_allowlisted_counts(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
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
```

- [ ] **Step 2: Run — FAIL (no route or 404)**

- [ ] **Step 3: Implement schemas + router**

Helpers in `admin.py`:

```python
def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = 50 if limit is None else limit
    off = 0 if offset is None else offset
    if lim < 1 or lim > 100 or off < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    return lim, off


def _owner_kind_label(resume: Resume) -> tuple[str, str, str]:
    if resume.user_id:
        label = resume.user.email if resume.user is not None else resume.user_id
        return "user", resume.user_id, label
    gid = resume.guest_session_id or ""
    return "guest", gid, f"guest:{gid[:8]}"
```

Overview: `SELECT 1` in try/except → `health.database` `"ok"` | `"unavailable"`; still HTTP 200. Counts via `func.count`. Windows: `datetime.now(timezone.utc) - timedelta(hours=24)` and `days=7`. Recent lists: order `created_at` desc, limit 10. Eager-load `Resume.user` (`joinedload`). `message_count` as subquery or `len(resume.messages)` after loading; prefer `func.count(ChatMessage.id)` grouped by `resume_id` to avoid loading all messages.

Mount: `router = APIRouter()` with paths starting `/v1/admin/...`.

- [ ] **Step 4: Re-run Task 2 + Task 3 tests**

Expected: PASS (401 / 404 / 200 as specified)

---

### Task 4: Users API

**Files:**
- Modify: `apps/backend/app/schemas_admin.py`, `apps/backend/app/routers/admin.py`
- Test: `apps/backend/tests/test_admin.py`

**Interfaces:**
- Produces: `GET /v1/admin/users?q&limit&offset`, `GET /v1/admin/users/{id}` as spec JSON

- [ ] **Step 1: Failing tests**

```python
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
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

`q` → `User.email.ilike(f"%{q}%")` (escape `%`/`_` in `q` by replacing with `\%`/`\_` and using `escape="\\"` if the dialect supports it; for SQLite tests a simple `contains` via `ilike` is enough if tests do not use wildcards). Order `created_at` desc. `resume_count` = `func.count(Resume.id)` grouped by `user_id`.

- [ ] **Step 4: Re-run — PASS**

---

### Task 5: Resumes list, detail, messages

**Files:**
- Modify: `apps/backend/app/schemas_admin.py`, `apps/backend/app/routers/admin.py`
- Test: `apps/backend/tests/test_admin.py`

**Interfaces:**
- Produces: `GET /v1/admin/resumes`, `GET /v1/admin/resumes/{id}`, `GET /v1/admin/resumes/{id}/messages`

- [ ] **Step 1: Failing tests**

```python
def test_admin_reads_other_user_and_guest_resume(client: TestClient, db_session, monkeypatch):
    _allow(monkeypatch, "ops@example.com")
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    ada = User(google_sub="sub-ada", email="ada@example.com", locale="en")
    db_session.add_all([ops, ada])
    db_session.commit()
    guest_resume = client.post("/v1/resumes", json={"locale": "en", "title": "Guest CV"}).json()
    client.post(
        f"/v1/resumes/{guest_resume['id']}/chat",
        json={"message": "hello from guest"},
    )
    # Owner route must stay closed to admin:
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
    _allow(monkeypatch, "OPS@example.com")  # case-insensitive allowlist
    ops = User(google_sub="sub-ops", email="ops@example.com", locale="en")
    db_session.add(ops)
    db_session.commit()
    res = client.get(
        "/v1/admin/resumes",
        params={"owner": "nope"},
        cookies=_session_cookie(ops),
    )
    assert res.status_code == 422
```

If guest chat returns 400 without LLM key, insert `ChatMessage` directly in the DB instead of POST chat.

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement list/detail/messages**

Filters: `owner=user` → `Resume.user_id.isnot(None)`; `guest` → `guest_session_id.isnot(None)`; `source` in `create`/`upload`; `q` ilike on `title`. Invalid `owner`/`source` → 422. Messages ordered `created_at` asc; include tool roles; `created_at` ISO strings.

- [ ] **Step 4: Re-run — PASS** including owner-route 404

---

### Task 6: Admin preview + ATS

**Files:**
- Modify: `apps/backend/app/routers/admin.py`
- Test: `apps/backend/tests/test_admin.py`

**Interfaces:**
- Consumes: `compile_typst_pages`, `compile_typst`, `analyze_pdf` (same as `resumes.py`)
- Produces: `GET /v1/admin/resumes/{id}/preview` → `PreviewPages`; `GET /v1/admin/resumes/{id}/ats` → `AtsReport`

- [ ] **Step 1: Failing test**

```python
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
```

- [ ] **Step 2: Implement by copying the owner handlers but using `load_resume_for_admin` — do not call `enforce_guest_rate`**

- [ ] **Step 3: Re-run `tests/test_admin.py` + `tests/test_guest_resume.py::test_ats_report_has_checks_not_score`**

Expected: PASS

---

### Task 7: OAuth `next` allowlist

**Files:**
- Modify: `apps/backend/app/routers/auth.py`
- Test: `apps/backend/tests/test_admin.py` (reuse `_FakeHttpx` pattern from `test_auth.py`)

**Interfaces:**
- Consumes: `GET /v1/auth/google/start?next=`
- Produces: callback `RedirectResponse` to `/admin…` only when `next` is a relative path that is `/admin` or starts with `/admin/`, and contains no `//`, `://`, or `\`

- [ ] **Step 1: Failing tests**

```python
def _safe_next_cases(client, monkeypatch, next_value, expected_location):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    monkeypatch.setattr("app.routers.auth.httpx.Client", _FakeHttpx)  # copy class into test_admin or import
    start = client.get(
        "/v1/auth/google/start",
        params={"next": next_value},
        follow_redirects=False,
    )
    assert start.status_code == 302
    from urllib.parse import parse_qs, urlparse
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
```

Copy `_FakeHttpx` / `_FakeResp` into `test_admin.py` (do not import private test helpers from `test_auth` if that creates a cycle; duplication is OK). `_FakeHttpx.get` must still return a stable `sub`/`email` so the user upsert works.

- [ ] **Step 2: Implement `_safe_admin_next` and pass `state` through Google**

`google_start`: if `_safe_admin_next(request.query_params.get("next"))` then set `params["state"]` to that path (URL-encoded by `urlencode`).

`google_callback`: `next_path = _safe_admin_next(request.query_params.get("state"))`; redirect to `next_path or "/dashboard"`.

Reject `/administration` (must be exactly `/admin` or prefix `/admin/`).

- [ ] **Step 3: Re-run — PASS**

---

### Task 8: Frontend API + readOnly Typst editor

**Files:**
- Modify: `apps/frontend/lib/api.ts`
- Modify: `apps/frontend/components/editor/TypstSourceEditor.tsx`
- Modify: `apps/frontend/app/[locale]/login/page.tsx`

**Interfaces:**
- Produces: `adminOverview`, `adminUsers`, `adminUser`, `adminResumes`, `adminResume`, `adminResumeMessages`, `adminResumePreview`, `adminResumeAts`; `googleStartUrl(next?: string)`
- Produces: `TypstSourceEditorProps.readOnly?: boolean`; `onChange` optional when `readOnly`

- [ ] **Step 1: Types matching spec** (`AdminOverview`, `AdminUserListItem`, `AdminResumeListItem`, paginated `{ items, total }`)

- [ ] **Step 2: Fetch helpers using `apiJson` to `/api/v1/admin/...`**

`googleStartUrl`:

```ts
export function googleStartUrl(next?: string): string {
  if (next && (next === "/admin" || next.startsWith("/admin/"))) {
    return `/api/v1/auth/google/start?next=${encodeURIComponent(next)}`;
  }
  return "/api/v1/auth/google/start";
}
```

- [ ] **Step 3: Login page** — `useSearchParams()`, read `next`, pass to `googleStartUrl`. Wrap with `Suspense` if Next requires it for `useSearchParams`.

- [ ] **Step 4: CodeMirror `EditorState.readOnly.of(true)` when `readOnly`; textarea fallback `readOnly`; skip `onChange` when read-only**

Existing editor callers keep passing `onChange`. Admin passes `readOnly` and omit or no-op `onChange`.

---

### Task 9: AdminShell, gate, i18n, overview + lists

**Files:**
- Create: `apps/frontend/components/admin/AdminShell.tsx`
- Create: `apps/frontend/components/admin/AdminGate.tsx`
- Create: `apps/frontend/app/[locale]/admin/layout.tsx`
- Create: pages: `admin/page.tsx`, `admin/users/page.tsx`, `admin/users/[id]/page.tsx`, `admin/resumes/page.tsx`
- Modify: `apps/frontend/messages/en.json`, `zh-TW.json`, `zh-CN.json`
- Modify: `apps/frontend/app/[locale]/globals.css`

**Interfaces:**
- Consumes: admin GET helpers; `ApiError`
- Produces: gated sidebar chrome; Overview / Users / User detail / Resumes list

- [ ] **Step 1: `admin` message keys in all three locales**

Include at least: `metaTitle`, `ops`, `overview`, `users`, `resumes`, `dashboard`, `signIn`, `empty`, `search`, `healthApi`, `healthDb`, `healthS3`, `counts` labels, table headers (`email`, `locale`, `resumeCount`, `created`, `title`, `owner`, `source`, `importStatus`, `messages`), filters (`all`, `ownerUser`, `ownerGuest`, `sourceCreate`, `sourceUpload`), inspect tabs (`source`, `chat`, `preview`, `ats`), `notFound` not required (use Next 404).

- [ ] **Step 2: Server `admin/layout.tsx`**

`generateMetadata`: `robots: { index: false, follow: false }`, title from `admin.metaTitle`. Render `<AdminGate>{children}</AdminGate>`.

- [ ] **Step 3: `AdminGate` (client)**

On mount: `adminOverview()`. `ApiError` 401 → `router.replace("/login?next=/admin")` via `@/i18n/navigation`. 404 → `notFound()` from `next/navigation`. Else render `AdminShell`.

- [ ] **Step 4: `AdminShell`**

Fixed left sidebar `background #1a1f2e`, text `#c9d1d9`, nav links Overview / Users / Resumes (`Link` from i18n). Active state from `usePathname()`. Footer: email from `/api/v1/auth/me` (already fetched if you pass it from gate), locale + theme switchers, link to `/dashboard`. Main pane uses existing `--paper` / `--ink`. No marketing `Nav`.

- [ ] **Step 5: Overview page** — cards from `counts`, health row, two recent tables linking to user/resume detail.

- [ ] **Step 6: Users list + detail, Resumes list** — search input + filters; prev/next using `limit=50` and `offset`. Empty state `admin.empty`.

- [ ] **Step 7: CSS** `.admin-shell { display:flex; min-height:100vh; }` sidebar `width: 14rem; flex-shrink:0;`

---

### Task 10: Resume inspect tabs

**Files:**
- Create: `apps/frontend/app/[locale]/admin/resumes/[id]/page.tsx`
- Reuse: `TypstSourceEditor` (readOnly), `ChatMessageCard` or a simpler preformatted log, `AtsStrip` without `onFix`, SVG pages like `EditorPreview` (no download button)

**Interfaces:**
- Consumes: `adminResume`, `adminResumeMessages`, `adminResumePreview`, `adminResumeAts`, `typstCompileDetail`

- [ ] **Step 1: Sticky header** — title, `owner_label` (link to `/admin/users/{owner_id}` when `owner_kind === "user"`), source, import_status, claimed_at, created_at, message_count

- [ ] **Step 2: Radix `Tabs`** — Source | Chat | Preview | ATS

Source: `<TypstSourceEditor value={typst_source} readOnly ariaLabel={t("source")} />`

Chat: fetch messages when the tab is selected (or with the resume); render chronological cards. Reuse `ChatMessageCard` with no `onRestoreEdit`. Map `created_at` onto `ChatMessage.timestamp` if needed.

Preview: on tab select, `adminResumePreview`; map each SVG string to `data:image/svg+xml;charset=utf-8,` + `encodeURIComponent`; show `typstCompileDetail` on failure.

ATS: on tab select, `adminResumeAts`; `<AtsStrip report={report} />` with no fix handler; same compile error treatment.

- [ ] **Step 3: Browser check** — allowlisted Google user opens `/admin`, lists a guest resume, reads source + chat + preview/ATS. A non-allowlisted signed-in user hitting `/admin` gets the same 404 as a missing route. Confirm no link in `Nav`/`Footer`.

---

## Self-review

1. Spec coverage: authz, schema, overview, users, resumes, messages, preview, ATS, OAuth next, i18n, noindex, owner isolation, tests — each has a task.
2. No placeholders: API shapes live in the spec; this plan points there and copies the critical tests.
3. Names: `require_admin`, `admin_email_set`, `load_resume_for_admin`, `/v1/admin/*`, `ADMIN_EMAILS` are consistent with the spec.
