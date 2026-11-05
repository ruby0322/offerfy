---
name: Deterministic template switch
overview: Stop guessing a template switch from English substrings and stop paying a full model rewrite for every one. Three independent shipments: a committed support list that hides the ~40% of listed templates that cannot work, an endpoint that carries the target as a parameter instead of a sentence, and a derived content record plus four hand-written emitters that make switching between those four instant, free and reproducible.
todos:
  - id: t1-inline-data
    content: "Task 1: Inline a template's yaml/toml/json data file as a Typst value"
    status: pending
  - id: t2-probe
    content: "Task 2: Probe one template and classify its font warnings"
    status: pending
  - id: t3-support-list
    content: "Task 3: Survey job, committed support list, list_templates filtering"
    status: pending
  - id: t4-endpoint
    content: "Task 4: Switch endpoint (model path), template parameter, delete the English matcher, role card"
    status: pending
  - id: t5-frontend-endpoint
    content: "Task 5: Picker calls the endpoint instead of composing a chat message"
    status: pending
  - id: t6-content-record
    content: "Task 6: resume_contents table and the ResumeContent schema"
    status: pending
  - id: t7-extraction
    content: "Task 7: Extract a ResumeContent from Typst source, billed as 'extract'"
    status: pending
  - id: t8-registry
    content: "Task 8: Emitter protocol, registry, golden IR, basic-resume emitter"
    status: pending
  - id: t9-ats-friendly
    content: "Task 9: ats-friendly-resume emitter"
    status: pending
  - id: t10-acorn
    content: "Task 10: acorn-resume emitter"
    status: pending
  - id: t11-clean-print
    content: "Task 11: clean-print-cv emitter"
    status: pending
  - id: t12-applied-mode
    content: "Task 12: Applied mode end to end, system row, editor rendering"
    status: pending
  - id: t13-verify
    content: "Task 13: Three locales, lint, tsc, build, browser walk of both paths"
    status: pending
isProject: false
---

# Deterministic Template Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switching to one of four curated templates becomes a code path — instant, free, identical every time, and honest about what the target template cannot hold — while every other listed template keeps the model rewrite it has today, and templates that cannot work stop being offered at all.

**Architecture:** `Resume.typst_source` stays the source of truth. A new `resume_contents` row is a derived snapshot keyed by the hash of the source it came from; when they disagree it is re-extracted once, by a model, the way `services/profile.build_profile` already derives a talent profile. Emitters are per-package Python functions pinned to an exact package version that turn that snapshot into a complete single-file document and declare what they dropped. A committed `data/template_support.json`, produced by an offline job, decides what the picker may offer and what the endpoint will accept.

**Tech Stack:** FastAPI, SQLAlchemy, pydantic, alembic (Postgres-only CHECK changes), pytest; typst 0.15.1 via `apps/backend/.tools/typst`; Next 16 / React 19 / next-intl on the frontend.

**Spec:** [docs/superpowers/specs/2026-11-05-deterministic-template-switch-design.md](../specs/2026-11-05-deterministic-template-switch-design.md)

## Global Constraints

- A resume is **one file**. `compile_typst_pages` writes it to a lone `resume.typ` in an empty temp dir; nothing may reference a sidecar file.
- Emitters pin `(name, version)` exactly — never "latest". A version with no emitter falls back to the model path.
- Emitters set the font explicitly rather than inheriting the package default. Use `"New Computer Modern"` unless the locale needs CJK, matching `services/starter.PLACEHOLDERS`.
- All Typst escaping goes through `escape_typst_string` / `escape_typst_markup` in `app/services/typst_escape.py`. No emitter writes its own, and `typst_value` lives there too — rendering a Python value as a Typst literal is the same concern, and both the probe and the emitters need it.
- A data-driven template is an emitter candidate **only when the package exposes the section functions**. `clean-print-cv` qualifies: its entrypoint just wires `cv-header(data.personal)` and seven more. `swe-cv` does not — its entrypoint *is* the layout and the package supplies only helpers, so an emitter would carry a copy of the vendor's layout and silently stop matching upstream.
- The frontend has no test runner (no vitest/jest/playwright in `apps/frontend/package.json`). Frontend tasks are gated by `npx tsc --noEmit`, `npm run lint` and a named manual check — that is the available gate, not a skipped one.
- Every word sent to a model lives in `app/services/llm/prompts.py`.
- Model-call telemetry writes an `LlmCall` row **and** `meter.record(...)`; `llm_calls` has a CHECK that `user_id` and `guest_session_id` are never both set.
- `Text`, not `JSONB`, for stored JSON blobs — see the comment at `models.py`'s `profile_json`.
- Typst-dependent tests use `pytestmark = pytest.mark.skipif(not typst_available(), reason=...)`, following `tests/test_import_qa.py`. This worktree has no binary; those tests must skip, not fail.
- CHECK-constraint migrations are Postgres-only and no test runs alembic — `conftest.py` builds the schema with `create_all`. Confirm `alembic heads` before writing one; `025` is the head as of 2026-11-05 and other sessions merge to `main` concurrently.
- Frontend copy goes in `messages/{en,zh-TW,zh-CN}.json` — all three, never only `en`.

## Review Focus

