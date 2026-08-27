# Editor settings and public resume share

**Date:** 2026-08-27
**Status:** approved in design review

## Goal

Add a **Settings** tab in the resume editor so the owner can rename the resume and see basic metadata. Signed-in users can switch the resume between **private** and **public**. Public assigns an unguessable `/s/{token}` link. Anyone with that link can preview the live compiled resume and download a PDF. Typst source, chat, and ATS never appear on public endpoints or the public page.

## Non-goals

- Snapshot / frozen version at share time (the link always shows current source)
- Share history, revocation ledger, or “this link was revoked” copy (unknown and old tokens both **404**)
- Password, expiry, or a public gallery / sitemap listing
- IP rate-limit table for anonymous preview/export
- Admin UI for shares
- `updated_at` on resumes
- Frontend unit tests (the frontend has no test runner today)
- Product names CareerOS, RenderResume, Roleloop, Offerly, Offerloop in UI copy

## Decisions

- **Live content.** The public page compiles the current `typst_source`.
- **Token rotate on republic.** Repeat `PUT public=true` while already public keeps the same token. Private deletes the row. Private → public inserts a **new** token. Old tokens 404.
- **Active-only row.** One `resume_shares` row means public. Private deletes it. No `revoked_at`.
- **View + PDF.** Public visitors get SVG preview pages and PDF download. No Typst, chat, or ATS.
- **Settings is a fourth left tab** (after Typst, Chat, Template). Preview stays the right pane.
- **Guests** may rename and see metadata. They cannot create a share. CTA: `/login?next=/editor/{id}`.

## Authz

Owner share `GET/PUT /v1/resumes/{id}/share`:

- No Google session → **401** `Sign in required`
- Session but resume is not `user_id == current user` (guest-owned / unclaimed) → **403** `Sign in required to share`
- Non-owner → existing owner loader **404** `Resume not found`

Public `GET /v1/shares/{token}` (and `/preview`, `/export`): no auth. Missing or deleted token → **404**. Never 403.

OAuth `next`: allow `/admin`, `/admin/...`, and `/editor/{uuid}`. Reject `//`, `://`, `\`. Anything else → `/dashboard`.

Editor mount: if `getMe()` is a user, `claimResumes()` best-effort **before** `getResume`, so login return-to-editor works.

## Schema

Table `resume_shares` (Alembic `005_resume_shares`, after `004_created_at`):

- `id` — `String(36)` PK, uuid
- `resume_id` — `String(36)` FK `resumes.id` unique, `ondelete=CASCADE`
- `token` — `String(32)` unique, `secrets.token_urlsafe(16)` (~22 chars)
- `created_at` — timezone-aware datetime, default now

`Resume.share` optional one-to-one. Deleting a resume deletes its share.

## APIs

`ResumeOut` gains `created_at` (ISO-8601). PUT title: strip; empty → 400 `Title is required`; length > 255 → 400.

Owner:

- `GET /v1/resumes/{id}/share` → `{ public: bool, token: str | null }`
- `PUT /v1/resumes/{id}/share` body `{ public: bool }` → same

Backend does not bake origin. Frontend copy URL: `new URL("/s/" + token, window.location.origin).href`.

Public (no cookies):

- `GET /v1/shares/{token}` → `{ title, locale }` only (no `id`, `typst_source`, owner)
- `GET /v1/shares/{token}/preview` → `PreviewPages` (same Typst SVG pipeline as owner)
- `GET /v1/shares/{token}/export` → PDF attachment; filename from title

## Editor Settings tab

Copy keys: `editor.*` in `en`, `zh-TW`, `zh-CN`.

- Name input + Save → existing `PUT /v1/resumes/{id}` `{ title }`. Header title updates.
- Metadata: created_at (locale-formatted), source (create/upload), locale, import_status.
- Guest: sharing card + link to `/login?next=/editor/{id}`.
- Signed-in: Private / Public. Public shows URL + Copy.

## Public page

`/{locale}/s/{token}` with localePrefix `as-needed` (default zh-TW is `/s/{token}`). Copied links omit the locale prefix.

- `robots: noindex, nofollow`
- Brand link home + title + `EditorPreview` without ATS
- Download PDF; 404 copy if the token is missing
- Namespace `share.*`

No chat, Typst editor, or login wall.

## Testing

Backend pytest (`apps/backend`, PATH includes `.tools`): guest 401, unclaimed 403, token stable while public, rotate after private, old token 404, title strip/empty, `created_at` on GET, public meta/preview/export without cookies, public JSON has no source/id, OAuth `next=/editor/{uuid}` allowed and traversal/evil rejected.

Verify in the browser: guest settings, signed-in share, anonymous preview + PDF, private 404, republic new token.
