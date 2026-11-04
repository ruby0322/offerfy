# Making resume import legible while it runs

**Date:** 2026-11-04
**Status:** approved — not implemented
**Touches:** backend `app/services/import_run.py` (new),
`app/services/import_tools.py` (new), `app/routers/resumes.py`,
`app/services/llm/prompts.py` (EDIT_TOOL), `app/models.py`, one alembic
revision; frontend `components/editor/ImportProgressCard.tsx` (new),
`lib/import-progress.ts` (new), `components/editor/EditorShell.tsx`,
`lib/api.ts`, `app/[locale]/upload/page.tsx`, `messages/{en,zh-TW,zh-CN}.json`

## The finding

Users report that import "feels stuck", so they refresh. Refreshing destroys the
import permanently. Both halves are in the code:

1. The uploaded `File` is carried to the editor in a module-level `Map`
   (`lib/pending-upload.ts`). It is not persisted anywhere the client can reach
   after a reload. A refresh between upload and the first chat turn leaves the
   person on `generate_starter()` with no file, no retry, and no explanation.
2. Nothing reports progress. The import is one `apply_typst_edit` call carrying
   the full source (`importFillPrompt` instructs exactly that), so for the whole
   wait the only visible activity is Typst markup streaming through the chat
   panel.

`resumes.import_status` deserves its own note, because it is a half-built
feature rather than a missing one. A CHECK constraint admits
`idle` / `pending` / `done` / `failed` (`app/models.py`), and four surfaces
already render them — `components/editor/EditorSettingsPanel.tsx` and the three
admin pages under `app/[locale]/admin/` for users, resumes and a single resume.
But the only value the backend ever writes is `"idle"` (`routers/resumes.py`,
plus the column default). The states are dead and their readers are live, so
driving the column correctly repairs those four surfaces as a side effect of
this work rather than as extra work.

A third wart falls out of the same place: `importFillPrompt` is sent as a
**user** message, so the transcript opens with "Fill the basic-resume template
from this uploaded resume. Call read_typst, then apply_typst_edit with the full
source…" looking as though the person typed it.

## Why a nicer spinner is not the fix

The wait is real — it is model round-trips, and no animation shortens it. Two
measurements decide the shape of the answer.

**A Typst compile costs 0.12s.** Measured on abbey 2026-11-04, five runs against
`apps/backend/typst/starter.typ` through the pinned binary
(`apps/backend/.tools/typst`, 0.15.1) with the real `--package-path` and
`--font-path`: 0.12s, 0.12s, 0.12s for SVG and 0.12s, 0.13s for PDF. So
rebuilding the document after every section is free. Whatever we show can be
backed by a genuinely recompiled page rather than a placeholder.

**The whole wait is therefore LLM rounds.** Which means the only honest way to
make the wait legible is to make the model's own progress visible — and the only
way to make the wait survivable is to stop keeping its state in the browser.

## Decisions taken

- Sections are **chosen and named by the model**, never enumerated in our code. A
  CV with no education, or with publications, military service and
  certifications, must produce its own list. Enumeration was rejected explicitly:
  it guarantees edge cases.
- The model **declares its plan first**, so the card can show a denominator
  ("4 of 7") and a visible next step. The denominator is the part that stops
  people refreshing; steps that only appear as they land give no sense of
  remaining work.
- The model edits **one section at a time**, so the preview fills in
  progressively rather than appearing at once.
- Progress renders as a **card in the chat**, not a separate loading page or a
  split screen. Tool messages continue to appear above it; the card is **pinned
  last in the turn**.
- The import runs as a **task in the API process** with plan and per-step
  progress committed to Postgres, and a stream that tails those rows. A refresh
  re-attaches. A deploy does not — that case is surfaced and resumable rather
  than hidden.

## The data model, and why snapshots rather than deltas

Two tables.