1. **A guest switches templates.** `profile.record_build_spend` takes `user: User`, but a guest has none, and `llm_calls` forbids setting both subject columns. An extraction billed the wrong way is a 500 on an ordinary path. *(Task 7)*
2. **Chat edits the source between extraction and the write.** The freshness check happens before `emit()`; committing afterwards without re-checking silently discards whatever the person just changed. *(Task 12)*
3. **Extraction comes back empty or unparseable.** A model that returns `{}` must not produce a résumé with a name and nothing else; the endpoint falls back to the model path and writes nothing. *(Task 7)*
4. **The resume is still the untouched starter.** Placeholder content, and in `zh-TW` by default. Emitters must not crash on an absent section — `acorn-resume` calls `.map` on a nominally optional argument — and must not emit an empty `#edu(...)`. *(Task 8)*
5. **`data/template_support.json` is missing, truncated, or lists a package that was never prefetched.** `list_templates()` must degrade to an empty list rather than 500, and the endpoint must reject an unlisted pair with 400. *(Tasks 3, 4)*

---

### Task 1: Inline a template's data file as a Typst value

The transform that recovers nine otherwise-unusable templates. Pure function, no typst.

**Files:**
- Modify: `apps/backend/app/services/typst_escape.py`
- Create: `apps/backend/app/services/template_probe.py`
- Test: `apps/backend/tests/test_typst_escape.py`, `apps/backend/tests/test_template_probe.py`

**Interfaces:**
- Produces: `typst_escape.typst_value(value: Any) -> str`; `template_probe.inline_data_files(source: str, base: Path) -> tuple[str, tuple[str, ...]]` returning the rewritten source and the relative paths it inlined.

`typst_value` goes in `typst_escape.py`, not in the probe: the emitters need it too (Task 11), and an emitter importing from a survey module would have the layering backwards.

- [ ] **Step 1: Write the failing tests**

```python
def test_typst_value_renders_nested_dicts_and_lists():
    out = typst_value({"a": [1, "x"], "b": {"c": True}, "d": None})
    assert out.replace("\n", "").replace(" ", "") == '("a":(1,"x",),"b":("c":true,),"d":none,)'

def test_typst_value_escapes_quotes_and_backslashes():
    assert typst_value('he said "hi"\\n') == '"he said \\"hi\\"\\\\n"'

def test_typst_value_renders_empty_dict_and_list():
    assert typst_value({}) == "(:)"
    assert typst_value([]) == "()"

def test_inline_data_files_replaces_the_loader_call(tmp_path):
    (tmp_path / "cv.yaml").write_text("name: Ruby\n", encoding="utf-8")
    src = '#let data = yaml("cv.yaml")\n#data.name'
    out, inlined = inline_data_files(src, tmp_path)
    assert 'yaml("cv.yaml")' not in out
    assert '"name": "Ruby"' in out
    assert inlined == ("cv.yaml",)

def test_inline_data_files_handles_toml_and_json(tmp_path): ...
def test_inline_data_files_leaves_source_alone_when_the_file_is_absent(tmp_path): ...
def test_inline_data_files_ignores_a_computed_path(tmp_path):
    # sys.inputs-driven loaders (polycv, porygon) are out of reach by design
    src = '#let p = sys.inputs.at("CV", default: "cv.json")\n#let d = json(p)'
    assert inline_data_files(src, tmp_path) == (src, ())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_typst_escape.py tests/test_template_probe.py -v`
Expected: FAIL, `ImportError` / `NameError` on both functions.

- [ ] **Step 3: Implement `typst_value` in `typst_escape.py` and `inline_data_files` in `template_probe.py`**

`typst_value` maps dict → `("key": value,)` with string keys (Typst accepts them and a template key may not be an identifier), list/tuple → `(a, b,)`, `bool` → `true`/`false`, `None` → `none`, numbers verbatim, everything else a `"..."` string via `escape_typst_string`. `inline_data_files` matches only a literal path — `\b(yaml|toml|json)\(\s*"([^"]+)"\s*\)` — reads the file relative to `base` with `yaml.safe_load` / `tomllib` / `json`, and substitutes. A missing or unparseable file leaves that call untouched.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_typst_escape.py tests/test_template_probe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/typst_escape.py apps/backend/app/services/template_probe.py \
        apps/backend/tests/test_typst_escape.py apps/backend/tests/test_template_probe.py
git commit -m "feat(templates): render a Python value as Typst and inline a template's data file"
```

---

### Task 2: Probe one template and classify its font warnings

**Files:**
- Modify: `apps/backend/app/services/template_probe.py`
- Modify: `apps/backend/tests/test_template_probe.py`

**Interfaces:**
- Consumes: Task 1's `inline_data_files`.
- Produces: `INSTALLABLE_FONTS: frozenset[str]`, `font_warnings(stderr: str) -> tuple[str, ...]`, `unavailable_fonts(warnings) -> tuple[str, ...]`, `probe_template(name: str, version: str, *, root: Path) -> SupportRow`, and `SupportRow` — a frozen dataclass with `name, version, ok: bool, pages: int, needs_inline_data: bool, font_warnings: tuple[str, ...], installable_fonts: tuple[str, ...], reason: str`.

- [ ] **Step 1: Write the failing tests**

```python
def test_font_warnings_reads_every_unknown_family():
    err = "warning: unknown font family: calibri\nwarning: unknown font family: font awesome 7 free\n"
    assert font_warnings(err) == ("calibri", "font awesome 7 free")

def test_unavailable_fonts_separates_what_we_could_ship():
    seen = ("calibri", "font awesome 7 free", "pt serif")
    assert unavailable_fonts(seen) == ("calibri",)

