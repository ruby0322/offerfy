# MCP Agent Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in Offerfy user mint their own bearer token and drive their own account — resumes, publishing, export, job preferences, job search — from their own AI assistant over MCP.

**Architecture:** A new `POST /mcp` router authenticates with a user-scoped bearer token (`ofa_`) through one dependency, then dispatches `tools/call` to pure functions in `app/services/mcp_tools.py`. Tool specs come from the existing provider-neutral surface so there is one `search_jobs` in the codebase, not two. Every call passes through an audit/quota context manager that writes on its own database session. Nothing in the tool set calls a model.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, alembic, pytest, Next.js 15 (App Router), TypeScript, `node:test`.

**Spec:** [docs/superpowers/specs/2026-11-20-mcp-agent-api-design.md](../specs/2026-11-20-mcp-agent-api-design.md)

## Global Constraints

- **No new Python dependencies.** `requirements.txt` stays 14 lines. The JSON-RPC envelope is hand-written; do not add the `mcp` SDK.
- **No tool may call a model.** `tests/test_metering.py::test_billable_call_sites_are_wrapped` must not gain an entry because of this work. If it does, a tool was added that should not exist.
- **Every new route carries `include_in_schema=False`,** whether it sits on a new router or on an existing one. `create_app()` passes no `openapi_url` and the Next rewrite publishes `/api/openapi.json`; a route in that document is a route described on the open web.
- **A tool handler holds an `InFlightGate` slot for its whole life, so no handler may sleep, poll, or retry.** The base commit added `app/concurrency.py`: `InFlightGate` is the outermost middleware, `MAX_IN_FLIGHT` is 32 per worker, and a request over `GATE_TIMEOUT` is shed with a 503. Releasing the database connection before a compile — which this plan mandates — protects the pool, not the gate. Do not add a retry loop, a backoff, or a wait of any kind inside a tool.
- **Do not touch `application.add_middleware(InFlightGate)` in `app/main.py`.** It is an unrelated production fix and must stay outermost.
- **`typst` is not on PATH in this worktree.** The binary is `apps/backend/.tools/typst`, reached through a symlink to the main checkout, as are `.venv` and `apps/frontend/node_modules`. Run every command from this worktree.
- **Token prefix is `ofa_`.** Never `ofk_` (that is `TalentApiKey`). The two credentials share no table, no prefix and no hash namespace.
- **Hashing is unsalted SHA-256** of the raw token, in a function of this module's own — do not call `hash_talent_key`, and do not substitute bcrypt.
- **`require_agent_caller` has no fallthrough.** Session cookie, guest cookie and admin account all get 401. Every failure is the same 401 with detail `"Credential required"` and header `WWW-Authenticate: Bearer`.
- **Audit rows and `last_used_at` are written on a session from `get_session_factory()`,** never the request's. `get_db` rolls back on any exception.
- **Max 5 live keys per user**; a 6th mint is 400.
- **Quota is 300 calls per key per hour,** counted on `agent_tool_calls` before any work, excluding rows whose outcome is `throttled` (the quota's own refusals — see Task 3).
- **Outcomes are exactly `ok` | `refused` | `error` | `throttled`.**
- **A tool's `resume_id` argument is validated before it reaches the audit handle** — a string of at most 36 characters, or `None`. Task 4 owns that check; `mcp_audit.record` coerces anything else to `None` defensively so an audit write can never be what fails.
- **Export links live 10 minutes.**
- **UI strings land in all three locales** (`messages/{en,zh-TW,zh-CN}.json`) and are designed and checked in zh-TW, not English.
- **Never run prettier on this repo.** No config; lines are hand-wrapped at ~90.
- **Validate the type of every argument a tool reads.** Every browser-facing route in this codebase has a Pydantic model in front of it, so handlers may assume their arguments are already the right type. **MCP arguments arrive as raw JSON with no model at all** — `args.get("title")` can be a list, a float or a dict, whatever the agent's JSON contained. Measured consequences before this was a rule: `create_tailor` crashed with an `AttributeError` on a float title (rendered to the agent as "The tool failed unexpectedly" with an `error` audit row instead of a usable 400), and `create_resume` silently accepted `[]`, `{}`, `0` and `False` as a locale and made a zh-TW resume, because `locale or "zh-TW"` treats every falsy value as unset. A wrong type is a 400 with a message that says which argument; only an **absent** argument may fall back to a default. Reuse one checker per argument shape rather than writing a second — two paths that validate the same thing differently is how the float-title crash happened while the sibling tool handled it.

  One deliberate exception: `search_jobs` and `list_matches` coerce a malformed `limit` to the default rather than refusing, matching what `jobs_tools` has always done for the chat surface. A limit is a hint, not an instruction.
- **Every assertion must be able to fail.** A test that would still pass under the wrong implementation is not coverage, it is decoration — and five of them have already shipped on this branch and been caught in review: a test whose premise a later task invalidated, one that asserted only a status code where the error code was the point, one that deleted a row the foreign key cascaded away so the branch under test was never reached, one that promised a guest case and never created a guest, and one that checked a cap with fewer rows than the cap. Before writing an assertion, ask what wrong code it would catch; if the answer is "none", either strengthen it or delete it. Where a test pins behaviour that is already correct, say so in the report rather than presenting it as RED evidence.
- **Deploy order:** `alembic upgrade head` → `npm run build:public` → `kill -HUP` API → roll web. The migration runs before any reload because `create_all()` runs on every API start.

## Review Focus

These are the inputs the spec implies but never names. Each line's test is added to the task that owns the code.

1. **`tools/call` naming a tool that does not exist** — a model working from a stale transcript will do this. Must come back as an MCP tool error with a readable message, not a 500. *(Task 4)*
2. **A malformed JSON-RPC body** — no `method`, a non-object `params`, a missing `id`. Must be a JSON-RPC error object, not an unhandled exception. *(Task 4)*
3. **A key revoked between two calls in one agent session** — the next call must 401, not ride a cached authorization. *(Task 2)*
4. **A `resume_id` that belongs to a guest session, not to any user** — an agent that gets one from anywhere must see 404, the same as another user's. *(Task 6)*
5. **An export token whose resume was deleted after the token was minted** — the signature still verifies; the row is gone. Must be 404, not a 500 from the compiler. *(Task 9)*

---

### Task 1: The credential — table, migration, service

**Files:**
- Modify: `app/models.py` (append `AgentApiKey` after `TalentApiKey`, ~line 1670)
- Create: `app/services/agent_keys.py`
- Create: `alembic/versions/043_agent_api_keys.py`
- Test: `tests/test_agent_keys.py`

**Interfaces:**
- Consumes: `app.models.Base`, `_uuid`, `_utcnow` from `app/models.py`.
- Produces:
  - `AgentApiKey` with columns `id, user_id, key_hash, label, created_at, last_used_at, revoked_at, expires_at`; `user_id` is `ForeignKey("users.id", ondelete="CASCADE"), index=True`; `key_hash` is `String(64), unique=True`.
  - `AGENT_KEY_PREFIX = "ofa_"`
  - `new_agent_key() -> str`
  - `hash_agent_key(raw: str) -> str`
  - `is_live(key: AgentApiKey, *, now: datetime | None = None) -> bool`
  - `mint_agent_key(db: Session, *, user: User, label: str, expires_in_days: int | None = None) -> tuple[AgentApiKey, str]`
  - `key_for_token(db: Session, raw: str) -> AgentApiKey | None`
  - `live_keys(db: Session, user: User) -> list[AgentApiKey]`
  - `MAX_LIVE_KEYS = 5`; `mint_agent_key` raises `ValueError("Too many keys")` at the limit.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_minted_key_is_live_and_found_by_its_token(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="laptop")
    db_session.commit()
    assert raw.startswith("ofa_")
    assert is_live(key)
    assert key_for_token(db_session, raw).id == key.id

def test_the_raw_token_is_not_stored(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="laptop")
    assert raw not in (key.key_hash or "")
    assert key.key_hash == hash_agent_key(raw)

def test_a_revoked_key_is_not_found(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="laptop")
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert key_for_token(db_session, raw) is None

def test_an_expired_key_is_not_found(db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="x", expires_in_days=1)
    key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert key_for_token(db_session, raw) is None

def test_a_token_without_the_prefix_never_reaches_the_database(db_session, a_user):
    mint_agent_key(db_session, user=a_user, label="laptop")
    db_session.commit()
    assert key_for_token(db_session, "ofk_whatever") is None
    assert key_for_token(db_session, "nonsense") is None

def test_a_talent_token_is_not_an_agent_token(db_session, a_user):
    _, talent_raw = mint_talent_key(
        db_session, label="Acme", contact="a@b.example", created_by="ops@example.com"
    )
    db_session.commit()
    assert key_for_token(db_session, talent_raw) is None

def test_the_sixth_live_key_is_refused(db_session, a_user):
    for n in range(MAX_LIVE_KEYS):
        mint_agent_key(db_session, user=a_user, label=f"key-{n}")
    db_session.commit()
    with pytest.raises(ValueError):
        mint_agent_key(db_session, user=a_user, label="one too many")

def test_revoking_frees_a_slot(db_session, a_user):
    keys = [mint_agent_key(db_session, user=a_user, label=f"k{n}")[0]
            for n in range(MAX_LIVE_KEYS)]
    keys[0].revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert mint_agent_key(db_session, user=a_user, label="replacement")
```

**Shared fixtures — add all of these to `tests/conftest.py` in this task.** Every later task uses them, so they are defined once, here. Follow how `tests/test_talent_api.py` and `tests/test_jobs_api.py` build these objects inline today.

- `a_user` — a committed `User` with `google_sub` and `email`.
- `other_user` — a second committed `User`, for every cross-user test.
- `signed_in` — sets `SESSION_COOKIE` on the `client` to `_sign(a_user.id, …)` and yields `a_user`; used by the cookie-authenticated routes in Task 11.
- `my_resume` — a committed `Resume` owned by `a_user`, with a `typst_source` the real compiler accepts (reuse `generate_starter("zh-TW")`).
- `a_job`, `another_job` — committed `Job` rows with their `JobFacets`.
- `_resume_for(db, user, title="Untitled") -> Resume` — a helper, not a fixture.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_agent_keys.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.agent_keys`

- [ ] **Step 3: Add the `AgentApiKey` model to `app/models.py`**

Follow `TalentApiKey` (line 1630) for column style. Its docstring must say what is now true: this credential **is** self-serve, it is per-user, and the operator-issued `TalentApiKey` is a different door — and must not repeat the old "no self-serve API keys" claim.

- [ ] **Step 4: Implement `app/services/agent_keys.py`**

Mirror `app/services/talent_keys.py` function for function. Carry over its two load-bearing comments in this module's own words: why the hash is unsalted SHA-256 and must not become bcrypt, and why this does not share `hash_talent_key`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_agent_keys.py -v`
Expected: PASS

- [ ] **Step 6: Write `alembic/versions/043_agent_api_keys.py`**

`down_revision = "042"` — confirm the exact revision identifier inside `042_job_scores.py` rather than assuming it is the filename prefix. Plain `create_table`, no dialect guard, following `042_job_scores.py`. The docstring explains the CASCADE on `user_id` (a token is personal data erased with the account) and states that no test runs alembic, so this file is checked by eye against the model and against `alembic upgrade head --sql`.

- [ ] **Step 7: Verify the migration matches the model**

Run: `cd apps/backend && .venv/bin/alembic upgrade head --sql | tail -40`
Expected: a `CREATE TABLE agent_api_keys` whose columns, nullability and foreign key match `AgentApiKey`.

- [ ] **Step 8: Commit**

```bash
git add apps/backend/app/models.py apps/backend/app/services/agent_keys.py \
  apps/backend/alembic/versions/043_agent_api_keys.py \
  apps/backend/tests/test_agent_keys.py apps/backend/tests/conftest.py
git commit -m "feat(agent): per-user API keys for the MCP surface"
```

Name every path. `git add` on a shared file sweeps in another session's work in this checkout.

---

### Task 2: The door — `require_agent_caller`

**Files:**
- Modify: `app/deps.py` (append after `require_talent_caller`, ~line 196)
- Test: `tests/test_mcp_transport.py` (auth cases only; the transport cases arrive in Task 4)

**Interfaces:**
- Consumes: `agent_keys.key_for_token`, `app.db.get_db`.
- Produces: `require_agent_caller(request: Request, db: Session = Depends(get_db)) -> tuple[User, AgentApiKey]`.

- [ ] **Step 1: Write the failing tests**

Point them at a throwaway probe route so this task does not depend on Task 4. Register it in the test module with `app.include_router` on a router that depends on `require_agent_caller` and returns the caller's email.

```python
def test_no_authorization_header_is_401(client):
    r = client.post(PROBE)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"

def test_a_session_cookie_is_401(client, a_user):
    client.cookies.set(SESSION_COOKIE, _sign(a_user.id, get_settings().auth_token_secret))
    assert client.post(PROBE).status_code == 401

def test_a_guest_cookie_is_401(client):
    client.cookies.set(GUEST_COOKIE, "whatever")
    assert client.post(PROBE).status_code == 401

def test_a_talent_token_is_401(client, db_session):
    _, raw = mint_talent_key(
        db_session, label="Acme", contact="a@b.example", created_by="ops@example.com"
    )
    db_session.commit()
    assert client.post(PROBE, headers=_bearer(raw)).status_code == 401

def test_every_refusal_says_the_same_thing(client, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    key, _ = mint_agent_key(db_session, user=a_user, label="dead")
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    bodies = {
        client.post(PROBE).json()["detail"],
        client.post(PROBE, headers=_bearer("ofa_invented")).json()["detail"],
        client.post(PROBE, headers={"Authorization": raw}).json()["detail"],
    }
    assert bodies == {"Credential required"}

def test_a_live_token_resolves_its_own_user(client, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    r = client.post(PROBE, headers=_bearer(raw))
    assert r.status_code == 200
    assert r.json()["email"] == a_user.email

def test_revoking_between_two_calls_closes_the_door(client, db_session, a_user):
    key, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    assert client.post(PROBE, headers=_bearer(raw)).status_code == 200
    key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    assert client.post(PROBE, headers=_bearer(raw)).status_code == 401
```

The last test is Review Focus 3.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_transport.py -v`
Expected: FAIL — `ImportError: cannot import name 'require_agent_caller'`

- [ ] **Step 3: Implement `require_agent_caller` in `app/deps.py`**

Copy the shape of `require_talent_caller`, including its docstring's reasoning about why there is no fallthrough and why every failure is identical — restated for this credential, which **is** self-serve. Return `(key.user, key)`; `user` comes off the relationship or a `db.get(User, key.user_id)`, and a key whose user has vanished is the same 401.

**Do not stamp `last_used_at` here.** Task 3 owns that, on its own session, because a stamp written in the request transaction is rolled back by any `HTTPException` the tool raises.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_transport.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/deps.py apps/backend/tests/test_mcp_transport.py
git commit -m "feat(agent): bearer-only dependency for the agent surface"
```

---

### Task 3: Audit and quota, on their own transaction

**Files:**
- Modify: `app/models.py` (append `AgentToolCall`)
- Modify: `alembic/versions/043_agent_api_keys.py` (add the second table)
- Create: `app/services/mcp_audit.py`
- Test: `tests/test_mcp_audit.py`

**Interfaces:**
- Consumes: `app.db.get_session_factory`, `AgentApiKey`.
- Produces:
  - `AgentToolCall` with `id, key_id, user_id, tool, resume_id, outcome, created_at`; `key_id` and `user_id` both `ondelete="CASCADE"`; `user_id` and `created_at` indexed, **plus a composite index on `(key_id, created_at)`** — that is the quota's own query and it runs on every tool call, so a `created_at`-only index would scan every key's recent rows and filter.
  - `outcome` is exactly `ok` | `refused` | `error` | `throttled`. **`throttled` is the quota's own refusal and is excluded from the quota count.** Without that exclusion the window never drains: a refused call writes a row, the row counts, and a client stuck in a loop stays locked out indefinitely — "300 per hour" becomes permanent. Excluding every `refused` row instead would let a client hammer invalid ids forever without ever being throttled, which is worse; only the throttle's own bookkeeping is exempt. The refusal is still recorded, it just does not feed the thing that caused it.
  - `created_at` carries a Python-side default, not a server default — the quota test constructs rows with an explicit `created_at`, and a server default would make every row look current. Follow `JobScore.created_at`.
  - `QUOTA_PER_HOUR = 300`
  - `record(key_id: str, user_id: str, tool: str, resume_id: str | None, outcome: str) -> None` — opens its own session, commits, swallows nothing.
  - `check_quota(key_id: str, *, now: datetime | None = None) -> None` — raises `HTTPException(429, "Too many calls")`.
  - `@dataclass class Call: resume_id: str | None = None` — the mutable handle `audited` yields.
  - `audited(key: AgentApiKey, tool: str) -> ContextManager[Call]` — checks quota on entry, stamps `last_used_at`, and on exit records `ok`, `refused` (an `HTTPException` escaped) or `error` (anything else), reading `resume_id` off the yielded `Call`. The caller assigns it: `with audited(key, name) as call: call.resume_id = args.get("resume_id")`. A handle rather than a parameter because the id is only known once the arguments have been read.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_successful_call_is_recorded(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with audited(key, "list_resumes"):
        pass
    rows = db_session.query(AgentToolCall).all()
    assert [(r.tool, r.outcome) for r in rows] == [("list_resumes", "ok")]

def test_a_refusal_is_still_recorded(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with pytest.raises(HTTPException):
        with audited(key, "get_resume"):
            raise HTTPException(status_code=404, detail="Resume not found")
    rows = db_session.query(AgentToolCall).all()
    assert [(r.tool, r.outcome) for r in rows] == [("get_resume", "refused")]

def test_the_record_survives_a_rolled_back_request_session(client, db_session, a_user):
    """Review Focus: get_db rolls back on any exception, so the audit must not
    ride the request's transaction."""
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    with pytest.raises(HTTPException):
        with audited(key, "edit_resume"):
            db_session.add(Resume(title="never committed", locale="en",
                                  source="create", typst_source="", user_id=a_user.id))
            raise HTTPException(status_code=409, detail="stale_hash")
    db_session.rollback()
    assert db_session.query(AgentToolCall).count() == 1
    assert db_session.query(Resume).filter(Resume.title == "never committed").count() == 0

def test_last_used_at_is_stamped(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    assert key.last_used_at is None
    with audited(key, "list_resumes"):
        pass
    db_session.refresh(key)
    assert key.last_used_at is not None

def test_the_quota_refuses_the_call_before_it_runs(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    for _ in range(QUOTA_PER_HOUR):
        with audited(key, "search_jobs"):
            pass
    ran = False
    with pytest.raises(HTTPException) as caught:
        with audited(key, "search_jobs"):
            ran = True
    assert caught.value.status_code == 429
    assert ran is False

def test_calls_older_than_an_hour_do_not_count(client, db_session, a_user):
    key, _ = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    for _ in range(QUOTA_PER_HOUR):
        db_session.add(AgentToolCall(key_id=key.id, user_id=a_user.id,
                                     tool="search_jobs", outcome="ok", created_at=stale))
    db_session.commit()
    with audited(key, "search_jobs"):
        pass  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.mcp_audit`

- [ ] **Step 3: Add `AgentToolCall` to `app/models.py` and extend revision 043**

Amend 043 rather than adding 044: 043 has not been applied anywhere, and `042_job_scores.py`'s own docstring sets the precedent for amending an unapplied revision. Say so in the docstring, and say that one revision carries both tables because a half-applied pair has no useful meaning.

- [ ] **Step 4: Implement `app/services/mcp_audit.py`**

`record` and the `last_used_at` stamp open a session from `get_session_factory()`, write, commit, close. The docstring must name the failure this avoids: a row written on the request session is rolled back by `get_db` on exactly the refusals and errors the user most wants to see, and a key producing only errors would never reach its quota.

`check_quota` counts `AgentToolCall` rows for this `key_id` newer than one hour, following `app/routers/talent.py::_check_budget` — refuse before doing anything, so a refusal writes nothing but its own audit row.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_audit.py -v`
Expected: PASS

- [ ] **Step 6: Verify the amended migration**

`alembic upgrade head --sql` fails offline on sqlite at revision 002, long before it reaches 043 — Task 1 established this. Render just this revision against a Postgres URL instead:

Run: `cd apps/backend && DATABASE_URL=postgresql://localhost/ignored .venv/bin/alembic upgrade 042_job_scores:043_agent_api_keys --sql`
Expected: `CREATE TABLE agent_api_keys` and `CREATE TABLE agent_tool_calls`, with columns, nullability, foreign keys and indexes matching the two models.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/models.py apps/backend/app/services/mcp_audit.py \
  apps/backend/alembic/versions/043_agent_api_keys.py \
  apps/backend/tests/test_mcp_audit.py
git commit -m "feat(agent): audit and hourly quota per key"
```

---

### Task 4: The transport — `POST /mcp`

**Files:**
- Create: `app/routers/mcp.py`
- Create: `app/services/mcp_tools.py` (registry and one tool: `list_resumes`)
- Modify: `app/main.py` (import and `include_router`)
- Test: `tests/test_mcp_transport.py` (extend)

**Interfaces:**
- Consumes: `require_agent_caller`, `mcp_audit.audited`.
- Produces:
  - `app/services/mcp_tools.py`: `REGISTRY: dict[str, Tool]` where `Tool` carries `spec: dict` (provider-neutral: `{name, description, parameters}`) and `handler: Callable`. A handler may be `def` or `async def` and takes `(db, user, args) -> dict`.
  - `specs() -> list[dict]` and `call(db, user, name, args) -> dict`; `call` raises `HTTPException(400, ...)` for an unknown name.
  - `PROTOCOL_VERSION` and `SERVER_INFO` constants.
  - `app/routers/mcp.py`: `router` with `prefix=""`, `include_in_schema=False`, `dependencies=[Depends(require_agent_caller)]`, one route `POST /mcp`.

- [ ] **Step 1: Read the MCP specification for the wire format**

Before writing anything: confirm the current protocol version string, the fields `initialize` must return, the shape of a `tools/list` entry (`name`, `description`, `inputSchema`), the shape of `tools/call` content blocks, and the JSON-RPC error codes. The plan fixes the structure; the spec fixes the bytes. Note the version string you used in the module docstring.

- [ ] **Step 2: Write the failing tests**

```python
def test_initialize_returns_the_protocol_version_and_server_info(client, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    r = client.post(MCP, headers=_bearer(raw),
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert body["result"]["serverInfo"]["name"]

def test_tools_list_comes_from_the_registry(client, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    r = client.post(MCP, headers=_bearer(raw),
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    listed = {t["name"] for t in r.json()["result"]["tools"]}
    assert listed == {spec["name"] for spec in specs()}

def test_a_tool_call_runs_the_handler(client, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.add(Resume(title="Mine", locale="zh-TW", source="create",
                          typst_source="= Mine", user_id=a_user.id))
    db_session.commit()
    r = client.post(MCP, headers=_bearer(raw), json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "list_resumes", "arguments": {}},
    })
    assert r.status_code == 200
    assert "Mine" in json.dumps(r.json()["result"])

def test_an_unknown_tool_is_a_tool_error_not_a_crash(client, db_session, a_user):
    """Review Focus 1."""
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    r = client.post(MCP, headers=_bearer(raw), json={
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "drop_database", "arguments": {}},
    })
    assert r.status_code == 200
    assert r.json()["result"]["isError"] is True

def test_a_malformed_body_is_a_jsonrpc_error(client, db_session, a_user):
    """Review Focus 2."""
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    for body in ({"jsonrpc": "2.0", "id": 5}, {"jsonrpc": "2.0", "id": 6, "method": "nope"},
                 {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": []}):
        r = client.post(MCP, headers=_bearer(raw), json=body)
        assert r.status_code == 200, body
        assert "error" in r.json(), body

def test_the_endpoint_is_not_in_the_openapi_document(client):
    assert "/mcp" not in client.get("/openapi.json").json().get("paths", {})
```

The last test needs the app to expose `/openapi.json` under test; if `create_app()` passes no `openapi_url`, assert on `app.openapi()["paths"]` directly instead.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_transport.py -v`
Expected: FAIL — no `/mcp` route

- [ ] **Step 4: Implement the registry and `list_resumes` in `app/services/mcp_tools.py`**

`list_resumes` returns the caller's resumes only — filter on `Resume.user_id == user.id`. Never fall back to `guest_session_id`.

- [ ] **Step 5: Implement `app/routers/mcp.py` and register it in `app/main.py`**

One handler reads `method` and dispatches. Wrap each `tools/call` in `audited`, and set `call.resume_id = args.get("resume_id")` before dispatching so a refusal still records which resume it was about. A tool's `HTTPException` becomes an MCP tool error (`isError: true` plus the detail as text) so the agent can read "the document changed, reload it"; a malformed envelope becomes a JSON-RPC `error` object.

Respond with `application/json`. No SSE, no `GET /mcp`, no session id — say why in the module docstring, citing the pool-pinning note in `app/deps.py::resolve_user`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_transport.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/routers/mcp.py apps/backend/app/services/mcp_tools.py \
  apps/backend/app/main.py apps/backend/tests/test_mcp_transport.py
git commit -m "feat(agent): MCP streamable-HTTP endpoint with a tool registry"
```

---

### Task 4b: The modern era — `server/discover` and per-request versioning

**Added 2026-11-20, after Task 4.** The design deferred the wire format to the
specification, and the specification has moved an era past what the design
assumed. Runs before Task 5, while the transport is fresh. Tasks 5–10 register
tools in the same registry and are unaffected either way.

**Why this is not optional.** `modelcontextprotocol.io/specification/versioning`
says the current revision is **2026-07-28**, which is *stateless*: no
`initialize`, the protocol version travels as per-request metadata, and
`server/discover` replaces the handshake — "Servers **MUST** implement it."
Revisions through 2025-11-25 are "legacy". Task 4 built legacy. The
backward-compatibility matrix is explicit about what that costs:

- *Dual-era client, legacy server:* "Works … the modern request returns a `4xx`
  without a recognized modern error body, and the client falls back to
  `initialize`." But that probe is a **MAY** and the body inspection a
  **SHOULD** — it is other people's optional code.
- *Modern client, legacy server:* "**Fails.** The server may reject the request
  with an implementation-defined error, stay silent, or even process an
  era-ambiguous method under legacy semantics."

A feature whose entire purpose is "a user connects their assistant" cannot rest
on a client-side MAY. Support both eras on the one endpoint.

**Files:**
- Create: `app/services/mcp_protocol.py`
- Modify: `app/routers/mcp.py`
- Test: `tests/test_mcp_protocol.py` (new)

**Interfaces:**
- Produces:
  - `MODERN_VERSION = "2026-07-28"`, `LEGACY_VERSION = "2025-11-25"`,
    `SUPPORTED = (MODERN_VERSION, LEGACY_VERSION)`
  - `era_of(request, body) -> str` — `"modern"` or `"legacy"`, decided by the
    `MCP-Protocol-Version` header and `_meta["io.modelcontextprotocol/protocolVersion"]`;
    a body whose method is `initialize` with no version header is legacy.
  - `check_version(request, body) -> None` — raises the two errors below.
  - `discover_result() -> dict` — the `server/discover` payload.
- Keeps Task 4's `initialize`, `tools/list` and `tools/call` working unchanged
  for legacy callers. The registry, the audit wrapper and every tool are shared.

**Error codes, exactly:**
- `-32022` `"Unsupported protocol version"`, with `data.supported` (the list) and
  `data.requested`. **This must be a recognized modern error**, because that is
  the signal a dual-era client uses to stay modern instead of falling back.
- `HeaderMismatch` + HTTP 400 when the `MCP-Protocol-Version` header and the
  body's `_meta` version disagree — the spec makes that a MUST.

- [ ] **Step 1: Write the failing tests**

```python
def test_server_discover_names_both_supported_versions(client, db_session, a_user):
    out = _rpc(client, db_session, a_user, "server/discover", MODERN_VERSION)
    assert out["result"]["supportedVersions"] == list(SUPPORTED)
    assert out["result"]["capabilities"]["tools"]

def test_a_modern_tools_list_works_without_any_handshake(client, db_session, a_user):
    """The whole point of the modern era: no initialize, ever."""
    out = _rpc(client, db_session, a_user, "tools/list", MODERN_VERSION)
    assert {t["name"] for t in out["result"]["tools"]} == {s["name"] for s in specs()}

def test_the_legacy_handshake_still_works(client, db_session, a_user):
    """Task 4's path must not regress."""
    out = _rpc(client, db_session, a_user, "initialize", None)
    assert out["result"]["protocolVersion"] == LEGACY_VERSION

def test_an_unsupported_version_is_a_recognized_modern_error(client, db_session, a_user):
    out = _rpc(client, db_session, a_user, "tools/list", "1999-01-01")
    assert out["error"]["code"] == -32022
    assert out["error"]["data"]["supported"] == list(SUPPORTED)
    assert out["error"]["data"]["requested"] == "1999-01-01"

def test_a_header_body_version_mismatch_is_rejected(client, db_session, a_user):
    r = _raw_rpc(client, db_session, a_user, "tools/list",
                 header_version=MODERN_VERSION, body_version=LEGACY_VERSION)
    assert r.status_code == 400
    assert "HeaderMismatch" in json.dumps(r.json())

def test_a_modern_tool_call_is_audited_like_a_legacy_one(client, db_session, a_user):
    """One registry, one audit path, two eras."""
```

`_rpc(client, db, user, method, version)` mints a key and posts one request,
setting both the `MCP-Protocol-Version` header and the body's `_meta` version
when `version` is not `None`; `_raw_rpc` lets the two disagree.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_protocol.py -v`
Expected: FAIL — no `server/discover`, no version checking

- [ ] **Step 3: Implement `app/services/mcp_protocol.py`**

The module docstring carries the two matrix rows quoted above and the URLs, so
the next person does not have to re-derive why both eras exist.

- [ ] **Step 4: Wire it into `app/routers/mcp.py`**

Version check first, then dispatch. `server/discover` is modern-only;
`initialize` is legacy-only and, per the spec, an error returned to an
`initialize` request **SHOULD** name the supported versions. `tools/list` and
`tools/call` serve both eras off the same registry and the same `audited`
wrapper. Do not duplicate the dispatch per era.

- [ ] **Step 5: Run the tests, then the whole transport suite**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_protocol.py tests/test_mcp_transport.py -v`
Expected: PASS, with Task 4's tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/services/mcp_protocol.py apps/backend/app/routers/mcp.py \
  apps/backend/tests/test_mcp_protocol.py
git commit -m "feat(agent): serve both MCP protocol eras"
```

---

### Task 5: Job tools — reuse the surface that exists

**Files:**
- Modify: `app/services/jobs_tools.py` (extract the resume-independent parts)
- Modify: `app/services/mcp_tools.py` (register three tools)
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `jobs_tools._search_spec()`, `jobs_tools._READ_JOB`, `jobs_tools._row()`, `app.services.jobs.search.filtered_query`, `app.models.JobScore`.
- Produces:
  - In `jobs_tools.py`: `catalogue_specs() -> list[dict]` returning `[_search_spec(), _READ_JOB]`, and `catalogue_handlers(db) -> dict[str, Callable[[dict], str]]` with `search_jobs` and `read_job`. Both are resume-independent. `jobs_tool_specs(resume)` and `jobs_tool_handlers(db, resume, user)` keep their signatures and are rewritten to build on these.
  - In `mcp_tools.py`: tools `search_jobs`, `read_job`, `list_matches`.
  - `list_matches(db, user, args) -> dict` reads `JobScore` for `user.id` with `outcome == "scored"`, joined to `Job`, **restricted to active postings**, ordered by `fit_score` descending then `scored_at` descending, `limit` capped at `jobs_tools.SEARCH_LIMIT_MAX`. Rows carry `_row(job, facets)` plus `tier`, `fit_score`, `reason`, `gap`.

    The active filter matters: the `job_scores` cache outlives a posting's open window, so without it an agent shows the user a closed job as a match. Filtering rather than adding an `is_active` flag matches `GET /v1/jobs`'s own `active=True` default instead of inventing a second convention, and the user's question is "what can I apply to". Say so in the tool description.

  - **The MCP tool is named `read_job`, not `get_job`** — the name the shared spec in `jobs_tools.py` already uses. The plan originally said `get_job`, which would have forced a runtime name mapping and a description rewrite to make the spec-equality test pass; a test that only passes because a transform made it pass is worth nothing, and two descriptions defeat the whole point of reusing one spec.
  - **No tool description may name another tool.** The roster differs between the chat surface and MCP, so "takes a `job_id` from `search_jobs` or `read_my_job_profile`" is wrong on one of them — and a description is the only thing an agent reads. Word it without naming tools.
  - Three test helpers in `tests/test_mcp_tools.py`, used by Tasks 5 through 10:
    - `_raw_call(client, db, user, name, args) -> dict` — mints a key for `user`, posts one `tools/call`, returns the parsed JSON-RPC response.
    - `_call(...) -> str` — the same, asserting success and returning the text content.
    - `_is_tool_error(response: dict) -> bool` — `response["result"].get("isError") is True`.

Do not define a second `search_jobs` spec. `jobs_tools.py` already has one and `app/services/jobs/search.py:5` says `filtered_query` was extracted so that tool could call it.

- [ ] **Step 1: Write the failing tests**

```python
def test_search_jobs_finds_a_posting_by_keyword(client, db_session, a_user, a_job):
    assert a_job.title in _call(client, db_session, a_user, "search_jobs", {"q": a_job.title})

def test_read_job_returns_the_posting(client, db_session, a_user, a_job):
    assert a_job.company in _call(client, db_session, a_user, "read_job", {"job_id": a_job.id})

def test_read_job_on_an_unknown_id_is_a_tool_error(client, db_session, a_user):
    assert _is_tool_error(_raw_call(client, db_session, a_user, "read_job",
                                    {"job_id": "no-such-posting"}))

def test_the_mcp_job_specs_are_the_catalogue_specs(client):
    """One search_jobs in this codebase, not two."""
    listed = {s["name"]: s for s in specs()}
    for shared in catalogue_specs():
        assert listed[shared["name"]]["parameters"] == shared["parameters"]

def test_list_matches_returns_scored_rows_best_first(client, db_session, a_user, a_job,
                                                     another_job):
    db_session.add_all([_score(a_user, a_job, 0.4), _score(a_user, another_job, 0.9)])
    db_session.commit()
    rows = json.loads(_call(client, db_session, a_user, "list_matches", {}))["items"]
    assert [r["job_id"] for r in rows] == [another_job.id, a_job.id]

def test_list_matches_hides_refusals(client, db_session, a_user, a_job):
    db_session.add(_score(a_user, a_job, None, outcome="refused"))
    db_session.commit()
    assert json.loads(_call(client, db_session, a_user, "list_matches", {}))["items"] == []

def test_list_matches_never_shows_another_persons_scores(client, db_session, a_user,
                                                         other_user, a_job):
    db_session.add(_score(other_user, a_job, 0.9))
    db_session.commit()
    assert json.loads(_call(client, db_session, a_user, "list_matches", {}))["items"] == []
```

`_score(user, job, fit_score, outcome="scored")` is a helper in this module building a `JobScore` with the model's NOT NULL columns filled: `facts_input_hash`, `description_hash`, `model`, `scored_at` (see `app/models.py:1446`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py -v`
Expected: FAIL — unknown tool `search_jobs`

- [ ] **Step 3: Extract `catalogue_specs` and `catalogue_handlers` in `app/services/jobs_tools.py`**

Pure refactor: move the resume-independent bodies out, leave `jobs_tool_specs` / `jobs_tool_handlers` calling them. The module docstring gains a line saying the catalogue half is now shared with the MCP surface and must stay resume-independent.

- [ ] **Step 4: Run the existing job-tool tests to prove the refactor is behaviour-free**

Run: `cd apps/backend && .venv/bin/pytest tests/test_jobs_tools.py tests/test_chat_jobs.py -v`
Expected: PASS, unchanged

- [ ] **Step 5: Register the three tools in `app/services/mcp_tools.py`**

`search_jobs` and `read_job` delegate to `catalogue_handlers`. `list_matches` is new, user-scoped, reads only the cache — it must not call the sweep, the scorer, or `digest.mark_opened`, for the reason `jobs_tools.py`'s docstring already gives about forging an engagement signal.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py tests/test_jobs_tools.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/services/jobs_tools.py apps/backend/app/services/mcp_tools.py \
  apps/backend/tests/test_mcp_tools.py
git commit -m "feat(agent): job search, posting and scored matches as MCP tools"
```

---

### Task 6: Resume reads — service extraction, `get_resume`, ownership

**Files:**
- Create: `app/services/resume_access.py`
- Modify: `app/routers/resumes.py` (`list_resumes`, `_new_resume` call sites)
- Modify: `app/services/mcp_tools.py`
- Test: `tests/test_mcp_tools.py` (extend)

**Interfaces:**
- Consumes: `app.services.typst_projection.parse_projection`, `app.services.revisions.source_hash`.
- Produces:
  - `resume_access.resumes_of(db, user) -> list[Resume]`
  - `resume_access.owned_by_user(db, user, resume_id) -> Resume` — raises `HTTPException(404, "Resume not found")` for a missing row, another user's row, **and a guest-owned row**.
  - `resume_access.create_for_user(db, user, *, title: str, locale: str, source: str, typst_source: str) -> Resume` — the user-only half of today's `routers/resumes.py::_new_resume`, which stays in the router for its guest branch and calls this one for the signed-in branch. Always sets `user_id` and leaves `guest_session_id` NULL.
  - `mcp_tools` tools `get_resume` (returns `{resume_id, title, locale, kind, parent_resume_id, target_label, hash, projection}`) and the `list_resumes` from Task 4 rewritten onto `resumes_of`.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_resume_returns_the_projection_and_a_hash(client, db_session, a_user, my_resume):
    out = json.loads(_call(client, db_session, a_user, "get_resume",
                           {"resume_id": my_resume.id}))
    assert out["hash"] == source_hash(my_resume.typst_source)
    assert out["projection"]["units"]

def test_another_users_resume_is_not_found(client, db_session, a_user, other_user):
    theirs = _resume_for(db_session, other_user)
    assert _is_tool_error(_raw_call(client, db_session, a_user, "get_resume",
                                    {"resume_id": theirs.id}))

def test_a_guest_owned_resume_is_not_found(client, db_session, a_user):
    """Review Focus 4: an agent must not reach a resume that belongs to a
    guest session, even though no user owns it."""
    guest = GuestSession(key_hash=hash_guest_key("g"))
    db_session.add(guest)
    db_session.flush()
    orphan = Resume(title="Guest draft", locale="en", source="create",
                    typst_source="= x", guest_session_id=guest.id)
    db_session.add(orphan)
    db_session.commit()
    assert _is_tool_error(_raw_call(client, db_session, a_user, "get_resume",
                                    {"resume_id": orphan.id}))

def test_list_resumes_shows_only_my_own(client, db_session, a_user, other_user):
    mine = _resume_for(db_session, a_user, title="Mine")
    _resume_for(db_session, other_user, title="Theirs")
    listed = json.loads(_call(client, db_session, a_user, "list_resumes", {}))["items"]
    assert [r["title"] for r in listed] == ["Mine"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py -k resume -v`
Expected: FAIL — unknown tool `get_resume`

- [ ] **Step 3: Implement `app/services/resume_access.py`**

`owned_by_user` is deliberately **not** `deps.load_resume_for_owner`: that one accepts a guest and is right for the browser. The docstring says so, and says why 404 rather than 403 — `app/routers/talent.py::_not_found` settled that question for this codebase.

- [ ] **Step 4: Point the existing router at the new service**

`routers/resumes.py::list_resumes` calls `resumes_of` for its signed-in branch and keeps its guest branch. Behaviour unchanged.

- [ ] **Step 5: Register `get_resume` and rewrite `list_resumes` in `mcp_tools.py`**

- [ ] **Step 6: Run the resume router tests and the tool tests**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py tests/test_guest_resume.py tests/test_family.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/services/resume_access.py apps/backend/app/routers/resumes.py \
  apps/backend/app/services/mcp_tools.py apps/backend/tests/test_mcp_tools.py
git commit -m "feat(agent): resume reads with user-only ownership"
```

---

### Task 7: Resume writes — `edit_resume` and `put_resume_source`

**Files:**
- Create: `app/services/resume_write.py`
- Modify: `app/routers/resumes.py` (`edit` calls the new service)
- Modify: `app/services/mcp_tools.py`
- Test: `tests/test_resume_write.py`

**Interfaces:**
- Consumes: `apply_intent`, `parse_projection`, `source_hash`, `set_draft_source`, `compile_preview_pages`.
- Produces:
  - `async def write_source(db, resume, *, source: str, expected_hash: str) -> dict` — the guarded whole-document write.
  - `async def apply_edit(db, resume, intent: IntentIn) -> dict` — the body of today's `/edit` handler, moved.
  - `mcp_tools` tools `edit_resume` (args: `resume_id`, `hash`, plus the `IntentIn` fields) and `put_resume_source` (args: `resume_id`, `hash`, `typst_source`).

Both follow the order `/edit` already proved, in this sequence: compare hash → `db.commit()` to release the pool connection → compile on `asyncio.to_thread` → `db.refresh` → compare hash again → `set_draft_source(..., coalesce=False)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_good_source_lands_and_returns_the_new_hash(db_session, my_resume):
    out = await write_source(db_session, my_resume, source="= Hired",
                             expected_hash=source_hash(my_resume.typst_source))
    db_session.refresh(my_resume)
    assert my_resume.typst_source == "= Hired"
    assert out["hash"] == source_hash("= Hired")

async def test_a_stale_hash_is_409_and_writes_nothing(db_session, my_resume):
    before = my_resume.typst_source
    with pytest.raises(HTTPException) as caught:
        await write_source(db_session, my_resume, source="= Nope",
                           expected_hash="not-the-hash")
    assert caught.value.status_code == 409
    db_session.refresh(my_resume)
    assert my_resume.typst_source == before

async def test_a_source_typst_rejects_writes_nothing(db_session, my_resume):
    before = my_resume.typst_source
    with pytest.raises(HTTPException) as caught:
        await write_source(db_session, my_resume, source="#this-is-not-typst(",
                           expected_hash=source_hash(before))
    assert caught.value.status_code == 400
    db_session.refresh(my_resume)
    assert my_resume.typst_source == before

async def test_a_write_that_lands_during_the_compile_is_409(db_session, my_resume,
                                                            monkeypatch):
    """The second hash check. A chat reply or a second tab lands while the
    compile runs, which is why the connection was released."""
    expected = source_hash(my_resume.typst_source)

    def _competing_compile(source, with_bands=False):
        set_draft_source(db_session, my_resume, "= Someone else got here first")
        db_session.commit()
        return {"pages": []}

    monkeypatch.setattr(resume_write, "_preview_pages", _competing_compile)
    with pytest.raises(HTTPException) as caught:
        await write_source(db_session, my_resume, source="= Mine",
                           expected_hash=expected)
    assert caught.value.status_code == 409
    db_session.refresh(my_resume)
    assert my_resume.typst_source == "= Someone else got here first"

async def test_an_empty_source_is_refused_not_stored(db_session, my_resume):
    before = my_resume.typst_source
    with pytest.raises(HTTPException):
        await write_source(db_session, my_resume, source="",
                           expected_hash=source_hash(before))
    db_session.refresh(my_resume)
    assert my_resume.typst_source == before

async def test_every_write_makes_its_own_revision(db_session, my_resume):
    """coalesce=False: two writes inside the coalescing window must not merge,
    or an agent edit would rewrite a chat reply's revision."""
    before = db_session.query(ResumeRevision).filter(
        ResumeRevision.resume_id == my_resume.id).count()
    for text in ("= One", "= Two"):
        await write_source(db_session, my_resume, source=text,
                           expected_hash=source_hash(my_resume.typst_source))
        db_session.refresh(my_resume)
    after = db_session.query(ResumeRevision).filter(
        ResumeRevision.resume_id == my_resume.id).count()
    assert after == before + 2
```

Mark the coroutine tests the way the suite already runs async cases — check how `tests/test_edit_api.py` drives the `async def edit` handler and follow it rather than introducing a new async plugin.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_resume_write.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.resume_write`

- [ ] **Step 3: Implement `app/services/resume_write.py`**

Move the body of `routers/resumes.py::edit` (lines 815-884) in, unchanged in behaviour, and add `write_source` beside it with the same five-step order. Carry over the three comments that explain why the order is what it is — the pool release, the re-read, and `coalesce=False` — because each one is a bug someone already paid for.

The docstring states that `PUT /v1/resumes/{resume_id}` deliberately keeps its unguarded behaviour for the editor's source tab, and that the agent path goes through here instead.

- [ ] **Step 4: Point `routers/resumes.py::edit` at `apply_edit`**

- [ ] **Step 5: Run the existing edit tests to prove the move is behaviour-free**

Run: `cd apps/backend && .venv/bin/pytest tests/test_edit_api.py tests/test_revisions.py -v`
Expected: PASS, unchanged

- [ ] **Step 6: Register `edit_resume` and `put_resume_source` in `mcp_tools.py`**

Both resolve the resume through `resume_access.owned_by_user` first. `put_resume_source`'s description says it replaces the whole document and that `get_resume` must be called first for the hash.

- [ ] **Step 7: Run the whole backend suite**

Run: `cd apps/backend && .venv/bin/pytest -q`
Expected: PASS except the two failures this checkout is known to have — confirm they are the same two and not new ones.

- [ ] **Step 8: Commit**

```bash
git add apps/backend/app/services/resume_write.py apps/backend/app/routers/resumes.py \
  apps/backend/app/services/mcp_tools.py apps/backend/tests/test_resume_write.py
git commit -m "feat(agent): guarded whole-document and field writes"
```

---

### Task 8: Resume lifecycle — create, fork, rename

**Files:**
- Modify: `app/services/mcp_tools.py`
- Test: `tests/test_mcp_tools.py` (extend)

**Interfaces:**
- Consumes: `resume_access.create_for_user` (Task 6), `starter.generate_starter`, `starter.default_title`, `family.create_tailor`, `routers.resumes._validate_locale`.
- Produces: tools `create_resume` (args: `title?`, `locale?`), `create_tailor` (args: `resume_id`, `title`, `target_label?`), `set_resume_title` (args: `resume_id`, `title`).

- [ ] **Step 1: Write the failing tests**

```python
def test_create_resume_starts_from_the_starter_and_belongs_to_me(client, db_session, a_user):
    out = json.loads(_call(client, db_session, a_user, "create_resume", {"locale": "zh-TW"}))
    made = db_session.get(Resume, out["resume_id"])
    assert made.user_id == a_user.id
    assert made.guest_session_id is None
    assert made.typst_source == generate_starter("zh-TW")

def test_create_resume_rejects_an_unknown_locale(client, db_session, a_user):
    assert _is_tool_error(_raw_call(client, db_session, a_user, "create_resume",
                                    {"locale": "klingon"}))

def test_create_tailor_forks_without_calling_a_model(client, db_session, a_user, my_resume):
    out = json.loads(_call(client, db_session, a_user, "create_tailor",
                           {"resume_id": my_resume.id, "title": "For Acme"}))
    tailor = db_session.get(Resume, out["resume_id"])
    assert tailor.parent_resume_id == my_resume.id
    assert tailor.typst_source == my_resume.typst_source

def test_create_tailor_on_someone_elses_resume_is_not_found(client, db_session, a_user,
                                                             other_user):
    theirs = _resume_for(db_session, other_user)
    assert _is_tool_error(_raw_call(client, db_session, a_user, "create_tailor",
                                    {"resume_id": theirs.id, "title": "x"}))

def test_set_resume_title_rejects_blank_and_overlong(client, db_session, a_user, my_resume):
    for bad in ("", "   ", "x" * 256):
        assert _is_tool_error(_raw_call(client, db_session, a_user, "set_resume_title",
                                        {"resume_id": my_resume.id, "title": bad}))

def test_no_tool_can_delete_a_resume(client, db_session, a_user):
    assert not [name for name in (s["name"] for s in specs()) if "delete" in name]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py -k "create or title or delete" -v`
Expected: FAIL — unknown tools

- [ ] **Step 3: Register the three tools in `app/services/mcp_tools.py`**

`create_resume` validates the locale through the same helper `routers/resumes.py::_validate_locale` uses, and always sets `user_id` — never `guest_session_id`. `create_tailor` wraps `family.create_tailor`, turning its `ValueError` into a 400. `set_resume_title` applies only the title; **no template argument** — switching templates is out of v1 because it either spends the user's model allowance or silently no-ops.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/mcp_tools.py apps/backend/tests/test_mcp_tools.py
git commit -m "feat(agent): create, fork and rename resumes"
```

---

### Task 9: Publish, unpublish, export

**Files:**
- Create: `app/services/sharing.py`
- Modify: `app/routers/shares.py` (`put_share` calls the new service)
- Modify: `app/services/mcp_tools.py`
- Modify: `app/routers/resumes.py` (add the signed export route)
- Test: `tests/test_agent_export.py`, `tests/test_mcp_tools.py` (extend)

**Interfaces:**
- Consumes: `deps._sign` / `deps._unsign`, `revisions.pin_current_draft`, `revisions.build_share_state`, `typst_compile.compile_typst`.
- Produces:
  - `sharing.set_public(db, resume, public: bool) -> ShareState` — the body of today's `routers/shares.py::put_share` with its `Request`/`Depends` plumbing removed: creates or deletes the `ResumeShare` row, pins the current draft when turning public with nothing pinned, and returns the share state. `routers/shares.py::put_share` becomes a thin caller.
  - `sharing.new_share_token(db) -> str` — today's `shares._new_token`, moved.
  - tools `publish_resume`, `unpublish_resume`, `export_resume`.
  - `EXPORT_TTL_SECONDS = 600`
  - `GET /v1/resumes/export/{token}` — public, `include_in_schema=False`.

**`publish_resume` goes through the share door, not `POST .../publish`.** `publish_resume` in the router only calls `pin_current_draft`, which sets `published_revision_id` and creates no link at all; the link is the `ResumeShare` row that `PUT .../share` writes. The tool therefore goes through `sharing.set_public`, which creates the share row and pins if nothing is pinned.

**`unpublish_resume` deletes the share row and leaves `published_revision_id` alone,** matching the website exactly. It therefore does not close the talent profile — `profile.readable_profiles_query` gates on `published_revision_id`, so a published resume keeps that gate open. This is deliberate and was decided by the user; the tool description must say it, and so must the settings page.

- [ ] **Step 1: Write the failing tests**

```python
def test_publish_creates_a_working_public_link(client, db_session, a_user, my_resume):
    out = json.loads(_call(client, db_session, a_user, "publish_resume",
                           {"resume_id": my_resume.id}))
    assert out["public"] is True and out["token"]
    assert client.get(f"/v1/shares/{out['token']}").status_code == 200

def test_publish_also_pins_a_revision(client, db_session, a_user, my_resume):
    _call(client, db_session, a_user, "publish_resume", {"resume_id": my_resume.id})
    db_session.refresh(my_resume)
    assert my_resume.published_revision_id is not None

def test_unpublish_closes_the_link_but_not_the_talent_gate(client, db_session, a_user,
                                                            my_resume):
    """The user chose this. Pinning stays, so a consenting profile stays readable."""
    pub = json.loads(_call(client, db_session, a_user, "publish_resume",
                           {"resume_id": my_resume.id}))
    _call(client, db_session, a_user, "unpublish_resume", {"resume_id": my_resume.id})
    assert client.get(f"/v1/shares/{pub['token']}").status_code == 404
    db_session.refresh(my_resume)
    assert my_resume.published_revision_id is not None

def test_publish_is_recorded_in_the_audit(client, db_session, a_user, my_resume):
    _call(client, db_session, a_user, "publish_resume", {"resume_id": my_resume.id})
    row = db_session.query(AgentToolCall).filter(
        AgentToolCall.tool == "publish_resume").one()
    assert row.resume_id == my_resume.id and row.outcome == "ok"

def test_export_returns_a_link_that_serves_a_pdf(client, db_session, a_user, my_resume):
    out = json.loads(_call(client, db_session, a_user, "export_resume",
                           {"resume_id": my_resume.id}))
    served = client.get(out["url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"

def test_an_expired_export_token_is_refused(client, db_session, a_user, my_resume):
    token = _sign(f"{my_resume.id}|{int(time.time()) - 1}", get_settings().auth_token_secret)
    assert client.get(f"/v1/resumes/export/{token}").status_code in (403, 404)

def test_a_tampered_export_token_is_refused(client, db_session, a_user, my_resume):
    assert client.get(f"/v1/resumes/export/{my_resume.id}|9999999999.bogus").status_code \
        in (403, 404)

def test_an_export_token_for_a_deleted_resume_is_404(client, db_session, a_user, my_resume):
    """Review Focus 5."""
    out = json.loads(_call(client, db_session, a_user, "export_resume",
                           {"resume_id": my_resume.id}))
    db_session.delete(my_resume)
    db_session.commit()
    assert client.get(out["url"]).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_agent_export.py -v`
Expected: FAIL — unknown tool `export_resume`

- [ ] **Step 3: Extract `app/services/sharing.py` and point `routers/shares.py::put_share` at it**

Pure refactor. Run `pytest tests/test_share.py -v` afterwards and expect it unchanged — the browser's sharing behaviour must not move.

- [ ] **Step 4: Add `GET /v1/resumes/export/{token}` to `app/routers/resumes.py`**

Verify the signature, then the expiry, then load the resume — a missing row is 404 before anything is compiled. A comment says why this is not a `ResumeShare` token: that one is a state the user chose to be in, with a table and a revoke and an OG image; this is a one-shot pipe.

- [ ] **Step 5: Register the three tools in `app/services/mcp_tools.py`**

`publish_resume` calls `sharing.set_public(db, resume, True)`; `unpublish_resume` calls it with `False`.

`publish_resume`'s description states both consequences in plain words: it creates a link anyone can open, and if the user has "open to matching" on with their profile pointed at this resume, it re-opens that profile to credentialed recruiters. `unpublish_resume`'s description states that it does not undo the second one.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_agent_export.py tests/test_mcp_tools.py tests/test_share.py tests/test_talent_api.py -v`
Expected: PASS

`test_talent_api.py` is in this list on purpose: it is the suite that would notice if the sharing extraction changed when a profile becomes readable.

- [ ] **Step 7: Commit**

```bash
git add apps/backend/app/services/sharing.py apps/backend/app/routers/shares.py \
  apps/backend/app/services/mcp_tools.py apps/backend/app/routers/resumes.py \
  apps/backend/tests/test_agent_export.py apps/backend/tests/test_mcp_tools.py
git commit -m "feat(agent): publish, unpublish and signed PDF export"
```

---

### Task 10: Job preference tools

**Files:**
- Create: `app/services/preferences_write.py`
- Modify: `app/routers/preferences.py` (`put_job_preferences` calls it)
- Modify: `app/services/mcp_tools.py`
- Test: `tests/test_mcp_tools.py` (extend)

**Interfaces:**
- Consumes: `app.services.jobs.preferences.current_or_seeded`, `JobPreferenceIn`.
- Produces:
  - `preferences_write.save(db, user, body: JobPreferenceIn) -> tuple[JobPreference, bool]` — the row and whether this was the first save. Mutates the loaded row in place so `onupdate` fires; the existing handler's comment explains why delete-and-reinsert is wrong.
  - tools `get_job_preferences`, `set_job_preferences`.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_returns_the_seed_before_the_first_save(client, db_session, a_user):
    out = json.loads(_call(client, db_session, a_user, "get_job_preferences", {}))
    assert "target_functions" in out
    assert db_session.query(JobPreference).count() == 0  # reading never stores

def test_set_then_get_round_trips(client, db_session, a_user):
    _call(client, db_session, a_user, "set_job_preferences",
          {"target_functions": ["backend"], "target_locations": ["Taipei"],
           "remote_required": True})
    out = json.loads(_call(client, db_session, a_user, "get_job_preferences", {}))
    assert out["target_functions"] == ["backend"] and out["remote_required"] is True

def test_set_replaces_wholesale_rather_than_patching(client, db_session, a_user):
    """An omitted field is cleared. The tool description must say so."""
    _call(client, db_session, a_user, "set_job_preferences",
          {"target_functions": ["backend"], "target_locations": ["Taipei"]})
    _call(client, db_session, a_user, "set_job_preferences",
          {"target_functions": ["frontend"]})
    out = json.loads(_call(client, db_session, a_user, "get_job_preferences", {}))
    assert out["target_locations"] == []

def test_preferences_are_per_user(client, db_session, a_user, other_user):
    _call(client, db_session, a_user, "set_job_preferences",
          {"target_functions": ["backend"]})
    out = json.loads(_call(client, db_session, other_user, "get_job_preferences", {}))
    assert out["target_functions"] != ["backend"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py -k preference -v`
Expected: FAIL — unknown tool

- [ ] **Step 3: Implement `app/services/preferences_write.py` and point the router at it**

- [ ] **Step 4: Register both tools in `app/services/mcp_tools.py`**

`set_job_preferences`'s description says in its first sentence that it replaces every field and clears any field not supplied.

- [ ] **Step 5: Run the tests**

Run: `cd apps/backend && .venv/bin/pytest tests/test_mcp_tools.py tests/test_job_preferences_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/services/preferences_write.py apps/backend/app/routers/preferences.py \
  apps/backend/app/services/mcp_tools.py apps/backend/tests/test_mcp_tools.py
git commit -m "feat(agent): read and replace job preferences"
```

---

### Task 11: Key management — HTTP routes and the settings UI

**Files:**
- Modify: `app/routers/account.py`
- Modify: `app/schemas.py`
- Create: `apps/frontend/components/settings/AgentKeys.tsx`
- Modify: `apps/frontend/app/[locale]/settings/page.tsx`
- Modify: `apps/frontend/lib/api.ts`
- Modify: `apps/frontend/messages/{en,zh-TW,zh-CN}.json`
- Test: `tests/test_account.py` (extend)

**Interfaces:**
- Produces:
  - `GET /v1/me/agent-keys` → `list[AgentKeyOut]` (`id`, `label`, `created_at`, `last_used_at`)
  - `POST /v1/me/agent-keys` → `AgentKeyCreated` (the above plus `token`, returned exactly once)
  - `DELETE /v1/me/agent-keys/{key_id}` → 204
  - `GET /v1/me/agent-keys/{key_id}/calls` → `list[AgentToolCallOut]`
  - `lib/api.ts`: `listAgentKeys()`, `createAgentKey(label)`, `revokeAgentKey(id)`, `listAgentKeyCalls(id)`

All four routes take `Depends(require_user)` — **cookie auth, not `require_agent_caller`.** An agent must not be able to mint itself more tokens.

- [ ] **Step 1: Write the failing tests**

```python
def test_minting_returns_the_token_once(client, signed_in):
    made = client.post("/v1/me/agent-keys", json={"label": "laptop"}).json()
    assert made["token"].startswith("ofa_")
    listed = client.get("/v1/me/agent-keys").json()
    assert "token" not in listed[0]

def test_an_agent_token_cannot_mint_another(client, db_session, a_user):
    _, raw = mint_agent_key(db_session, user=a_user, label="k")
    db_session.commit()
    assert client.post("/v1/me/agent-keys", json={"label": "second"},
                       headers=_bearer(raw)).status_code == 401

def test_i_cannot_revoke_someone_elses_key(client, db_session, signed_in, other_user):
    theirs, _ = mint_agent_key(db_session, user=other_user, label="theirs")
    db_session.commit()
    assert client.delete(f"/v1/me/agent-keys/{theirs.id}").status_code == 404

def test_the_sixth_key_is_a_400(client, signed_in):
    for n in range(5):
        client.post("/v1/me/agent-keys", json={"label": f"k{n}"})
    assert client.post("/v1/me/agent-keys", json={"label": "sixth"}).status_code == 400

def test_a_blank_or_overlong_label_is_a_400(client, signed_in):
    """`agent_keys.mint_agent_key` stores whatever it is given — Task 1's review
    confirmed it does not validate. The route is where that is caught."""
    for bad in ("", "   ", "x" * 256):
        assert client.post("/v1/me/agent-keys", json={"label": bad}).status_code == 400

def test_a_non_positive_expiry_is_a_400(client, signed_in):
    """Task 1's review: `expires_in_days=0` silently means "never expires" and a
    negative value mints an already-dead key. Neither may reach the service."""
    for bad in (0, -1):
        assert client.post("/v1/me/agent-keys",
                           json={"label": "k", "expires_in_days": bad}).status_code == 400

def test_the_call_log_is_mine_only(client, db_session, signed_in, other_user):
    theirs, _ = mint_agent_key(db_session, user=other_user, label="theirs")
    db_session.commit()
    assert client.get(f"/v1/me/agent-keys/{theirs.id}/calls").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/backend && .venv/bin/pytest tests/test_account.py -k agent -v`
Expected: FAIL — 404 on an unregistered route

- [ ] **Step 3: Add the four routes to `app/routers/account.py` and the schemas**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/backend && .venv/bin/pytest tests/test_account.py -v`
Expected: PASS

- [ ] **Step 5: Build `components/settings/AgentKeys.tsx` and wire it into the settings page**

The section holds: the live keys with label, created, last used and a revoke button; a "generate" flow whose result is shown once with a copy button and a warning that it will not be shown again; a per-key "recent actions" disclosure reading the call log; and the connection instructions, including that the header value is `Authorization: Bearer ofa_…` with a space after `Bearer`.

When the user has "open to matching" on, the section carries one line saying that an agent publishing a resume also re-opens their talent profile to credentialed recruiters.

Design and check it in **zh-TW**. CJK labels run about 1.5x wider and read heavier, so fixed widths wrap and filled buttons crowd. Add all three locale files.

- [ ] **Step 6: Typecheck and build**

Run: `cd apps/frontend && npx tsc --noEmit`
Expected: no errors

Then `npm run build:public` and confirm it exits 0 **and** that `.next/BUILD_ID` exists — a typecheck failure still wipes it.

- [ ] **Step 7: Verify it in a browser**

Headless Chrome over CDP against a stub API, following the project's usual approach. Confirm the generated token appears once, that reloading does not show it again, and that the zh-TW layout does not wrap.

- [ ] **Step 8: Commit**

```bash
git add apps/backend/app/routers/account.py apps/backend/app/schemas.py \
  apps/backend/tests/test_account.py \
  apps/frontend/components/settings/AgentKeys.tsx \
  "apps/frontend/app/[locale]/settings/page.tsx" apps/frontend/lib/api.ts \
  apps/frontend/messages/en.json apps/frontend/messages/zh-TW.json \
  apps/frontend/messages/zh-CN.json
git commit -m "feat(agent): mint and revoke agent keys from settings"
```

---

### Task 12: Make the published claims true again

**Files:**
- Modify: `apps/frontend/lib/agent-markdown.ts` (lines 106, 107, 175)
- Modify: `apps/frontend/lib/agent-markdown.test.ts` (line 42)
- Modify: `apps/backend/app/deps.py` (~line 166), `app/models.py` (~line 1635), `app/services/talent_keys.py` (line 4)

This task exists because the work above makes live text false. `llms.txt` and `/en/contact` are served from `agent-markdown.ts`, and a test pins the sentence on purpose.

- [ ] **Step 1: Update the test to assert the new claim**

```typescript
assert.match(body, /self-serve MCP server/i);
assert.match(body, /no machine OAuth authorization server/i);
assert.doesNotMatch(body, /no public agent API/i);
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/frontend && node --test lib/agent-markdown.test.ts`
Expected: FAIL — the old sentence is still there

- [ ] **Step 3: Rewrite the three strings in `lib/agent-markdown.ts`**

Line 106 becomes a sentence saying there is a self-serve MCP server authenticated by a user-generated token, and that there is still no machine OAuth authorization server. **Line 107 stays** — Google sign-in really is still for humans only. Line 175's "no self-serve API key desk" points at the settings page instead.

Add a short "how to connect" block to `llmsTxt()`: the endpoint, that the credential is a token the user generates in settings, and that the tools act only on that user's own data and never submit applications.

- [ ] **Step 4: Run the frontend test to verify it passes**

Run: `cd apps/frontend && node --test lib/agent-markdown.test.ts`
Expected: PASS

- [ ] **Step 5: Rewrite the three backend docstrings**

`deps.py::require_talent_caller`, `models.py::TalentApiKey` and `talent_keys.py`'s module docstring each claim all three statements "must stay true". Rewrite them to say what is now true: the **talent** credential is still operator-issued only and no route mints one but the admin endpoint; the self-serve agent credential is a different table, a different prefix and a different door. Do not delete the reasoning about why talent has no self-serve path — it is still the rule for that credential.

- [ ] **Step 6: Run both suites**

Run: `cd apps/backend && .venv/bin/pytest -q` and `cd apps/frontend && npx tsc --noEmit`
Expected: PASS (backend: the same two known failures, no new ones)

- [ ] **Step 7: Commit**

```bash
git add apps/frontend/lib/agent-markdown.ts apps/frontend/lib/agent-markdown.test.ts \
  apps/backend/app/deps.py apps/backend/app/models.py \
  apps/backend/app/services/talent_keys.py
git commit -m "docs(agent): the no-agent-API claim is no longer true"
```

---

## Deploy

Not a task — the release step, run once the twelve tasks are merged to `main`.

1. `cd apps/backend && .venv/bin/alembic upgrade head` — **before any reload.** `create_all()` runs on every API start, so a HUP from a tree carrying the new models would create these tables in prod with no migration recorded.
2. `cd apps/frontend && cp -al .next .next.bak-$(date +%Y%m%d-%H%M%S)`
3. `npm run build:public` — never a bare `next build`. Confirm `.next/BUILD_ID` exists afterwards.
4. `kill -HUP` the API master. **API first**, because this release adds routes and the old frontend never calls them. Verify by the listener with `ss -ltnpe`, not by worker count.
5. `ops/rolling-restart-web.sh`
6. Delete the ISR copy of `/en` and roll twice — the old process revalidates from its stale in-memory entry otherwise.
7. Smoke: `curl -s localhost:8001/mcp` with a real token (no `/api` prefix on `:8001`), then the settings page in a browser.