`resume_imports` — one row per run: `id`, `resume_id`, `status`
(`running` / `done` / `failed`), `file_key` (the existing `upload_s3_key`),
`error`, `version` (integer), `heartbeat_at`, `created_at`.

`resume_import_steps` — `import_id`, `seq`, `key`, `label`, `status`
(`pending` / `active` / `done` / `skipped`), `detail`.

`resumes.import_status` keeps its existing values and constraint; this design
simply starts writing the three that were never produced, which is what makes
its existing readers correct.

`version` is the mechanism. It lives on `resume_imports`, and **every write to
either table bumps the parent row's `version` in the same transaction** — a step
update that does not bump it is the one bug that can freeze the card while the
resume keeps changing. The stream endpoint polls and emits a **complete
snapshot** whenever it changes. With a
bounded step list the payload is a few hundred bytes, so there is no delta
protocol to get wrong, no replay buffer to size, and a client that attaches late
or re-attaches after a refresh is correct from its very first frame.

This is not a style preference. `offerfy-api` runs uvicorn with **`--workers 2`**
(`docs/deploy.md`, service table). A reconnecting client can land on the other
worker, which shares no memory with the one running the import. Postgres is the
only state both workers can see, so in-memory progress is not an option and a
snapshot read from the database is the simplest thing that is actually correct.

`heartbeat_at` is bumped on every write. A row still `running` whose heartbeat is
older than 60s means the task died — the API restarted under it — and the card
offers Resume instead of spinning forever.

## How the model declares and advances its own steps

One import-only tool, passed through `extra_tools` so the general chat tool
surface is untouched. `app/services/family_tools.py` is the existing precedent
for per-turn tools, and `app/services/llm/tools.py` documents that adding a tool
is meant to be a single edit in one place.

```
declare_import_plan(steps: [{key, label}])
```

Called once, with the sections the model found in the upload. The handler writes
`resume_import_steps` and bumps `version`.

Advancing them must not cost a round each — the budget cannot afford it (below).
So `EDIT_TOOL` gains one **optional** `step` property: each `apply_typst_edit`
names the step it is fulfilling. The relay marks that step `done`, the next
`active`, and bumps `version`. Outside an import the property is simply never
set, and no existing behaviour changes.

Per-section editing needs no change to the edit tool itself.
`apply_typst_edit` already falls through to search-and-replace when `source` is
absent (`app/typst_edit.py`), so one turn can mix a full-source first write with
small appends afterwards. `prefer_full_source` is `False` for the import.

### Drift rules

These are the whole reason model-chosen steps are safe. All four are reducer
behaviour, specified so they cannot be improvised later:

| Situation | Behaviour |
|---|---|
| Edit names a step not in the plan | Append it, mark `done`. The model found something mid-way. |
| Planned step never edited | Mark `skipped` at completion. Never blocks finishing. |
| Plan declared twice | Replace the step list, preserving status for keys that match. |
| **Plan never declared** | Card degrades to steps-as-they-land. An import must never fail for want of a plan. |

The last row is load-bearing: the plan is an affordance, not a dependency.

## Round budget

`declare_import_plan` + `read_typst` + N edits + a closing message is `N + 3`
rounds. A seven-section CV needs ten.

Today `max_rounds` is **8** whenever `prefer_full_source or verify_compile`
(`app/services/llm/turn.py`), and `timeout` is 90s without
`prefer_full_source`. Both would truncate a real import mid-document. The import
call site therefore sets its own: `max_rounds` 14 and a longer timeout, with
slack for a compile-repair retry. At 0.12s per compile the extra
`verify_compile` work this implies is immaterial.

## The card

**Placement.** In flight it renders from stream state, positioned after the last
message in the transcript, so tool messages append above it and it stays last.

After a reload it cannot be a message in stream order, because the plan call
happens *first* and the card would surface at the top of the turn. Instead it is
rebuilt from `resume_imports` plus its steps and rendered after every message
with `created_at >= resume_imports.created_at`. That timestamp is the turn
boundary. `chat_messages` has no turn id (`id`, `resume_id`, `role`, `content`,
`created_at`) and does not need one.