def test_probe_rejects_a_name_that_is_not_a_package_name():
    row = probe_template("../etc", "1.0.0", root=Path("/nonexistent"))
    assert row.ok is False and row.reason == "invalid name"

def test_probe_reports_no_example_when_the_package_ships_none(tmp_path): ...
```

Plus, under a module-level `typst_available()` skipif in a second test module `tests/test_template_probe_compile.py`:

```python
def test_probe_accepts_basic_resume():
    row = probe_template("basic-resume", "0.2.9", root=package_root())
    assert row.ok is True and row.pages == 1 and row.font_warnings == ()

def test_probe_needs_inline_data_for_clean_print_cv():
    row = probe_template("clean-print-cv", "0.1.0", root=package_root())
    assert row.ok is True and row.needs_inline_data is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_template_probe.py tests/test_template_probe_compile.py -v`
Expected: the pure tests FAIL on the missing names; the compile module SKIPs here (no binary) — that skip is the correct result in this worktree.

- [ ] **Step 3: Implement the probe**

`INSTALLABLE_FONTS` is the measured set of free families the pool asks for: Font Awesome 6/7 brands, free and free solid, Source Sans 3, Source Sans Pro, Source Serif 4, Source Han Sans/Serif, PT Serif, Inria Sans, Fira Sans, EB Garamond, Libre Baskerville, Inter, Roboto, Open Sans, Montserrat, Cabin, Alegreya, Cantarell, XCharter, Linux Libertine, Linux Biolinum, Academicons, LXGW WenKai GB, Harano Aji Gothic/Mincho. Anything else warned about counts as unavailable.

`probe_template` resolves the vendor example the way `templates.template_example_source` already does (`typst.toml`'s `[template]`, then `template/main.typ|cv.typ|resume.typ`), compiles it as a single file through `typst_compile.compile_typst`, and on a "file not found" failure naming a data file retries once through `inline_data_files`. Page count comes from `pdfminer.high_level.extract_pages`, as `services/layout.py` does. `ok` is true only when it compiled; gates 2, 3 and 5 are applied by Task 3, which has the denylist.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_template_probe.py -v`
Expected: PASS (and the compile module SKIPs).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/template_probe.py apps/backend/tests/
git commit -m "feat(templates): probe a template and classify its missing fonts"
```

---

### Task 3: Survey job, committed support list, list_templates filtering

**Files:**
- Create: `apps/backend/app/jobs/survey_templates.py`
- Create: `apps/backend/data/template_support.json`
- Modify: `apps/backend/app/services/templates.py`
- Modify: `apps/backend/tests/test_templates.py`

**Interfaces:**
- Consumes: Task 2's `probe_template`, `SupportRow`, `unavailable_fonts`.
- Produces: `templates.supported_templates() -> dict[tuple[str, str], dict]` (cached read of the JSON), `templates.is_supported(name: str, version: str) -> bool`, and `DENYLIST: dict[str, str]` in the job.

- [ ] **Step 1: Write the failing tests**

```python
def test_supported_templates_returns_empty_when_the_file_is_missing(monkeypatch, tmp_path):
    # Review Focus 5: a missing list degrades to an empty picker, never a 500
    monkeypatch.setattr(templates, "SUPPORT_PATH", tmp_path / "absent.json")
    assert templates.supported_templates() == {}

def test_supported_templates_returns_empty_on_malformed_json(monkeypatch, tmp_path): ...

def test_list_templates_drops_rows_that_are_not_supported(monkeypatch): ...

def test_is_supported_matches_on_name_and_version(monkeypatch):
    # a floated version is not the supported one
    ...

def test_denylist_entries_each_carry_a_reason():
    from app.jobs.survey_templates import DENYLIST
    assert set(DENYLIST) == {
        "exp-cover-letter", "modernpro-coverletter", "hadronic-rirekisho",
        "rirekisho-ofa", "shokumu-keirekisho-ofa", "scienceicons",
    }
    assert all(reason for reason in DENYLIST.values())

def test_survey_marks_a_denylisted_package_unsupported(monkeypatch): ...
def test_survey_marks_a_template_with_any_font_warning_unsupported(monkeypatch): ...
def test_survey_marks_a_template_over_two_pages_unsupported(monkeypatch): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_templates.py -v`
Expected: FAIL on the missing `supported_templates` / `is_supported` / `DENYLIST`.

- [ ] **Step 3: Implement the loader and the job**

In `templates.py`: `SUPPORT_PATH = BACKEND_ROOT / "data" / "template_support.json"`, read once into a module cache next to `_cv_cache`, reset by the existing `reset_template_index_cache()`. Any failure to read or parse logs a warning and yields `{}`. `list_templates()` keeps only rows where `is_supported(name, version)`, and gains a `deterministic: bool` field from the Task 8 registry (hard-coded `False` until then).

In `app/jobs/survey_templates.py`, following `ingest_catalog.py`'s argparse shape: fetch the index, take `latest_cv_packages`, `prefetch_one` each, `probe_template` each, then apply the remaining gates — `ok` requires the probe compiled, `font_warnings == ()`, `pages <= 2`, a thumbnail that `fetch_registry_thumbnail` actually returns, and absence from `DENYLIST`. Write `{"generated_at", "typst_version", "rows": [...]}`. Each row keeps `reason` for a human; the loader does not serve it.

`DENYLIST` maps package → reason, exactly the six names in the test: two cover letters, three rirekisho forms, one icon library.

- [ ] **Step 4: Run them to verify they pass, then generate the list for real**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_templates.py -v`
Expected: PASS
Then, on a host with the binary and network: `cd apps/backend && .venv/bin/python -m app.jobs.survey_templates --out data/template_support.json`
Expected: around 26 rows with `ok: true`. Commit the file as generated; do not hand-edit it.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/jobs/survey_templates.py apps/backend/data/template_support.json \
        apps/backend/app/services/templates.py apps/backend/tests/test_templates.py
