---
name: New user onboarding
overview: A first-run pass that shortens time-to-first-good-resume without adding editor chrome. Fixes the create-path cold start (raw Typst placeholders), gives the empty chat starter prompts, makes the ATS strip visible on first encounter, turns the dashboard empty state into real actions, repairs first-time auth copy, and moves the save-your-work nudge to the moment value exists. Frontend plus messages only, no migration. Task 0 establishes a baseline funnel from tables that already exist.
todos:
  - id: t0-baseline
    content: "Task 0: Baseline the first-run funnel from existing tables (read-only SQL, no migration)"
    status: completed
  - id: t1-create-cold-start
    content: "Task 1: Create path opens on chat, not raw Typst"
    status: completed
  - id: t2-chat-suggestions
    content: "Task 2: Empty chat offers starter prompts that fill the composer"
    status: completed
  - id: t3-ats-visible
    content: "Task 3: ATS strip opens itself when a check fails"
    status: completed
  - id: t4-dashboard-empty
    content: "Task 4: Dashboard empty state carries the two real actions"
    status: completed
  - id: t5-auth-copy
    content: "Task 5: Auth copy stops assuming a returning user"
    status: completed
  - id: t6-save-nudge
    content: "Task 6: Guest save nudge fires after the first real result"
    status: completed
  - id: t7-tailor-copy
    content: "Task 7: Name master vs tailored at point of use"
    status: completed
  - id: t8-verify
    content: "Task 8: Three locales, lint, tsc, build, browser walk of all three entry paths"
    status: in_progress
isProject: false
---

# New User Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A first-time visitor reaches a resume that is filled in with their own content and passing its ATS checks, without ever being told what Typst is and without being blocked by a wizard, a tour, or a signup gate.