This also removes the fake user message: the server owns the prompt, so an
import writes **no user row at all**.

**States.**

- `running` — step list, `n of N`, elapsed time, progress bar.
- `done` — collapses to one line with the count; the original file stays
  attached to the step in history.
- `failed` — what failed, which steps were saved, an explicit assurance that
  nothing the person had was lost, and Retry, which starts a fresh import from
  the same `file_key`.
- `stale` — `running` with a heartbeat older than 60s. Says the server restarted,
  names how many sections were saved, offers Resume.

**Progressive preview.** When a snapshot shows the source changed, the editor
runs the same `refreshPreview()` + `compileResume()` + `refreshAts()` sequence
the chat handler already runs on a `source` frame
(`components/editor/EditorShell.tsx`). That trio is extracted into one function
and called from both places rather than duplicated.

## What this does not include

- No change to the upload intake surface. Paste, URL import and the merged
  `/import` route are a separate conversation.
- No `pdf.js`. Nothing renders the person's original file; the big blurred
  side-by-side was dropped when progress moved into the chat.
- No durable worker, no Redis, no queue. A deploy still interrupts an import;
  the `stale` state makes that visible and recoverable instead of silent.
- No sub-progress inside a step. A step is atomic; the spinner and the elapsed
  counter carry a slow one.

## Constraints honoured

- **No new services.** The host runs `offerfy-web` and `offerfy-api` under
  systemd and nothing else.
- **The k8s and Argo material is a decoy.** `workflows/typst-edit.yaml` declares
  an `offerfy-typst-edit` WorkflowTemplate and `app/jobs/typst_edit.py` calls
  itself its entrypoint, but `kubectl get wf` answers *"the server doesn't have a
  resource type wf"* — Argo is not installed. `app/jobs/` is four CLI scripts,
  not a job runner. There is no celery, arq, rq, dramatiq, APScheduler,
  `BackgroundTasks` or `asyncio.create_task` anywhere in the backend. Nothing in
  this design may be built on the assumption that a runner exists.
- **Two uvicorn workers.** Shared state lives in Postgres only.
- **Migrations have no test coverage** and the only live Postgres is production.
  The revision must be verified against a throwaway database before it goes near
  the host.
- **Alembic revisions collide across branches.** Other sessions merge to `main`
  concurrently, so `alembic heads` must be re-checked at merge time and the
  `down_revision` re-pointed if it moved.

## Testing

`tests/openai_script.py` provides `tool_call()`, `llm_message()` and
`ScriptedOpenAI`, so the whole loop is deterministic without a model.

- Scripted end-to-end import: plan → `read_typst` → seven labelled edits →
  close. Asserts step rows, final statuses, `version` monotonicity, and that the
  source accumulated section by section rather than in one write.
- One test per drift rule, including an import that never declares a plan and
  still reaches `done`.
- `apply_typst_edit` with and without `step` produces identical source, so the
  new property cannot change edit behaviour.
- Stream: a second client attaching mid-import is correct on its first frame; a
  client that disconnects and re-attaches resumes without gaps.
- Stale detection: a `running` row with an old `heartbeat_at` reports `stale`.
- Frontend: the card renders from a snapshot, stays last in the turn with tool
  messages above it, and survives a remount.

## Seeing it

An animated mockup of all five states lives in
`.superpowers/brainstorm/*/content/import-animated.html` (gitignored). It shows
the running sequence, the no-plan fallback, `failed`, `stale` and `done` against
the real editor layout.

## Where to look if this misbehaves

- Card at the top of the turn after a reload → the `created_at >= import.created_at`
  grouping, not the card.
- Progress frozen but the resume still changing → a write that mutated a step
  without bumping `version`.
- Import visible in one tab and not another → something cached progress in
  process memory instead of reading Postgres; see the two-worker note.
- Import ends mid-document with no error → the round budget, not the model.