git commit -m "feat(templates): offer only templates a survey says can work"
```

---

### Task 4: Switch endpoint (model path), template parameter, delete the English matcher

Behaviour is unchanged for the person; the intent stops being guessed.

**Files:**
- Modify: `apps/backend/app/routers/resumes.py`
- Modify: `apps/backend/app/routers/chat.py:240-246`
- Modify: `apps/backend/app/services/templates.py` (delete `is_template_switch_message`, add `mentioned_template`)
- Modify: `apps/backend/app/schemas.py` (`ChatRequest`, new `TemplateSwitchRequest`/`TemplateSwitchOut`)
- Modify: `apps/backend/app/services/role_card.py`
- Modify: `apps/backend/tests/test_templates.py`, `apps/backend/tests/test_chat.py`
- Create: `apps/backend/tests/test_template_switch.py`

**Interfaces:**
- Consumes: Task 3's `is_supported`.
- Produces: `POST /v1/resumes/{resume_id}/template` taking `{name, version}` and returning `{"mode": "llm", "prompt": str}`; `templates.mentioned_template(message: str) -> tuple[str, str] | None`; `ChatRequest.template: TemplateRef | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_switch_rejects_a_template_not_on_the_support_list(client, ...):
    # Review Focus 5
    resp = client.post(f"/v1/resumes/{rid}/template", json={"name": "modern-cv", "version": "1.0.0"})
    assert resp.status_code == 400

def test_switch_returns_the_model_path_prompt_for_a_supported_template(client, ...):
    body = client.post(f"/v1/resumes/{rid}/template", json={"name": "gloat", "version": "0.1.0"}).json()
    assert body["mode"] == "llm"
    assert "@preview/gloat:0.1.0" in body["prompt"]

def test_switch_404s_for_a_resume_this_caller_does_not_own(client, ...): ...

def test_mentioned_template_matches_a_package_name_in_any_language(monkeypatch):
    assert mentioned_template("幫我換成 acorn-resume") == ("acorn-resume", "0.1.0")
    assert mentioned_template("switch to @preview/gloat:0.1.0") == ("gloat", "0.1.0")
    assert mentioned_template("give me something prettier") is None

def test_chat_applies_the_template_extra_when_the_request_names_one(client, ...):
    # the picker's model path sets the parameter; no English matching involved
    ...

def test_role_card_names_the_template_the_document_uses(db, ...):
    card = build_role_card(db, resume)
    assert "@preview/basic-resume:0.2.9" in card
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_template_switch.py tests/test_templates.py tests/test_chat.py -v`
Expected: FAIL — 404 on the unknown route, `ImportError` on `mentioned_template`.

- [ ] **Step 3: Implement**

Add the route to `routers/resumes.py` beside `restore_revision`, reusing `_owned_resume`. Reject an unsupported `(name, version)` with 400 before anything else. With no emitter registered — all of them, until Task 8 — return `{"mode": "llm", "prompt": apply_prompt(name, version)}`.

In `chat.py`, replace `template_switch = is_template_switch_message(message)` with `spec = req.template or mentioned_template(message)`; `template_switch` becomes `spec is not None`, and the existing `template_api_block(*spec)` call stays. Delete `is_template_switch_message` and its tests.

`mentioned_template` scans for `@preview/<name>:<version>` via the existing `PREVIEW_SPEC_RE` first, then for any supported package name as a whole-word substring, returning that row's version. Longest name first, so `basic-resume-plus` is not read as `basic-resume`.

`build_role_card` adds one line naming the current template from `parse_preview_spec(resume.typst_source)`, and says nothing when the source names no package.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/ -v`
Expected: PASS, no regressions in `test_chat.py`.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app apps/backend/tests
git commit -m "feat(templates): carry the switch target as a parameter, not a sentence"
```

---

### Task 5: Picker calls the endpoint instead of composing a chat message

**Files:**
- Modify: `apps/frontend/lib/api.ts`
- Modify: `apps/frontend/components/editor/EditorTemplatePanel.tsx`
- Modify: `apps/frontend/components/editor/EditorShell.tsx:1199-1202`

**Interfaces:**
- Consumes: Task 4's endpoint.
- Produces: `switchTemplate(resumeId: string, name: string, version: string): Promise<TemplateSwitchResult>` where `TemplateSwitchResult` is `{mode: "llm", prompt: string} | {mode: "applied", dropped: string[], degraded: string[], resume: Resume}`; `UniverseTemplate` gains `deterministic: boolean` and loses `apply_prompt`.

- [ ] **Step 1: Point the panel at the endpoint**

`onApply` passes `{name, version}` upward rather than a prompt string. `EditorShell.onApplyTemplate` awaits `switchTemplate(...)`; on `mode: "llm"` it does exactly what it does today — `setLeftTab("chat")` then `sendUserChat(prompt, null, {preferFullSource: true})`, now also passing `template: {name, version}` so the server does not have to recognise the sentence. `mode: "applied"` is unreachable until Task 12; handle it by refreshing the resume and preview, which is the same work `onRestoreHistory` already does.

- [ ] **Step 2: Verify the path by hand**

Run: `cd apps/frontend && npx tsc --noEmit && npm run lint`
Expected: clean. Then switch a template in the editor and confirm the chat turn still runs and the document changes.

- [ ] **Step 3: Commit**

```bash
git add apps/frontend/lib/api.ts apps/frontend/components/editor/
git commit -m "feat(editor): switch templates through the endpoint"
```

---

### Task 6: resume_contents table and the ResumeContent schema

**Files:**
- Modify: `apps/backend/app/models.py`
- Create: `apps/backend/alembic/versions/026_resume_contents.py`
- Create: `apps/backend/app/services/resume_content.py`
- Create: `apps/backend/tests/test_resume_content.py`

**Interfaces:**
- Produces: `models.ResumeContent`; and in `resume_content.py` the pydantic models `Contact`, `EducationEntry`, `WorkEntry`, `ProjectEntry`, `SkillGroup`, `Section`, `ResumeContent`, plus `stored_content(db, resume) -> ResumeContent | None` and `store_content(db, resume, content, *, model: str) -> ResumeContent`.

- [ ] **Step 1: Write the failing tests**

```python
def test_resume_content_round_trips_through_json():
    ...  # model_validate_json(content.model_dump_json()) == content

