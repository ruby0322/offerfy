# Resume Import Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make resume import legible while it runs and survivable across a refresh, by having the model declare its own section plan and edit one section at a time, with progress in Postgres and a card pinned to the bottom of the chat turn.

**Architecture:** An import is a row in `resume_imports` plus ordered `resume_import_steps`. The model declares its plan through an import-only tool and names the step each `apply_typst_edit` fulfils, so no section list exists in our code. Every write bumps the run's `version`; an SSE endpoint polls and emits complete snapshots, because `offerfy-api` runs two uvicorn workers and a reconnecting client can land on either.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Postgres, SSE (`StreamingResponse`); Next.js, React, next-intl; `pytest` backend, `node:test` + `tsx` frontend.

**Spec:** `docs/superpowers/specs/2026-11-04-resume-import-progress-design.md`

## Global Constraints

- **Toolchain lives only in the main checkout** `/home/ruby0322/offerfy`: `apps/backend/.venv` and `apps/frontend/node_modules`. A worktree has neither — run tests from the main checkout.
- Backend tests: `cd apps/backend && .venv/bin/python -m pytest tests/<file> -q`
- Frontend tests: `cd apps/frontend && npx tsx --test <file>`
- **Frontend tests use `node:test` + `node:assert/strict` only.** There is no vitest, jest, jsdom or React Testing Library in this repo. React components are therefore not unit-testable; only pure modules are. Do not add a test runner.
- Every new i18n key must be added to **all three** of `apps/frontend/messages/{en,zh-TW,zh-CN}.json`. Key parity is checked at merge.
- Alembic revision id `024_resume_import_progress`, `down_revision = "023_llm_call_cost"`. **Re-check `alembic heads` at merge** — parallel branches collide on revision ids. Verify the migration against a throwaway Postgres; never against production.
- **No new services and no new Python or npm dependencies.** There is no task runner, no Redis, no Argo (`kubectl get wf` denies the resource type).
- Fixed values: `MAX_STEPS = 12`, `LABEL_MAX = 120`, `STALE_AFTER_SECONDS = 60`, import `max_rounds = 14`, import `timeout = 180.0`.
- The snapshot payload is the contract between Tasks 3, 7, 8 and 10. Its exact shape is defined in Task 3's Interfaces block and must not drift.

## Review Focus

1. **Two imports started for the same resume** (Retry double-clicked): the second must be refused rather than run concurrently against the same source — test in Task 3.
2. **The upload bytes are unavailable** (`get_object` returns `None` because S3 is unconfigured in dev, or the key is gone): the import must fail with a readable error and never sit in `running` forever — test in Task 6.
3. **A malformed plan from the model** (duplicate keys, empty labels, 40 steps, non-string labels): must be sanitised and bounded, never persisted raw — test in Task 4.
4. **The stream after the run finishes**: must emit a final snapshot and close, not poll forever and leak a connection per reload — test in Task 7.
5. **An edit naming an unplanned step, and a planned step never edited**: appended and `skipped` respectively, with the import still reaching `done` — test in Task 4.

---

### Task 1: Read an upload back out of S3

**Files:**
- Modify: `apps/backend/app/services/s3.py`
- Test: `apps/backend/tests/test_s3_get_object.py`

**Interfaces:**
- Produces: `get_object(key: str) -> bytes | None` — the stored bytes, or `None` when object storage is unconfigured or the key is absent. Never raises for a missing key.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_object_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr("app.services.s3._client", lambda: None)
    assert get_object("uploads/x/y/z.pdf") is None

def test_get_object_returns_none_for_missing_key(monkeypatch):
    # _client() stub whose get_object raises ClientError with code NoSuchKey
    assert get_object("uploads/missing") is None

def test_get_object_returns_bytes(monkeypatch):
    # _client() stub returning {"Body": io.BytesIO(b"hello")}
    assert get_object("uploads/x") == b"hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_s3_get_object.py -q`
Expected: FAIL with `ImportError` / `cannot import name 'get_object'`

- [ ] **Step 3: Implement `get_object(key: str) -> bytes | None` in `apps/backend/app/services/s3.py`**

Mirror `put_object`'s use of `_client()` and `get_settings().s3_bucket`. Catch `ClientError` and `BotoCoreError` and return `None`; a missing upload is a condition the caller handles, not an exception.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_s3_get_object.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/s3.py apps/backend/tests/test_s3_get_object.py
git commit -m "feat(s3): read an object back by key"
```

---

### Task 2: Tables for import runs and their steps

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/024_resume_import_progress.py`
- Test: `apps/backend/tests/test_import_models.py`

