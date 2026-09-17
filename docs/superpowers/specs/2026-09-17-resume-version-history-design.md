# Resume version history and publish

**Date:** 2026-09-17
**Status:** approved in design review

## Goal

Give every resume a **source-agnostic, coalesced document history** the owner can browse and restore, and split **draft** from **published** so `/s/{token}` stays frozen until the owner clicks **Publish**. Typing and chat edits are the same write path; revisions do not record origin.

This supersedes the share-spec decision that the public page compiles live `typst_source`.

## Non-goals

- Named versions, branches, comments, or CRDT / multi-tab merge (draft remains last-write-wins)
- Compiling historical revisions in the owner preview pane
- Freezing title, chat, or ATS with publish (title stays live on `resumes.title`)
- Changing share token rotate / revoke behavior
- Removing chat-card restore (`previous_source`)
- Frontend unit tests (the frontend has no test runner today)

## Decisions

- **Draft** is `resumes.typst_source`. Editor autosave and chat still write this column.
- **History** is `resume_revisions`: full Typst snapshots, coalesced on a 120s sliding window.
- **Published pointer** lives on `resumes.published_revision_id`, not on `resume_shares` (private deletes the share row; the frozen document must survive).
- **Public** preview, export, and OG compile the published revision’s source only.
- **First `public=true`** with a null pointer auto-pins the current draft so a new link is not empty. Later autosaves do not move the pointer.
- **Publish** is an explicit signed-in owner action that pins the current draft.
- Guests get history and restore. Publish and sharing stay behind Google sign-in.

## Schema

Alembic `008_resume_revisions` after `007_jobs_spotlight`.

`resume_revisions`:

- `id` — String(36) PK, uuid
- `resume_id` — FK `resumes.id` `ondelete=CASCADE`, indexed
- `typst_source` — Text not null
- `content_hash` — String(64) sha256 hex of source
- `created_at` — timezone-aware datetime

`resumes` gains:

- `updated_at` — timezone-aware, set when draft source changes
- `published_revision_id` — nullable FK `resume_revisions.id` `ondelete=SET NULL`

`Resume.revisions` cascade delete-orphan. Circular FK uses `use_alter` / `post_update`.

**Migration backfill:** one revision per existing resume from current `typst_source`. Every existing `resume_shares` row sets `published_revision_id` to that revision so live links do not change overnight. New creates insert an initial revision in `_new_resume`.

## Coalescing

`set_draft_source(db, resume, source, now=...)` is the only writer for draft source (PUT resume, chat `source` events, `app.jobs.typst_edit`, restore):

1. Write `typst_source` and `updated_at`.
2. If latest revision has the same `content_hash`, no-op.
3. Else if latest is **not** the published revision **and** `now - created_at < 120s`, update that row in place (source, hash, `created_at`).
4. Else insert a new revision.

A published revision is frozen. Restore copies that revision’s source into the draft through the same helper (restore does not move the published pointer).

## APIs

`ResumeOut` gains `published_revision_id`, `updated_at`, and `unpublished_changes`.

Owner (guest or user, same loader as GET resume):

- `GET /v1/resumes/{id}/revisions` → `{ id, created_at, is_published }[]` newest first, no source
- `GET /v1/resumes/{id}/revisions/{rev_id}` → includes `typst_source`
- `POST /v1/resumes/{id}/revisions/{rev_id}/restore` → sets draft, returns `ResumeOut`

Signed-in **user owner** (same 401/403 as share):

- `POST /v1/resumes/{id}/publish` → pin current draft; return share-shaped state

`GET/PUT /v1/resumes/{id}/share` also returns `published_revision_id`, `published_at`, `unpublished_changes`. Repeat `PUT public=true` while already public keeps the token and does **not** publish. Private still deletes the share row and leaves the pointer.

Public `GET /v1/shares/{token}/preview`, `/export`, `/og.png` compile published source. Share exists but pointer missing → 404.

`unpublished_changes` is true when there is no published revision, or draft hash ≠ published hash.

## Editor UI

- **History** tab after Settings: timestamp list, select shows read-only source, Restore confirms then writes draft (`skipTimers` + editor source, same as chat restore).
- Owner preview pane stays the **draft**. Public `/s/{token}` is the published snapshot.
- Header + Settings share card: **Publish** when signed-in owner and `unpublished_changes`. Copy that the public link shows the last published version.

Copy keys: `editor.*` in `en`, `zh-TW`, `zh-CN`.

## Testing

Backend pytest (`apps/backend`, PATH includes `.tools`):

- Coalesce: two PUTs 1s apart → one extra revision; PUT after 121s → another; edit after publish → new revision, published row unchanged
- Helper used by PUT and job path
- Public preview ignores a later draft PUT until publish
- First public auto-pins; existing-share backfill keeps preview source
- Restore does not change published pointer
- Guest cannot publish; signed-in non-owner 404

Verify in the browser: edit after public → share unchanged → Publish updates share; restore an old revision into the editor only.