def test_resume_content_defaults_every_section_to_empty():
    assert ResumeContent().work == [] and ResumeContent().extras == []

def test_stored_content_returns_none_when_the_source_has_changed(db, resume):
    store_content(db, resume, ResumeContent(name="Ruby"), model="m")
    resume.typst_source = resume.typst_source + "\n// edited"
    assert stored_content(db, resume) is None

def test_stored_content_returns_the_snapshot_when_the_hash_still_matches(db, resume): ...
def test_store_content_overwrites_the_existing_row_for_a_resume(db, resume): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_resume_content.py -v`
Expected: FAIL on the missing module.

- [ ] **Step 3: Implement**

`ResumeContent` holds `name`, `contact: Contact` (email, phone, location, github, linkedin, website), `summary`, `education`, `work`, `projects`, `skills: list[SkillGroup]` and `extras: list[Section]`. `WorkEntry` is `title, company, location, start, end, bullets`; `EducationEntry` is `institution, degree, location, start, end, bullets`; `ProjectEntry` is `name, role, url, start, end, bullets`; `SkillGroup` is `category, items`; `Section` is `title, entries: list[str]`. Dates stay strings in the `YYYY-MM` / `Present` convention `llm.SYSTEM_PROMPT` enforces — a date we could not parse is better kept verbatim, as `schemas.TalentRole` already argues.

`ResumeContent` is a 1:1 table keyed on `resume_id`; `store_content` upserts and stamps `derived_from_hash = revisions.source_hash(resume.typst_source)` and `built_at`. `stored_content` returns the row's payload only when that hash still equals the live source's.

Migration `026_resume_contents` creates the table with `ON DELETE CASCADE`; no CHECK, so it is not Postgres-only.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_resume_content.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/models.py apps/backend/app/services/resume_content.py \
        apps/backend/alembic/versions/026_resume_contents.py apps/backend/tests/test_resume_content.py
git commit -m "feat(templates): store a derived content snapshot per resume"
```

---

### Task 7: Extract a ResumeContent from Typst source

Mirrors `services/profile.build_profile` — a direct provider call with a strict JSON schema, its own result dataclass, its own telemetry.

**Files:**
- Modify: `apps/backend/app/services/resume_content.py`
- Modify: `apps/backend/app/services/llm/prompts.py`
- Modify: `apps/backend/app/models.py` (`LLM_CALL_PURPOSES`)
- Create: `apps/backend/alembic/versions/027_llm_call_extract_purpose.py`
- Modify: `apps/backend/tests/test_resume_content.py`

**Interfaces:**
- Consumes: Task 6's `ResumeContent`, `store_content`.
- Produces: `ExtractResult` (`content: ResumeContent | None`, `error: str | None`, `usage: dict`, `model: str`, `source_chars: int`), `async extract_content(source: str) -> ExtractResult`, and `record_extract_spend(db, *, user, guest, resume_id, result, meter) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_extract_content_parses_a_strict_json_response(monkeypatch):
    ...  # stub the provider; assert the parsed ResumeContent

def test_extract_content_reports_an_empty_payload_as_an_error(monkeypatch):
    # Review Focus 3: a résumé with no name and no sections is a failure, not a result
    result = await extract_content(SOURCE)   # provider returns "{}"
    assert result.content is None and result.error == "empty"

def test_extract_content_reports_a_transport_failure_without_raising(monkeypatch): ...

def test_record_extract_spend_bills_a_guest_to_the_guest_column(db, ...):
    # Review Focus 1: llm_calls forbids both subject columns being set
    record_extract_spend(db, user=None, guest=guest, resume_id=r.id, result=result, meter=meter)
    row = db.query(LlmCall).one()
    assert row.purpose == "extract" and row.user_id is None and row.guest_session_id == guest.id

def test_record_extract_spend_bills_a_signed_in_user_to_the_user_column(db, ...): ...

def test_extract_purpose_is_allowed():
    assert "extract" in LLM_CALL_PURPOSES
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_resume_content.py -v`
Expected: FAIL on the missing names.

- [ ] **Step 3: Implement**