**Interfaces:**
- Produces:
  - `ResumeImport` — `id: str`, `resume_id: str`, `status: str`, `file_key: str | None`, `error: str | None`, `version: int`, `applied: int`, `heartbeat_at: datetime`, `created_at: datetime`. CHECK `status IN ('running','done','failed')`.
  - `ResumeImportStep` — `id: str`, `import_id: str`, `seq: int`, `key: str`, `label: str`, `status: str`, `detail: str | None`. CHECK `status IN ('pending','active','done','skipped')`. UNIQUE `(import_id, key)`.
  - `Resume.imports` relationship.

- [ ] **Step 1: Write the failing tests**

```python
def test_import_run_defaults(db_session, resume):
    run = ResumeImport(resume_id=resume.id, status="running", file_key="uploads/k")
    db_session.add(run); db_session.flush()
    assert run.version == 0 and run.applied == 0 and run.id

def test_import_status_check_constraint(db_session, resume):
    # status="bogus" must raise IntegrityError on flush

def test_step_key_unique_per_import(db_session, resume):
    # two ResumeImportStep rows with the same (import_id, key) must raise IntegrityError
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'ResumeImport'`

- [ ] **Step 3: Add `ResumeImport` and `ResumeImportStep` to `apps/backend/app/models.py`**

Follow the existing `ChatMessage` pattern for `id`/`created_at` defaults (`_uuid`, `_utcnow`) and the `Resume.import_status` pattern for `CheckConstraint` naming (`resume_import_status_values` style).

- [ ] **Step 4: Write the alembic revision `024_resume_import_progress`**

`revision = "024_resume_import_progress"`, `down_revision = "023_llm_call_cost"`. `upgrade()` creates both tables with the constraints above; `downgrade()` drops them in reverse order.

- [ ] **Step 5: Run tests and the migration round-trip**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_models.py -q`
Expected: PASS

Then, against a **throwaway** Postgres (never production), with `DATABASE_URL` exported in the environment because `alembic/env.py` reads it from the process environment only:
Run: `.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head`
Expected: three clean runs, no error

- [ ] **Step 6: Commit**

```bash
git add apps/backend/app/models.py apps/backend/alembic/versions/024_resume_import_progress.py apps/backend/tests/test_import_models.py
git commit -m "feat(db): tables for resume import runs and steps"
```

---

### Task 3: The import run store

**Files:**
- Create: `apps/backend/app/services/import_run.py`
- Test: `apps/backend/tests/test_import_run.py`

**Interfaces:**
- Consumes: `ResumeImport`, `ResumeImportStep` from Task 2.
- Produces:
  - `MAX_STEPS = 12`, `LABEL_MAX = 120`, `STALE_AFTER_SECONDS = 60`
  - `start_run(db: Session, resume: Resume) -> ResumeImport` — raises `ImportAlreadyRunning` if a `running` row for this resume exists and is not stale.
  - `ImportAlreadyRunning(Exception)`
  - `touch(db: Session, run: ResumeImport) -> None` — increments `version`, sets `heartbeat_at`, commits.
  - `finish(db: Session, run: ResumeImport) -> None` — any `pending`/`active` step becomes `skipped`; `status="done"`; also sets `resume.import_status = "done"`.
  - `fail(db: Session, run: ResumeImport, error: str) -> None` — `status="failed"`, stores `error`, sets `resume.import_status = "failed"`.
  - `is_stale(run: ResumeImport, now: datetime | None = None) -> bool`
  - `snapshot(db: Session, resume_id: str) -> dict | None` — the newest run for the resume, or `None`. **This dict is the wire contract** for Tasks 7, 8 and 10:

```python
{
  "import_id": str,
  "status": "running" | "done" | "failed",
  "version": int,
  "applied": int,          # edits applied so far; the preview refreshes when this changes
  "steps": [{"key": str, "label": str, "status": str, "detail": str | None}],  # seq order
  "error": str | None,
  "started_at": str,       # ISO 8601
  "stale": bool,           # status == "running" and heartbeat older than STALE_AFTER_SECONDS
}
```

- [ ] **Step 1: Write the failing tests**

```python
def test_start_run_sets_running_and_pending_status(db_session, resume):
    run = start_run(db_session, resume)
    assert run.status == "running"
    assert resume.import_status == "pending"

def test_second_concurrent_run_is_refused(db_session, resume):   # Review Focus 1
    start_run(db_session, resume)
    with pytest.raises(ImportAlreadyRunning):
        start_run(db_session, resume)

def test_stale_run_may_be_replaced(db_session, resume):
    run = start_run(db_session, resume)
    run.heartbeat_at = _utcnow() - timedelta(seconds=STALE_AFTER_SECONDS + 1)
    db_session.flush()
    assert start_run(db_session, resume).id != run.id