**Architecture:** No new product surfaces. Every change is a better default, a real empty state, or copy at the point of use. The create path gets the same editor hand-off the upload path already has (`lib/pending-upload.ts`'s module-level Map). The empty chat grows an optional `suggestions` prop that fills the composer and nothing more — the user still presses send, so AI stays optional. No migration, no new table, no persisted onboarding state.

**Tech Stack:** Next 16, React 19, next-intl, Radix accordion/tabs, existing `Toaster`. Frontend and `messages/{en,zh-TW,zh-CN}.json` only.

**Spec:** [docs/superpowers/specs/2026-08-26-offerfy-design.md](../specs/2026-08-26-offerfy-design.md) (Global constraints), [docs/superpowers/specs/2026-08-27-indie-landing-design.md](../specs/2026-08-27-indie-landing-design.md)

## Global Constraints

- **No editor chrome.** The spec forbids wizards, forced analyze steps, and extra flow chrome in the editor, and that holds here. No overlays, no coachmarks, no highlighted walkthrough, no step counters, no progress checklist, no "N of 3". Permitted: default tab choice, default accordion state, empty-state content, inline copy, and the existing toast.
- **AI stays optional.** Nothing added here may gate editing on an LLM run. Suggestion chips fill the composer; they never auto-send.
- **Anonymous stays whole.** Create, upload, edit, preview, ATS report, and download must keep working with no account. The signup nudge is never a blocker and never precedes real work — the "ATS check without signup" wedge is the thing being protected.
- **Tier 1 scope.** Frontend and message files only. No Alembic migration, no new model, no new endpoint, no change to `starter.typ`.
- Locales are `en`, `zh-TW`, `zh-CN`. Every new key lands in all three `apps/frontend/messages/*.json`.
- Product name is Offerfy. Never CareerOS, Roleloop, Offerly, Offerloop, or RenderResume.
- Frontend checks: `cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build`. No frontend unit runner; UI behavior is verified in a browser.
- Out of scope: a paste-your-text intake flow (Tier 2), persisted onboarding state (Tier 3), landing-page restructuring, event-table instrumentation, and any change to the upload path's auto-import, which already works.

## Findings this plan is built on

Each was read out of the code, not assumed.

| # | Finding | Evidence |
| --- | --- | --- |
| 1 | The create path drops a first-timer onto raw Typst markup for a placeholder person. `leftTab` defaults to `typst` unless a pending upload exists. | [EditorShell.tsx:146](../../../apps/frontend/components/editor/EditorShell.tsx), [starter.typ](../../../apps/backend/typst/starter.typ) |
| 2 | The empty chat is one gray sentence, and that sentence is a disclaimer ("AI is optional") rather than an invitation. It is also only visible on the tab a create-path user did not open. | [EditorChatPanel.tsx:176](../../../apps/frontend/components/editor/EditorChatPanel.tsx), `editor.chatEmpty` |
| 3 | The ATS strip — the Phase 1 wedge — is a collapsed accordion. A new user sees "ATS parseability · Shown after compile" and the already-written `ats.meanings` copy sits two accordion levels down. | [AtsStrip.tsx:35](../../../apps/frontend/components/editor/AtsStrip.tsx) |
| 4 | The dashboard empty state is a gray paragraph in a card with no action in it; the only CTA is a small top-right button. | [dashboard/page.tsx:192](../../../apps/frontend/app/[locale]/dashboard/page.tsx) |
| 5 | The login page greets first-time users with "Welcome back" and offers to "keep your resume history" they do not have yet. OAuth then lands them on that dead-end empty dashboard. | [login/page.tsx:20](../../../apps/frontend/app/[locale]/login/page.tsx), [auth.py:153](../../../apps/backend/app/routers/auth.py) |
| 6 | Guest work rides a cookie, and the only warning is `editor.settingsSignInHint`, inside the Settings tab. A guest who clears cookies loses everything with no prior notice. | `EditorSettingsPanel.tsx`, `editor.settingsSignIn*` |
| 7 | The dashboard shows "Tailored" badges and "N tailored versions" to users who were never told what a tailored version is. | [dashboard/page.tsx](../../../apps/frontend/app/[locale]/dashboard/page.tsx), `dashboard.tailorBadge` |
| 8 | There is no first-run state anywhere — no `onboarding`/`has_seen` column on `User` or `GuestSession`, nothing in storage. Any show-once behavior would need new state, which is why this plan avoids show-once behavior entirely. | [models.py](../../../apps/backend/app/models.py) |

## What can and cannot be measured without a migration

Task 0 was chosen as instrumentation, but Tier 1 forbids backend work, so it has to be a read-only funnel over tables that already exist. That is possible, and the limits are worth stating up front rather than discovering later.

**Measurable today, no schema change:**

| Step | Source |
| --- | --- |
| Resumes created, split by entry path | `resumes.created_at`, `resumes.source` (`create` / `upload`) |
| Did the upload import land | `resumes.import_status` (`idle` / `pending` / `done` / `failed`) |
| Did the user ever talk to AI | `chat_messages` where `role = 'user'`, grouped by `resume_id` |
| Edit depth | `count(resume_revisions)` per resume |
| Guest to account conversion | `resumes.claimed_at`, `user_id` vs `guest_session_id` |
| Did they reach tailoring | `resumes.kind = 'tailor'` |
| Did they publish | `resumes.published_revision_id IS NOT NULL` |

**Not measurable without backend work — do not promise these:**

- **Landing abandonment.** No row exists until a resume is created, so `/` → `/create` or `/upload` drop-off is invisible. This is the single biggest blind spot.
- **ATS pass rate.** `ats_report` recompiles and returns on the fly and persists nothing ([resumes.py:473](../../../apps/backend/app/routers/resumes.py)). "Did the user reach a passing resume" cannot be answered from the database.
- **Exports by signed-in users.** `rate_events` is written only for guests *and* only when `app_env == "production"` ([rate_limit.py:13](../../../apps/backend/app/services/rate_limit.py)), so the admin dashboard's export rate is guest-and-prod-only. Treat it as a floor, not a count.

If landing drop-off or ATS pass rate turn out to be the numbers that matter, that is a Tier 2+ follow-up: a real event table, or ATS results persisted on the revision.

## File Structure

```
apps/frontend/
  lib/
    pending-upload.ts          # pattern to mirror (module-level Map, same-navigation handoff)
    pending-create.ts          # NEW: marks a just-created resume so the editor opens on chat
  app/[locale]/
    create/page.tsx            # stash the create marker before routing
    dashboard/page.tsx         # real empty state
    login/page.tsx             # no copy change needed; strings move
  components/editor/
    EditorShell.tsx            # leftTab default, suggestions wiring, guest nudge
    EditorChatPanel.tsx        # optional suggestions under the empty hint
    AtsStrip.tsx               # open when a check fails
  messages/
    en.json  zh-TW.json  zh-CN.json
```

## Task 0: Baseline the first-run funnel

Establishes before/after numbers so the rest of this plan can be judged instead of believed.

- [ ] Write the funnel query set against the table/column map above as a committed SQL file. There is no analytics convention in the repo yet (`ops/` is systemd units, `apps/backend/scripts/` holds one shell script), so create `apps/backend/scripts/funnel/first_run.sql` and let it be the convention.
- [ ] Cohort by `resumes.created_at` week and split every rate by `resumes.source`, since create and upload are different experiences and will move differently.
- [ ] Capture the current numbers as a dated comment block or short note so the post-change comparison has something to compare against.
- [ ] Record the three blind spots from the section above alongside the results, so nobody later reads a missing number as a zero.

**Verify:** Queries run against the dev database and return non-absurd counts. No schema change in the diff.

## Task 1: Create path opens on chat, not raw Typst

Finding 1. The highest-leverage change in this plan.

- [ ] Add `lib/pending-create.ts` mirroring `lib/pending-upload.ts`: a module-level `Map`/`Set` with `stashPendingCreate(resumeId)`, `peekPendingCreate(resumeId)`, `takePendingCreate(resumeId)`. Module-level (not `sessionStorage`) is deliberate and matches the upload pattern — this is a same-navigation handoff, and a reload should fall through to normal behavior.
- [ ] In `app/[locale]/create/page.tsx`, call `stashPendingCreate(resume.id)` before `router.push`.
- [ ] In `EditorShell.tsx:146`, extend the `leftTab` initializer so a pending create also starts on `chat`: `peekPendingUpload(id) || peekPendingCreate(id) ? "chat" : (loadEditorTab(id) ?? "typst")`.
- [ ] Clear the marker with `takePendingCreate` once consumed, so a later visit respects the user's own stored tab choice.

This is a default, not a gate: the Typst tab is one click away and nothing runs automatically. Unlike the upload path, **no prompt auto-sends** here — there is no uploaded file to import, and auto-sending would be a forced LLM run.

**Verify:** `/create` → editor opens on Chat with the composer focusable and no network call fired. Reload → the stored tab wins. `/upload` behavior is byte-for-byte unchanged.

## Task 2: Empty chat offers starter prompts

Finding 2. Turns the disclaimer into an invitation without turning it into a wizard.

- [ ] Add an optional prop to `EditorChatPanel` (`Props` at line 43): `suggestions?: { key: string; label: string; prompt: string }[]`.
- [ ] Render them in the empty branch at line 176, under `emptyHint`, as a short list of buttons. Clicking one calls `onDraftChange(prompt)` and focuses the composer. **It must not submit.** The user reads it, edits it, and presses send.
- [ ] In `EditorShell.tsx` near the existing `chatEmptyHint` (line 782), build the suggestion list and pass it at line 887. Hide suggestions entirely when `chatUnavailable`.
- [ ] Suggestions, by resume kind — three at most, no scrolling:
  - master: fill this in from my background; rewrite my bullets to be stronger; fix the failing ATS checks
  - tailor: the existing kickstart already covers this, so pass no suggestions and leave it alone
- [ ] Rewrite `editor.chatEmpty`. It currently reads as an apology. It should say what this panel does and keep the "you can also just edit the Typst yourself" reassurance as the second, quieter half.
- [ ] New keys under `editor.suggest*` in all three message files.

**Verify:** Empty chat on a freshly created master shows the hint plus three chips. Click → composer fills, nothing sends. Type over it → fine. Send one → chips disappear with the empty state and never come back. `chatNotConfigured` shows no chips.

## Task 3: ATS strip opens itself when a check fails

Finding 3. The wedge feature should not be hidden behind two clicks.

- [ ] In `AtsStrip.tsx`, drive the outer `Accordion`'s open state: open when the report has at least one failing check, collapsed when everything passes or nothing has compiled yet. Keep it `collapsible` so the user can always shut it.
- [ ] Make it controlled-but-overridable: once the user collapses it by hand, a later report must not reopen it and fight them. Local state seeded from the report, not recomputed on every render.
- [ ] Reword `ats.pending` — "Shown after compile" describes the implementation. Say what is coming and why (how ATS parsers will read the PDF). Keep `ats.disclaimer` exactly as is; the no-hireability-claim wording is a spec constraint.

**Verify:** A resume with failures opens the strip with the failing rows reachable in one click. Collapse it, trigger another compile → it stays collapsed. An all-pass resume stays collapsed. The `ats.disclaimer` text is unchanged in the diff.

## Task 4: Dashboard empty state carries the two real actions

Finding 4.

- [ ] Replace the bare `{t("empty")}` card at `dashboard/page.tsx:192` with an empty state holding both entry paths: upload an existing resume, create a new one. Reuse the `rr-card rr-choice-card` pattern from `app/[locale]/new/page.tsx` so it reads as the same product and needs no new CSS.
- [ ] Keep the heading honest and short. The existing `picker.*` strings already say this well — reuse them rather than writing near-duplicates, and add only what is genuinely new.
- [ ] Leave the top-right "New resume" button alone; it is the right control for a populated dashboard.

**Verify:** A signed-in account with no resumes sees two clickable cards that land on `/upload` and `/create`. A populated dashboard is visually unchanged.

## Task 5: Auth copy stops assuming a returning user

Finding 5. A new signup reading "Welcome back" is a plain copy bug.

- [ ] Rewrite `login.title` and `login.lead` to be true for both first-timers and returners. Neutral dual-purpose copy, no new state, no branching — a "is this a returning user" check would need state we deliberately are not adding (finding 8).
- [ ] The lead should name the actual benefit — the resumes follow the account instead of the browser — rather than "history", which means nothing before you have any.
- [ ] Leave the `legal.loginAgree` block and its terms/privacy links untouched.

**Verify:** Both locales of the login page read correctly for someone who has never signed in. `/login?next=/editor/...` still round-trips.

## Task 6: Guest save nudge fires after the first real result

Finding 6. Ordered by the chosen goal: value first, account second. This is the one place where doing it eagerly would cost the wedge, so the trigger matters more than the wording.

- [ ] Trigger on the **export/download** of a PDF by a guest. Someone who just downloaded their resume has demonstrated the product worked; that is the honest moment to mention the account.
- [ ] Deliver it through the existing `useToast` from `components/Toaster.tsx` — already in `EditorShell` and already used for deletes. One line, non-blocking, self-dismissing. No overlay, no banner, no modal.
- [ ] Fire once per editor session only, guarded by a ref (the same idempotence shape as `kickstartedRef`). Not persisted — Tier 1 adds no state, and a nudge that reappears next session is acceptable where a nagging one is not.
- [ ] Condition on `!signedIn`. A signed-in user must never see it.
- [ ] Wording: their resume lives in this browser; signing in keeps it. State the risk plainly without scaring someone out of the product.
- [ ] Leave `editor.settingsSignInHint` in Settings as is — it is correct where it sits.

**Verify:** Guest downloads a PDF → one toast, once. Download again in the same session → no toast. Signed-in user downloads → no toast. The download itself is unaffected if the toast throws.

## Task 7: Name master vs tailored at point of use

Finding 7. Point-of-use copy only — no tour, no explainer screen.

- [ ] Add a one-line description to the "New tailored version" item in `ResumeFamilyMenu`, in the shape of the existing `editor.tailorDialogHint`, which already explains this well.
- [ ] Give the dashboard one quiet line distinguishing a master from its tailored versions, shown only when the user actually has a tailored version — so it teaches on first encounter and then stops being noise.
- [ ] Do not touch `CreateTailorDialog`'s `tailorDialogHint`; it is already the clearest copy in the product.

**Verify:** The family menu item reads clearly to someone who has never tailored. The dashboard line appears only once a tailor exists and does not re-wrap the row cards.

## Task 8: Locales, checks, and a browser walk

- [ ] Every new key present in `messages/en.json`, `messages/zh-TW.json`, and `messages/zh-CN.json` with no placeholder English left in the Chinese files. zh-TW is the default locale, so it is not the afterthought.
- [ ] Diff the three files' key sets against each other and confirm they match exactly.
- [ ] `cd apps/frontend && npx eslint . && npx tsc --noEmit && npm run build`.
- [ ] `cd apps/backend && PATH="$PWD/.tools:$PATH" .venv/bin/pytest -q` — expected untouched, run to prove it.
- [ ] Browser walk, all three entry paths, in `en` and `zh-TW`:
  1. Landing → Upload existing → drop a PDF → auto-import still fires, editor on Chat.
  2. Landing → or create one → name it → editor on **Chat** with suggestions; click one, edit it, send; confirm an edit applies and the ATS strip opens on failures; download as a guest and get exactly one nudge.
  3. Nav sign-in → login copy reads right for a first-timer → OAuth → dashboard empty state with two working cards.
- [ ] Re-run Task 0's queries after a few days of real traffic and compare against the captured baseline.

## Risks

- **Defaulting create to Chat could read as pushing AI** on a product whose spec says AI is optional. Mitigated by auto-sending nothing and leaving the Typst tab one click away — but it is the judgment call in this plan most worth a second look.
- **Suggestion chips are the thin end of wizard chrome.** The line held here: they fill a text box and have no order, no state, and no progress. If they later grow steps or persistence, the no-chrome constraint has been broken.
- **The ATS strip opening itself is a layout change** in a resizable split. Check it does not squeeze the preview on short viewports.
- **Task 0 cannot see the landing page**, so a large drop-off before any resume exists would stay invisible and this whole plan could look ineffective while actually working. Say so when reporting the numbers.


## Verification record (2026-10-19)

**Automated checks, all green on the final tree:**

- `npx tsc --noEmit` — clean.
- `npx eslint .` — clean.
- `npm run build` — succeeds; all three locales prerender.
- Locale parity — 386 keys in each of `en` / `zh-TW` / `zh-CN`, sets identical, no English left in either Chinese file.
- `pytest -q` — 199 passed, 47 skipped, **1 pre-existing failure** unrelated to this work:
  `test_chat_template_switch_includes_package_example` expects `#exp(` in the system
  prompt for the `acorn-resume` template, which is not vendored in this worktree.
  No backend `app/` code was touched by this change (`git status apps/backend/app/`
  is empty). The 47 skips are the Typst golden tests; the CLI is not installed here.

**Runtime checks:**

- Task 0's SQL ran end to end against the live dev Postgres. All nine queries
  execute. The baseline is recorded in the file's footer.
- Query 6 initially returned negative durations. Root cause was not the query:
  73 of 246 resumes (30%) share one `created_at` from a backfill, so their chat
  turns predate their own resume row. Query 0 now surfaces that cluster and
  query 6 excludes it. Worth knowing because the backfilled rows all land in the
  2026-08-24 cohort, which otherwise looks like the best-converting week.
- `next start` + the live backend on `127.0.0.1:8001` confirmed the Task 5 copy
  renders in all three locales: `Sign in to Offerfy` / `登入 Offerfy` /
  `登录 Offerfy`, and "Welcome back" / 歡迎回來 / 欢迎回来 are gone.

**Not verified — needs a browser on a machine that can reach the dev server.**
The only connected browser is on macOS and cannot reach this Linux host
(inbound blocked), so the interactive walk did not run. These rest on code
review and the type checker alone:

1. `/create` → editor actually opens on the Chat tab (Task 1).
2. Suggestion buttons render, and clicking one fills the composer without sending (Task 2).
3. The ATS strip opens on a failing report and stays collapsed after a manual collapse (Task 3).
4. The dashboard empty state's two cards (Task 4).
5. The guest download nudge firing exactly once (Task 6).
6. The family-menu hint and dashboard explainer line (Task 7).

To walk it: `npm run build && npx next start -p 3100` in `apps/frontend` with the
backend already up on `127.0.0.1:8001`, then visit `/en/create`.