`EXTRACT_PROMPT` goes in `prompts.py`. It says: read the Typst source, return the person's content, keep dates as written, and **put any section that does not fit a named field into `extras` rather than discarding it**. The wire schema is written out by hand rather than derived from pydantic, for the reason `profile._schema` already documents (strict structured output needs every property required and `additionalProperties: false` throughout); the payload is validated against `ResumeContent` afterwards.

`extract_content` follows `build_profile`: `spec_or_passthrough(settings.pinned_chat_model() or DEFAULT_MODEL)`, one `httpx.AsyncClient` post, `ResumeContent.model_validate_json`, every failure a populated `error` and a `None` content. A payload with no name and no entries in any list is `error="empty"`.

`record_extract_spend` follows `record_build_spend` but takes `user` *and* `guest` and sets exactly one of `user_id` / `guest_session_id`, then `meter.record({... "purpose": "extract"})`. Migration `027` copies `025`'s shape verbatim, adding `'extract'` to the CHECK, Postgres-only, with a downgrade that fails loudly for the same reason.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app apps/backend/alembic apps/backend/tests
git commit -m "feat(templates): extract a content snapshot, billed as its own purpose"
```

---

### Task 8: Emitter protocol, registry, golden IR, basic-resume emitter

**Files:**
- Create: `apps/backend/app/services/emitters/__init__.py`
- Create: `apps/backend/app/services/emitters/basic_resume.py`
- Create: `apps/backend/tests/test_emitters.py`
- Create: `apps/backend/tests/fixtures/golden_content.py`

**Interfaces:**
- Consumes: Task 6's `ResumeContent`.
- Produces: `EmitResult(source, dropped, degraded)`; `Emitter` protocol with `name`, `version`, `emit(content) -> EmitResult`; `emitters.emitter_for(name: str, version: str) -> Emitter | None`; `emitters.deterministic_pairs() -> frozenset[tuple[str, str]]`; `GOLDEN` and `MINIMAL` in the fixture module.

- [ ] **Step 1: Write the failing tests**

```python
def test_emitter_for_matches_name_and_version_exactly():
    assert emitter_for("basic-resume", "0.2.9") is not None
    assert emitter_for("basic-resume", "0.3.0") is None   # a floated version falls back

def test_basic_resume_emitter_declares_no_losses():
    result = emitter_for("basic-resume", "0.2.9").emit(GOLDEN)
    assert result.dropped == () and result.degraded == ()

def test_basic_resume_emitter_pins_the_import_and_the_font():
    src = emitter_for("basic-resume", "0.2.9").emit(GOLDEN).source
    assert src.startswith('#import "@preview/basic-resume:0.2.9"')
    assert 'font: "' in src

def test_emitter_escapes_content_that_would_break_typst():
    hostile = GOLDEN.model_copy(update={"name": 'A "B" \\ C'})
    assert '\\"B\\"' in emitter_for("basic-resume", "0.2.9").emit(hostile).source

def test_emitter_omits_a_section_with_no_entries():
    # Review Focus 4: the untouched starter has sections we must not emit empty
    src = emitter_for("basic-resume", "0.2.9").emit(MINIMAL).source
    assert "== Projects" not in src and "#project(" not in src
```

And in `tests/test_emitters_compile.py`, under the `typst_available()` skipif:

```python
def test_basic_resume_emitter_output_compiles_in_one_page(): ...
def test_basic_resume_emitter_output_keeps_the_content(): 
    # import_qa.verify_render over the emitted source
    ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_emitters.py -v`
Expected: FAIL on the missing package.

- [ ] **Step 3: Implement the protocol, the registry, the fixtures and the first emitter**

`GOLDEN` carries two jobs with bullets, one degree with bullets, one project with dates, two skill groups, one `extras` section, CJK text, a `"`, a `\`, a bare `$`, `@` and `*`, and one over-long bullet. `MINIMAL` is a name and an email and nothing else — the shape of an untouched starter.

`basic_resume.py` emits `resume.with(...)` with `author`, `location`, `email`, `github`, `linkedin`, `phone`, `accent-color: "#26428b"`, `paper: "a4"`, an explicit `font`, then `== Education` / `#edu(... dates-helper ...)`, `== Experience` / `#work(...)`, `== Projects` / `#project(...)`, `== Skills`, and each `extras` section as `== <title>` with markup bullets. Values pinned by the spec and by `services/starter`: `accent-color: "#26428b"`, `paper: "a4"`. It reports nothing dropped — this template holds everything the IR has, which is what makes it the round-trip case.

`emitter_for` looks up a dict keyed `(name, version)`; `deterministic_pairs()` feeds `list_templates()`'s `deterministic` field, replacing Task 3's hard-coded `False`.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_emitters.py tests/test_templates.py -v`
Expected: PASS (compile module SKIPs here; it must pass on a host with the binary).

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/emitters apps/backend/tests
git commit -m "feat(templates): emitter registry and the basic-resume emitter"
```

---

### Task 9: ats-friendly-resume emitter

The false-friend case: it is a fork of `basic-resume` with two renamed parameters.

**Files:**
- Create: `apps/backend/app/services/emitters/ats_friendly_resume.py`
- Modify: `apps/backend/app/services/emitters/__init__.py`
- Modify: `apps/backend/tests/test_emitters.py`, `apps/backend/tests/test_emitters_compile.py`

**Interfaces:**
- Consumes: Task 8's `EmitResult`, `Emitter`, `GOLDEN`, `MINIMAL`.