def test_touch_bumps_version_monotonically(db_session, resume):
    run = start_run(db_session, resume)
    before = run.version
    touch(db_session, run); touch(db_session, run)
    assert run.version == before + 2

def test_finish_skips_unreached_steps(db_session, resume):
    # plan three steps, mark none; finish -> all three "skipped", status "done"

def test_snapshot_shape_and_stale_flag(db_session, resume):
    snap = snapshot(db_session, resume.id)
    assert set(snap) == {"import_id","status","version","applied","steps","error","started_at","stale"}
    assert snap["stale"] is False

def test_snapshot_is_none_without_a_run(db_session, resume):
    assert snapshot(db_session, resume.id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_run.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.import_run`

- [ ] **Step 3: Implement the module in `apps/backend/app/services/import_run.py`**

`start_run` takes the resume's `upload_s3_key` as `file_key` and sets `resume.import_status = "pending"`. Every mutating function must bump `version` in the **same transaction** as its change — a step write that does not bump it freezes the card while the resume keeps changing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_run.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/import_run.py apps/backend/tests/test_import_run.py
git commit -m "feat(import): run store with versioned snapshots"
```

---

### Task 4: Plan declaration and the drift rules

**Files:**
- Modify: `apps/backend/app/services/import_run.py`
- Test: `apps/backend/tests/test_import_drift.py`

**Interfaces:**
- Consumes: Task 3's module.
- Produces:
  - `declare_plan(db: Session, run: ResumeImport, steps: list[dict]) -> list[ResumeImportStep]` — accepts `[{"key": str, "label": str}]`. Drops entries whose key or label is not a non-empty string; de-duplicates by key keeping the first; truncates labels to `LABEL_MAX`; keeps at most `MAX_STEPS`. Called twice, it replaces the list while preserving `status` for keys that still exist. First step becomes `active`, rest `pending`.
  - `advance(db: Session, run: ResumeImport, step_key: str | None, detail: str | None = None) -> None` — marks `step_key` `done` and the next `pending` step `active`; appends `step_key` as a new `done` step when it is not in the plan; increments `run.applied`.

    When `step_key` is `None` or empty — the model edited without naming a step — it synthesises `key=f"edit-{run.applied + 1}"` and `label=""`. The synthesised key must be unique per edit: a literal `""` key would violate `UNIQUE(import_id, key)` on the second unlabelled edit and abort the import. An empty `label` is the frontend's signal to render its own fallback wording (Task 8), which keeps translation on the frontend where the locale is known.

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_drops_bad_entries_and_bounds_size(db_session, resume):   # Review Focus 3
    steps = ([{"key": "", "label": "x"}, {"key": "a", "label": ""}, {"key": "b", "label": 7}]
             + [{"key": f"k{i}", "label": "L" * 300} for i in range(40)])
    kept = declare_plan(db_session, run, steps)
    assert len(kept) == MAX_STEPS
    assert all(len(s.label) <= LABEL_MAX for s in kept)

def test_plan_dedupes_by_key_keeping_first(db_session, resume):
    kept = declare_plan(db_session, run, [{"key":"a","label":"First"},{"key":"a","label":"Second"}])
    assert [s.label for s in kept] == ["First"]

def test_first_step_is_active(db_session, resume):
    kept = declare_plan(db_session, run, [{"key":"a","label":"A"},{"key":"b","label":"B"}])
    assert [s.status for s in kept] == ["active", "pending"]

def test_advance_marks_done_and_activates_next(db_session, resume):
    declare_plan(db_session, run, [{"key":"a","label":"A"},{"key":"b","label":"B"}])
    advance(db_session, run, "a", detail="2 degrees")
    assert [s.status for s in _steps(db_session, run)] == ["done", "active"]
    assert run.applied == 1

def test_unplanned_step_is_appended_done(db_session, resume):   # Review Focus 5
    declare_plan(db_session, run, [{"key":"a","label":"A"}])
    advance(db_session, run, "surprise")
    rows = _steps(db_session, run)
    assert rows[-1].key == "surprise" and rows[-1].status == "done"

def test_planned_step_never_edited_is_skipped_and_run_completes(db_session, resume):   # Review Focus 5
    declare_plan(db_session, run, [{"key":"a","label":"A"},{"key":"b","label":"B"}])
    advance(db_session, run, "a")
    finish(db_session, run)
    assert run.status == "done"
    assert [s.status for s in _steps(db_session, run)] == ["done", "skipped"]

def test_redeclare_preserves_status_of_surviving_keys(db_session, resume):
    declare_plan(db_session, run, [{"key":"a","label":"A"},{"key":"b","label":"B"}])
    advance(db_session, run, "a")
    kept = declare_plan(db_session, run, [{"key":"a","label":"A"},{"key":"c","label":"C"}])
    assert {s.key: s.status for s in kept}["a"] == "done"

def test_advance_bumps_version(db_session, resume):
    # every declare_plan and advance call increases run.version

def test_consecutive_unlabelled_edits_do_not_collide(db_session, resume):
    advance(db_session, run, None)
    advance(db_session, run, "")
    rows = _steps(db_session, run)
    assert [r.key for r in rows] == ["edit-1", "edit-2"]
    assert [r.label for r in rows] == ["", ""]
    assert run.applied == 2

def test_unlabelled_edits_work_without_any_plan(db_session, resume):
    advance(db_session, run, None)
    finish(db_session, run)
    assert run.status == "done" and run.applied == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_drift.py -q`
Expected: FAIL with `ImportError: cannot import name 'declare_plan'`

- [ ] **Step 3: Implement `declare_plan` and `advance` in `apps/backend/app/services/import_run.py`**

Sanitising happens before any row is written, so a malformed plan can never reach the database.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_drift.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/import_run.py apps/backend/tests/test_import_drift.py
git commit -m "feat(import): plan declaration with drift rules"
```

---

### Task 5: The plan tool, and a step label on edits

**Files:**
- Create: `apps/backend/app/services/import_tools.py`
- Modify: `apps/backend/app/services/llm/tools.py:126-136` (EDIT_TOOL `properties`)
- Test: `apps/backend/tests/test_import_tools.py`

**Interfaces:**
- Consumes: `declare_plan` from Task 4.
- Produces:
  - `DECLARE_PLAN_TOOL = "declare_import_plan"`
  - `import_tool_specs() -> list[dict]` — one spec, same flat `{type, name, description, parameters}` shape as `app/services/family_tools.py`.
  - `import_tool_handlers(db: Session, run: ResumeImport) -> dict[str, Callable[[dict], str]]`
  - `EDIT_TOOL` gains an optional `step: {"type": "string"}` property. The schema has no `additionalProperties: False` and no `required`, so this is a pure addition.

- [ ] **Step 1: Write the failing tests**

```python
def test_declare_plan_tool_spec_shape():
    spec = import_tool_specs()[0]
    assert spec["type"] == "function" and spec["name"] == "declare_import_plan"
    assert "steps" in spec["parameters"]["properties"]

def test_handler_persists_the_plan(db_session, resume):
    handlers = import_tool_handlers(db_session, run)
    out = json.loads(handlers["declare_import_plan"]({"steps":[{"key":"a","label":"A"}]}))
    assert out["accepted"] == 1

def test_edit_tool_advertises_step():
    edit = next(t for t in TOOLS if t.get("name") == EDIT_TOOL)
    assert edit["parameters"]["properties"]["step"] == {"type": "string"}

def test_step_argument_does_not_change_edit_result():
    base = apply_typst_edit("hello world", {"search": "world", "replace": "there"})
    withstep = apply_typst_edit("hello world", {"search": "world", "replace": "there", "step": "a"})
    assert base == withstep
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_tools.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.import_tools`

- [ ] **Step 3: Implement `import_tool_specs()` and `import_tool_handlers()`, and add `step` to EDIT_TOOL**

The tool description must tell the model to list the sections it found in the upload and to name the same `key` in each later edit's `step`. The handler returns a small JSON object (`{"accepted": n}`) — tool results go back into model context, so keep it short.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_tools.py tests/test_chat.py -q`
Expected: PASS, including the existing chat suite — the EDIT_TOOL change must not disturb it

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/import_tools.py apps/backend/app/services/llm/tools.py apps/backend/tests/test_import_tools.py
git commit -m "feat(import): plan tool and an optional step label on edits"
```

---

### Task 6: The import runner

**Files:**
- Create: `apps/backend/app/services/import_runner.py`
- Test: `apps/backend/tests/test_import_runner.py`

**Interfaces:**
- Consumes: `get_object` (Task 1); `start_run`, `touch`, `advance`, `finish`, `fail` (Tasks 3-4); `import_tool_specs`, `import_tool_handlers` (Task 5).
- Produces: `async def run_import(db: Session, resume: Resume, run: ResumeImport, *, model: str | None = None) -> None` — drives one import to completion and never raises; every failure path ends in `fail()`.

Behaviour, in order: read the upload with `get_object(run.file_key)`; if `None`, `fail()` with a readable message and return. Otherwise call `aiter_chat_edit` with `prefer_full_source=False`, `verify_compile=True`, `max_rounds=14`, `timeout=180.0`, `extra_tools=import_tool_specs()`, `tool_handlers=import_tool_handlers(db, run)`. For each event: on `kind == "tool"` where `trace["name"] == EDIT_TOOL` and `trace["result"].get("changed")` is true, call `advance(db, run, trace["arguments"].get("step"))` — pass the value through untouched, including `None`, because Task 4 owns the unlabelled case; on `kind == "source"`, persist via `set_draft_source` as the chat router does; `touch()` on every event so the heartbeat stays fresh. Persist assistant and tool rows with the existing `_persist_*` helpers, and **write no `user` row** — the prompt is server-owned. On completion call `finish()`; on any exception call `fail()`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_missing_upload_fails_cleanly(db_session, resume, monkeypatch):   # Review Focus 2
    monkeypatch.setattr("app.services.import_runner.get_object", lambda key: None)
    await run_import(db_session, resume, run)
    assert run.status == "failed" and run.error
    assert resume.import_status == "failed"

async def test_scripted_import_walks_its_plan(db_session, resume, monkeypatch, openai_enabled):
    # ScriptedOpenAI: declare_import_plan(3 steps) -> read_typst -> 3 edits each with "step" -> final message
    await run_import(db_session, resume, run)
    assert run.status == "done" and run.applied == 3
    assert [s.status for s in _steps(db_session, run)] == ["done", "done", "done"]

async def test_import_writes_no_user_message(db_session, resume, monkeypatch, openai_enabled):
    await run_import(db_session, resume, run)
    assert not [m for m in _messages(db_session, resume.id) if m.role == "user"]

async def test_import_applies_sections_incrementally(db_session, resume, monkeypatch, openai_enabled):
    # assert run.applied increments once per changed edit, not once for the whole turn

async def test_model_error_marks_failed_not_running(db_session, resume, monkeypatch, openai_enabled):
    # ScriptedOpenAI raising mid-turn -> status "failed", never left "running"

async def test_import_without_a_declared_plan_still_completes(db_session, resume, monkeypatch, openai_enabled):
    # ScriptedOpenAI: read_typst -> 2 edits carrying NO "step" -> final message.
    # The plan tool is never called; the import must still reach "done".
    await run_import(db_session, resume, run)
    assert run.status == "done" and run.applied == 2

async def test_runner_uses_its_own_round_budget(db_session, resume, monkeypatch, openai_enabled):
    seen = {}
    # wrap app.services.import_runner.aiter_chat_edit to capture kwargs
    await run_import(db_session, resume, run)
    assert seen["max_rounds"] == 14
    assert seen["prefer_full_source"] is False
```

The budget assertion is not ceremony: inheriting the default of 8 silently truncates any resume with more than five sections, and the symptom looks like a model failure rather than a configuration one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_runner.py -q`
Expected: FAIL with `ModuleNotFoundError: app.services.import_runner`

- [ ] **Step 3: Implement `run_import` in `apps/backend/app/services/import_runner.py`**

Read `app/routers/chat.py`'s `relay()` for how `tool`, `source` and `assistant` events are persisted and reuse those helpers rather than re-deriving them. The compile-repair loop stays in the chat router; this runner does not duplicate it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_runner.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/import_runner.py apps/backend/tests/test_import_runner.py
git commit -m "feat(import): runner with its own round budget"
```

---

### Task 7: Start, snapshot and stream endpoints

**Files:**
- Modify: `apps/backend/app/routers/resumes.py`
- Test: `apps/backend/tests/test_import_api.py`

**Interfaces:**
- Consumes: `start_run`, `snapshot`, `ImportAlreadyRunning` (Task 3); `run_import` (Task 6).
- Produces:
  - `POST /v1/resumes/{resume_id}/import` → `201` with the Task 3 snapshot; `409` when `ImportAlreadyRunning`; `400` when the resume has no `upload_s3_key`. Schedules `run_import` as an `asyncio` task on the running loop.
  - `GET /v1/resumes/{resume_id}/import` → `200` with the snapshot, or `404` when there is none.
  - `GET /v1/resumes/{resume_id}/import/stream` → `text/event-stream`, reusing `SSE_HEADERS` and the `_sse` framing from `app/routers/chat.py`. Emits a snapshot immediately, then one on every `version` change, polling every 400ms. **Emits a final snapshot and closes once `status` is `done` or `failed`.**
  - All three use `_owned_resume(...)` for ownership.

- [ ] **Step 1: Write the failing tests**

```python
def test_start_import_returns_snapshot(client, uploaded_resume):
    r = client.post(f"/v1/resumes/{uploaded_resume}/import")
    assert r.status_code == 201 and r.json()["status"] == "running"

def test_start_import_conflicts_while_running(client, uploaded_resume):   # Review Focus 1
    client.post(f"/v1/resumes/{uploaded_resume}/import")
    assert client.post(f"/v1/resumes/{uploaded_resume}/import").status_code == 409

def test_start_import_requires_an_upload(client, created_resume):
    assert client.post(f"/v1/resumes/{created_resume}/import").status_code == 400

def test_get_import_is_404_before_any_run(client, uploaded_resume):
    assert client.get(f"/v1/resumes/{uploaded_resume}/import").status_code == 404

def test_second_client_attaching_sees_current_state(client, uploaded_resume):
    # start, advance two steps, then GET -> applied == 2, two steps "done"

def test_stream_closes_once_the_run_is_done(client, uploaded_resume):   # Review Focus 4
    # with a run already status="done": the stream yields one snapshot then ends
    with client.stream("GET", f"/v1/resumes/{uploaded_resume}/import/stream") as s:
        frames = [line for line in s.iter_lines() if line.startswith("data:")]
    assert len(frames) == 1

def test_import_endpoints_reject_another_owner(client, other_guest_resume):
    assert client.get(f"/v1/resumes/{other_guest_resume}/import").status_code in (403, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_api.py -q`
Expected: FAIL with `404` on every route

- [ ] **Step 3: Implement the three routes in `apps/backend/app/routers/resumes.py`**

The stream must read the snapshot through a **fresh session per poll** so it observes another worker's commits; holding one session open would serve a stale transaction snapshot forever.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_import_api.py tests/test_import.py -q`
Expected: PASS, including the existing upload suite

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/routers/resumes.py apps/backend/tests/test_import_api.py
git commit -m "feat(api): start, read and stream an import"
```

---

### Task 8: The progress reducer

**Files:**
- Create: `apps/frontend/lib/import-progress.ts`
- Test: `apps/frontend/lib/import-progress.test.ts`

**Interfaces:**
- Produces:
  - `export type ImportStepStatus = "pending" | "active" | "done" | "skipped"`
  - `export type ImportSnapshot = { import_id: string; status: "running" | "done" | "failed"; version: number; applied: number; steps: { key: string; label: string; status: ImportStepStatus; detail: string | null }[]; error: string | null; started_at: string; stale: boolean }`
  - `export type ImportViewStep = { key: string; label: string; status: ImportStepStatus; detail: string | null; unlabelled: boolean }`
  - `export type ImportView = { mode: "running" | "done" | "failed" | "stale"; steps: ImportViewStep[]; doneCount: number; totalCount: number; error: string | null; showPlan: boolean }`
  - `export function toImportView(snap: ImportSnapshot): ImportView`

`unlabelled` is `true` when the snapshot's `label` is empty — the backend's signal that the model edited without naming a step (Task 4). The card renders the `importUnlabelledStep` string for those rows, so translation stays where the locale is.
  - `export function importTurnStartsAt(snap: ImportSnapshot): number` — `Date.parse(snap.started_at)`, for grouping messages into the import turn.

`mode` is `"stale"` when `snap.stale` is true and `status` is `"running"`, otherwise `status`. `showPlan` is false once `mode === "done"`, which is what collapses the card. `totalCount` is `steps.length`.

- [ ] **Step 1: Write the failing tests**

```typescript
test("running snapshot counts done steps against the plan", () => {
  const v = toImportView(snap({status:"running", steps:[d("a"),d("b"),act("c"),pend("d")]}));
  assert.equal(v.mode, "running");
  assert.equal(v.doneCount, 2);
  assert.equal(v.totalCount, 4);
});

test("a stale running snapshot becomes stale mode", () => {
  assert.equal(toImportView(snap({status:"running", stale:true})).mode, "stale");
});

test("a stale flag on a finished run is ignored", () => {
  assert.equal(toImportView(snap({status:"done", stale:true})).mode, "done");
});

test("done collapses the plan", () => {
  assert.equal(toImportView(snap({status:"done"})).showPlan, false);
});

test("failed keeps the plan and the error visible", () => {
  const v = toImportView(snap({status:"failed", error:"ran out of turns"}));
  assert.equal(v.showPlan, true);
  assert.equal(v.error, "ran out of turns");
});

test("an empty plan still yields a usable view", () => {
  const v = toImportView(snap({status:"running", steps:[]}));
  assert.equal(v.totalCount, 0);
  assert.equal(v.doneCount, 0);
});

test("a step with an empty label is marked unlabelled", () => {
  const v = toImportView(snap({status:"running", steps:[
    {key:"edit-1", label:"", status:"done", detail:null},
    {key:"work", label:"Work history", status:"active", detail:null},
  ]}));
  assert.deepEqual(v.steps.map(s => s.unlabelled), [true, false]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/frontend && npx tsx --test lib/import-progress.test.ts`
Expected: FAIL — cannot resolve `./import-progress`

- [ ] **Step 3: Implement `apps/frontend/lib/import-progress.ts`**

Pure functions only — no React, no fetch. This is the only part of the frontend this repo can unit-test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/frontend && npx tsx --test lib/import-progress.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/lib/import-progress.ts apps/frontend/lib/import-progress.test.ts
git commit -m "feat(import): progress view reducer"
```

---

### Task 9: Client calls for the import

**Files:**
- Modify: `apps/frontend/lib/api.ts`
- Test: `apps/frontend/lib/import-api.test.ts`

**Interfaces:**
- Consumes: `ImportSnapshot` from Task 8.
- Produces:
  - `export async function startImport(id: string): Promise<ImportSnapshot>`
  - `export async function getImport(id: string): Promise<ImportSnapshot | null>` — `null` on 404
  - `export async function streamImport(id: string, onSnapshot: (s: ImportSnapshot) => void | Promise<void>, signal?: AbortSignal): Promise<void>`

`streamImport` reuses the SSE line-splitting already written in `sendChat` (`lib/api.ts:705-725`): split the decoded buffer on `\n\n`, parse each `data:` block. Extract that splitting into a shared helper rather than copying it.

- [ ] **Step 1: Write the failing test**

```typescript
test("parses concatenated and split sse frames into snapshots", async () => {
  // feed a fake ReadableStream whose chunks split one frame across two reads
  const seen: ImportSnapshot[] = [];
  await streamImportFromBody(fakeBody, s => { seen.push(s); });
  assert.deepEqual(seen.map(s => s.version), [1, 2, 3]);
});
```

Expose the body-reading half as `streamImportFromBody(body: ReadableStream<Uint8Array>, onSnapshot)` so it is testable without `fetch`; `streamImport` is the thin wrapper that supplies `res.body`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/frontend && npx tsx --test lib/import-api.test.ts`
Expected: FAIL — `streamImportFromBody` is not exported

- [ ] **Step 3: Implement the three functions plus `streamImportFromBody` in `apps/frontend/lib/api.ts`**

Follow the existing `apiJson` / `apiFetch` error conventions, including `ApiError`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/frontend && npx tsx --test lib/import-api.test.ts && npx tsx --test lib/agent-markdown.test.ts`
Expected: PASS both — the extracted SSE helper must not break existing behaviour

- [ ] **Step 5: Commit**

```bash
git add apps/frontend/lib/api.ts apps/frontend/lib/import-api.test.ts
git commit -m "feat(import): client calls and snapshot stream reader"
```

---

### Task 10: The card, pinned to the bottom of the turn

**Files:**
- Create: `apps/frontend/components/editor/ImportProgressCard.tsx`
- Modify: `apps/frontend/components/editor/EditorChatPanel.tsx` (after the reasoning panel, ~line 495), `apps/frontend/components/editor/EditorShell.tsx`

**Interfaces:**
- Consumes: `toImportView` (Task 8); `getImport`, `streamImport` (Task 9).
- Produces:
  - `<ImportProgressCard view={ImportView} elapsedSeconds={number} onRetry={() => void} onResume={() => void} />`
  - `EditorShell` exports nothing new; it gains `importView` state and passes it to `EditorChatPanel` as `importView?: ImportView`.
  - `refreshAfterSourceChange(): Promise<void>` in `EditorShell` — the extracted `refreshPreview()` + `compileResume()` + `refreshAts()` trio, called both by the chat `source` handler (`EditorShell.tsx:664-673`) and by the import snapshot handler.

Behaviour: on mount, `getImport(resumeId)`; if a snapshot exists and `status === "running"`, open `streamImport` and keep the latest snapshot in state, aborting on unmount. When `applied` increases between snapshots, call `refreshAfterSourceChange()`. The card renders **after** the message list and the reasoning panel so tool messages append above it. The four modes come from `view.mode`; `stale` shows Resume, `failed` shows Retry, both calling `startImport`.

- [ ] **Step 1: Extract the refresh trio**

Replace the inline body at `EditorShell.tsx:664-673` with a call to a new `refreshAfterSourceChange()` defined in the same component. No behaviour change.

- [ ] **Step 2: Verify nothing regressed**

Run: `cd apps/frontend && npx tsc --noEmit && npx eslint components/editor/EditorShell.tsx`
Expected: no errors

- [ ] **Step 3: Implement `ImportProgressCard.tsx`**

Match the mockup in `.superpowers/brainstorm/*/content/import-animated.html`: title, `n of N`, step rows with status marks, progress bar, footer with elapsed time and the mode's action. Use existing primitives and the `rr-*` / shadcn classes already used by `ChatMessageCard.tsx`; all strings come from `useTranslations("editor")` (Task 11). No new dependency.

- [ ] **Step 4: Wire `EditorShell` and `EditorChatPanel`**

Mount the stream in `EditorShell`; render the card in `EditorChatPanel` after the reasoning panel block.

- [ ] **Step 5: Verify types, lint and the full frontend suite**

Run: `cd apps/frontend && npx tsc --noEmit && npx eslint . && npx tsx --test lib/import-progress.test.ts lib/import-api.test.ts`
Expected: no errors, tests PASS

- [ ] **Step 6: Verify the card visually, since no unit test can**

This repo has no jsdom or React Testing Library, so "the card renders from a snapshot, stays last in the turn, and survives a remount" — required by the spec's testing section — is only checkable by looking. Drive the editor in headless Chrome over CDP against a stub API that returns a scripted sequence of snapshots, and confirm three things: tool messages append **above** the card, the card stays last, and a reload mid-import re-attaches and keeps counting.

Expected: all three hold. Record what you saw in the commit message; a claim of "renders correctly" without having driven it is not verification.

- [ ] **Step 7: Commit**

```bash
git add apps/frontend/components/editor/ImportProgressCard.tsx apps/frontend/components/editor/EditorChatPanel.tsx apps/frontend/components/editor/EditorShell.tsx
git commit -m "feat(import): progress card pinned to the bottom of the turn"
```

---

### Task 11: Start the import from upload, and retire the in-memory handoff

**Files:**
- Modify: `apps/frontend/app/[locale]/upload/page.tsx`, `apps/frontend/components/editor/EditorShell.tsx`, `apps/frontend/messages/{en,zh-TW,zh-CN}.json`
- Delete: `apps/frontend/lib/pending-upload.ts`
- Test: `apps/frontend/lib/i18n-parity.test.ts`

**Interfaces:**
- Consumes: `startImport` (Task 9).
- Produces: nothing new. `peekPendingUpload` / `takePendingUpload` and their three call sites in `EditorShell.tsx` (lines ~188, ~788, ~814) are removed, along with the `importFillPrompt` auto-send.

Upload becomes: `uploadResume({file, locale})` → `startImport(resume.id)` → navigate to `/editor/{id}`. The browser no longer carries the file, so a refresh costs nothing.

New i18n keys under `editor`, in all three locales: `importTitle`, `importPlanFound`, `importWorking`, `importElapsed`, `importDoneTitle`, `importFailedTitle`, `importStaleTitle`, `importRetry`, `importResume`, `importNothingLost`, `importUnlabelledStep`. Remove `importFillPrompt`.

- [ ] **Step 1: Write the failing i18n parity test**

```typescript
test("every locale has the same key set", () => {
  const keys = (o: object) => Object.keys(flatten(o)).sort();
  assert.deepEqual(keys(zhTW), keys(en));
  assert.deepEqual(keys(zhCN), keys(en));
});

test("import card keys exist in every locale", () => {
  for (const m of [en, zhTW, zhCN]) assert.ok(m.editor.importTitle);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/frontend && npx tsx --test lib/i18n-parity.test.ts`
Expected: FAIL — `editor.importTitle` is undefined

- [ ] **Step 3: Add the keys to all three locale files and remove `importFillPrompt`**

- [ ] **Step 4: Rewrite `upload/page.tsx` to call `startImport` and drop the stash**

- [ ] **Step 5: Remove `lib/pending-upload.ts` and its three `EditorShell` call sites**

Deleting the module is what proves nothing still depends on it — `tsc` fails if anything does.

- [ ] **Step 6: Verify the whole suite**

Run: `cd apps/frontend && npx tsc --noEmit && npx eslint . && npx tsx --test lib/*.test.ts`
Expected: no errors, tests PASS

Run: `cd apps/backend && .venv/bin/python -m pytest -q`
Expected: PASS except the two failures already known to be environmental in a worktree

- [ ] **Step 7: Commit**

```bash
git add apps/frontend/app/[locale]/upload/page.tsx apps/frontend/components/editor/EditorShell.tsx apps/frontend/messages apps/frontend/lib/i18n-parity.test.ts
git rm apps/frontend/lib/pending-upload.ts
git commit -m "feat(import): start imports server-side and drop the in-memory handoff"
```