- [ ] **Step 1: Write the failing tests**

```python
def test_ats_friendly_uses_dates_util_not_dates_helper():
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert "dates-util(" in src and "dates-helper(" not in src

def test_ats_friendly_passes_role_not_title():
    src = emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).source
    assert "role:" in src and "title:" not in src

def test_ats_friendly_declares_what_its_project_slot_cannot_hold():
    assert emitter_for("ats-friendly-resume", "0.1.1").emit(GOLDEN).dropped == ("projects[].role",)
```

- [ ] **Step 2: Run to verify they fail** — Run: `… -m pytest tests/test_emitters.py -k ats_friendly -v`; Expected: FAIL.

- [ ] **Step 3: Implement against the package's own source**

Read `typst/packages/preview/ats-friendly-resume/0.1.1/src/resume.typ` before writing: `work(company:, role:, dates:, tech-used:, location:)`, `project(name:, dates:, tech-used:, url:)`, `edu(institution:, location:, degree:, dates:)`, and the date helper is `dates-util`. Section order follows the vendor example: skills, experience, education, projects.

- [ ] **Step 4: Run to verify they pass** — Run: `… -m pytest tests/test_emitters.py -v`; Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/emitters apps/backend/tests
git commit -m "feat(templates): ats-friendly-resume emitter"
```

---

### Task 10: acorn-resume emitter

The lossy case, and the one that needs an explicit font.

**Files:**
- Create: `apps/backend/app/services/emitters/acorn_resume.py`
- Modify: `apps/backend/app/services/emitters/__init__.py`
- Modify: `apps/backend/tests/test_emitters.py`, `apps/backend/tests/test_emitters_compile.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_acorn_declares_its_losses_exactly():
    result = emitter_for("acorn-resume", "0.1.0").emit(GOLDEN)
    assert result.dropped == ("projects[].dates",)
    assert result.degraded == ("education[].bullets -> plain markup after #edu",)

def test_acorn_overrides_the_calibri_default():
    src = emitter_for("acorn-resume", "0.1.0").emit(GOLDEN).source
    assert 'font: "New Computer Modern"' in src

def test_acorn_always_passes_technologies_as_a_list():
    # the body calls .map on it, so omitting it raises "type none has no method map"
    assert "technologies: (" in emitter_for("acorn-resume", "0.1.0").emit(GOLDEN).source
```

And in the compile module:

```python
def test_acorn_output_has_no_missing_font_warning(): ...
```

- [ ] **Step 2: Run to verify they fail** — Expected: FAIL.

- [ ] **Step 3: Implement against the package's own source**

Read `acorn-resume/0.1.0/src/lib.typ` first. `exp(role:, date:, organization:, location:, details:)` takes bullets through `details`. `edu(degree:, date:, institution:, gpa:, location:)` has **no** `details`, so education bullets are emitted as markup after the call and reported as degraded. `project(name:, technologies:, live-url:, repo-url:, details:)` has **no** date slot — report `projects[].dates` dropped — and `technologies` must always be a list. Contacts go through `header(name:, contacts: ((url, label), …))`.

- [ ] **Step 4: Run to verify they pass** — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/emitters apps/backend/tests
git commit -m "feat(templates): acorn-resume emitter"
```

---

### Task 11: clean-print-cv emitter

The dict-shaped case: the emitter builds a data value and calls section functions. This is the check that the interface is not over-fitted to `basic-resume`.

**Files:**
- Create: `apps/backend/app/services/emitters/clean_print_cv.py`
- Modify: `apps/backend/app/services/emitters/__init__.py`
- Modify: `apps/backend/tests/test_emitters.py`, `apps/backend/tests/test_emitters_compile.py`

**Interfaces:**
- Consumes: `typst_escape.typst_value` from Task 1.

- [ ] **Step 1: Write the failing tests**

```python
def test_clean_print_cv_emits_a_data_value_and_calls_section_functions():
    src = emitter_for("clean-print-cv", "0.1.0").emit(GOLDEN).source
    assert "#let data = (" in src
    assert "#cv-header(data.personal)" in src and "#cv-experience(data.experience)" in src

def test_clean_print_cv_declares_its_losses_exactly():
    result = emitter_for("clean-print-cv", "0.1.0").emit(GOLDEN)
    assert result.dropped == ("projects[].dates",)
    assert result.degraded == (
        "projects[].bullets -> joined into description",
        "education[].bullets -> joined into details",
    )

def test_clean_print_cv_omits_section_calls_with_no_data():
    src = emitter_for("clean-print-cv", "0.1.0").emit(MINIMAL).source
    assert "#cv-certifications(" not in src
```

- [ ] **Step 2: Run to verify they fail** — Expected: FAIL.

- [ ] **Step 3: Implement**

Read `clean-print-cv/0.1.0/template/main.typ` and `cv-data.yaml` first. The data value matches that schema: `personal{name,title,email,phone,location,linkedin,github,website}`, `summary`, `skills[{category,items}]`, `experience[{role,company,location,period,highlights}]`, `projects[{name,url,description}]`, `certifications[{name,issuer,year}]`, `education[{degree,institution,location,period,details}]`, `languages[{language,level,dots}]`. `period` is `"<start> – <end>"`. `projects[].description` and `education[].details` are single strings, so bullets are joined and reported degraded; there is no project date slot. Emit `#show: cv-page-setup`, then only the section calls that have data. `extras`, `certifications` and `languages` have no IR source yet — emit nothing and report `extras` dropped when present.

- [ ] **Step 4: Run to verify they pass** — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/backend/app/services/emitters apps/backend/tests
git commit -m "feat(templates): clean-print-cv emitter"
```

---

### Task 12: Applied mode end to end

**Files:**
- Modify: `apps/backend/app/routers/resumes.py`
- Modify: `apps/backend/app/services/chat_messages.py`
- Modify: `apps/backend/tests/test_template_switch.py`
- Modify: `apps/frontend/components/editor/ChatMessageCard.tsx`
- Modify: `apps/frontend/components/editor/EditorTemplatePanel.tsx`
- Modify: `apps/frontend/messages/{en,zh-TW,zh-CN}.json`

**Interfaces:**
- Consumes: Tasks 3, 7, 8–11.
- Produces: `chat_messages.persist_template_switch(db, resume_id, *, name, version, dropped, degraded) -> ChatMessage`.

The spec listed the system row under its step 2; it moves here, because until an emitter exists there is no applied switch to record and the chat transcript already records the model path.

- [ ] **Step 1: Write the failing tests**

```python
def test_switch_applies_without_a_model_when_an_emitter_exists(client, monkeypatch, ...):
    body = client.post(f"/v1/resumes/{rid}/template",
                       json={"name": "acorn-resume", "version": "0.1.0"}).json()
    assert body["mode"] == "applied"
    assert body["dropped"] == ["projects[].dates"]
    assert "@preview/acorn-resume:0.1.0" in db.get(Resume, rid).typst_source

def test_switch_writes_a_system_row_that_never_reaches_the_model(client, db, ...):
    ...  # role == "system", content parses as JSON, and _history excludes it

def test_switch_extracts_once_and_reuses_the_snapshot(client, monkeypatch, ...):
    # two switches, one extraction
    ...

def test_switch_does_not_write_when_the_emitted_source_fails_to_compile(client, monkeypatch, ...):
    # compile_typst raises HTTPException(400); the document must be untouched
    assert body["mode"] == "llm"
    assert db.get(Resume, rid).typst_source == before

def test_switch_refuses_when_the_source_changed_after_extraction(client, monkeypatch, ...):
    # Review Focus 2: a chat edit between extraction and the write must not be clobbered
    assert body["mode"] == "llm"
    assert db.get(Resume, rid).typst_source == edited_source

def test_switch_falls_back_to_the_model_path_when_extraction_fails(client, monkeypatch, ...): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_template_switch.py -v`
Expected: FAIL — every switch still returns `mode: "llm"`.

- [ ] **Step 3: Implement the applied branch**

In the route: look up `emitter_for(name, version)`; if none, keep today's `{"mode": "llm", …}`. Otherwise `preflight(...purpose="extract")` and `async with metered(guard) as meter`, take `stored_content(db, resume)`, and on a miss `await extract_content(resume.typst_source)` → `record_extract_spend(...)` → `store_content(...)`; any error returns the model path. Then `emit()`, `compile_typst(source, "pdf")` inside a `try/except HTTPException`, **re-read the resume and compare `source_hash` against the value the snapshot was derived from** before `set_draft_source` — that is Review Focus 2 — and finally `persist_template_switch(...)`.

`persist_template_switch` follows `persist_tool_message`: a `ChatMessage` whose `content` is `json.dumps({"type": "template_switch", "name", "version", "dropped", "degraded"})` with `role="system"`.

Frontend: `ChatMessageCard` gains a `role === "system"` branch that parses the JSON and renders a one-line note naming the template, plus a sentence per `dropped`/`degraded` entry; an unparseable payload renders nothing rather than raw JSON. New keys in all three message files. `EditorTemplatePanel` shows the same note inline after an applied switch.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd apps/backend && .venv/bin/python -m pytest tests/ -v` and `cd apps/frontend && npx tsc --noEmit && npm run lint`
Expected: PASS and clean.

- [ ] **Step 5: Commit**

```bash
git add apps/backend apps/frontend
git commit -m "feat(templates): apply a curated template without a model call"
```

---

### Task 13: Verification pass

**Files:** none — this task only runs things and reports.

- [ ] **Step 1: Backend suite, including the typst-gated modules**

Run, on a host with the binary (the main checkout, not this worktree):
`cd apps/backend && .venv/bin/python -m pytest tests/ -v`
Expected: PASS with no skips in `test_emitters_compile.py` or `test_template_probe_compile.py`. A skip there on the deploy host means the binary was not found and the gates were never actually checked.

- [ ] **Step 2: Three locales and the frontend gates**

Run: `cd apps/frontend && npx tsc --noEmit && npm run lint && npm run build:public`
Expected: clean. Confirm the new keys exist in `en`, `zh-TW` and `zh-CN` — a missing key renders as the key.

- [ ] **Step 3: Browser walk, both paths**

Switch to `acorn-resume` (applied: instant, note names the dropped project dates, preview redraws) and to a long-tail template (model path: chat turn runs as before). Then send one ordinary chat message and confirm the document does **not** drift back toward `basic-resume` — that is the role-card line from Task 4 doing its job.

- [ ] **Step 4: Regenerate the support list on the deploy host and commit any diff**

Run: `cd apps/backend && .venv/bin/python -m app.jobs.survey_templates --out data/template_support.json`
Expected: the committed file is unchanged, or the diff is explained by a package that moved upstream.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore(templates): verify the deterministic switch end to end"
```
