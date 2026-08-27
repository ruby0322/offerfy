# Offerfy landing: workshop you’ll reopen

**Date:** 2026-08-28
**Status:** approved; implemented
**Supersedes:** `docs/superpowers/specs/2026-08-27-indie-landing-design.md` for `/` only

## Goal

Rebuild `/` so a job seeker believes this is an AI resume **editor they will reopen**, not a generate-and-download mill and not a job board. Desktop width proves it: copy on the left, an honest editor mock on the right that plays a small `apply_typst_edit` loop. Typst is visible in the mock and explained in one civilian paragraph after the hero. Guest use is a single sentence in the hero sub, not a third button.

Product truth unchanged: editor ships now; search, tailor, apply, tracking are Next. `/create`, `/upload`, `/login`, editor, dashboard do not change.

## Non-goals

- Product Hunt badge, waitlist, logo cloud, testimonials, FAQ
- Real `EditorShell` (needs a resume id and the API)
- Video / GIF / screenshot pipeline
- Three-column fake UI (source | preview | chat at once)
- Invented metrics, fake personal names, 「主履歷」 / “master resume” as marketing
- Claiming “first Typst-based AI resume editor” on the page
- Navy/teal pitch tokens
- Changing `/create`, `/upload`, `/editor`, dashboard, or auth

## Audience and promise

- **Audience:** job seekers (zh-TW / en / zh-CN). Not HN-first.
- **Hero promise:** the editor you’ll reopen and keep using. Typst is not in the headline.
- **Proof:** workshop, not wizard — same file, Typst + preview + chat that applies a diff.
- **Anti-slop:** said in the hero sub and again below the fold. No screening theater, no hireability scores.

## Visual system

Keep the paper tokens, Fraunces display, Source Sans 3, clay kicker mark, and light/dark from the indie landing. No glow, no gradients, no pill CTAs.

Hero is no longer a `max-w-[40rem]` column. From `md` up: CSS grid ~`0.9fr / 1.25fr`, page max ~`72rem`. Type on the left still wraps like a sentence (`h1` max ~`14–18ch`). Notes + roadmap stay ~`40–44rem` centered. Mobile: single column, copy then mock.

## Page composition

```
Nav (landing)
Hero
  left: kicker, h1, sub (includes guest hint), Create + upload
  right: EditorMock (honest chrome + loop)
Notes
  Typst in one breath
  anti-slop paragraph
Roadmap
  Now | Next
  ATS footnote
Footer (landing)
```

Phone: same sections stacked. `prefers-reduced-motion: reduce` freezes the mock on the after-state (Typst tab + preview with the strong bullet).

**Delete from `/`:** decorative `ResumeSheet`, three execution-gap `Reasons`.

## Hero copy

Guest hint is the **last sentence of `sub`**, above the buttons. Not a caption under the CTAs.

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| kicker | 同一份履歷，繼續改 | Same resume. Keep editing. | 同一份简历，继续改 |
| headline | 一個你會一直回來用的 AI 編輯器 | The AI editor you’ll reopen and keep using. | 一个你会一直回来用的 AI 编辑器 |
| sub | 對話裡改的是這份履歷，PDF 會一起更新。不是產生完就丟。不用登入。 | Chat edits this file. The PDF updates. Not a generate-and-download. No account needed. | 对话里改的是这份简历，PDF 会一起更新。不是生成完就扔。不用登录。 |
| ctaCreate | 建立履歷 | Create resume | 创建简历 |
| ctaUpload | 上傳一份 | or upload one | 上传一份 |

Create → `/create`. Upload → `/upload`. No login wall.

### Meta

| Locale | description |
| --- | --- |
| zh-TW | 一個你會一直回來用的 AI 編輯器。對話裡改履歷，不用登入。 |
| en | The AI editor you’ll reopen and keep using. Chat edits this file. No account needed. |
| zh-CN | 一个你会一直回来用的 AI 编辑器。对话里改简历，不用登录。 |

## Editor mock

New client component, `aria-hidden="true"`. Looks like the real editor: header (Offerfy wordmark + document title **Resume** / **履歷** / **简历** — not a person’s name), tabs Typst / Chat / Template, left pane, right preview. Tabs use the same labels as the editor (`editor.tabTypst`, `editor.tabChat`, `editor.tabTemplate`).

Not a screenshot. Not `EditorShell`. No CodeMirror.

### Demo Typst (English on every locale)

Crop the source pane so `#import` and `#show: resume.with` never appear. Real `basic-resume` `#work` form from `apps/backend/typst/starter.typ`:

```typ
== Experience

#work(
  title: "Software Engineer",
  company: "Offerfy",
  dates: dates-helper(start-date: "Jan 2025", end-date: "Present"),
)
- worked on the resume editor
- added ATS checks on the compiled PDF — parseability, not hireability
```

After the AI edit, only the first bullet changes:

```typ
- built a Typst resume editor with live PDF preview and chat that edits the same file
```

Preview matches that file (English body on every locale — it is the compiled page, not a translation). Layout matches `#work`: **Software Engineer** top-left, **Jan 2025 — Present** top-right, **Offerfy** under the title. Section label EXPERIENCE / 經歷 / 经历. No fake person in the preview crop.

Chat prompt **does** follow locale:

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| chatPrompt | 把 Offerfy 的第一則經歷寫具體一點。 | Tighten the first Offerfy bullet. | 把 Offerfy 的第一条经历写具体一点。 |

Diff in the chat pane is the English Typst lines (same as source), minus/plus colors like `EditDiff`. Before/after bullets are **constants in `EditorMock`**, not message keys — do not translate them.

### Loop (~8s, then repeat)

1. **0–2.5s — Chat tab.** User prompt. Then a tiny diff: `- worked on the resume editor` / `+ built a Typst resume editor with live PDF preview and chat that edits the same file`.
2. **2.5–5s — Typst tab.** Source crop as above; the new bullet highlighted.
3. **5–8s — Preview.** After-state bullet visible; second ATS bullet unchanged.

Phone plays the same three beats at full width under the copy.

## Notes (below the fold)

No section titles if the type can stand alone. Two short paragraphs, then roadmap.

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| typst | PDF 用 Typst 排，有點像更好上手的 LaTeX。原始檔看得到、改得到。 | Typst is how the PDF is typeset — a calmer LaTeX. You see the file, not a black box. | PDF 用 Typst 排，有点像更好上手的 LaTeX。源文件看得到、改得到。 |
| antiSlop | 這不是產生完就下載的那種。同一份檔，你會一直回來改。 | This is not another generate-and-download resume. Same file. You come back. | 这不是生成完就下载的那种。同一份文件，你会一直回来改。 |

## Roadmap

Keep the current Now / Next items and ATS footnote (`landing.roadmap.*`). Hero already hints guest use; `nowAnon` still explains Google is how history is saved. Do not add peach pills.

## Files

Create:

- `apps/frontend/components/landing/EditorMock.tsx` — chrome + loop
- `apps/frontend/components/landing/Notes.tsx` — Typst + anti-slop

Modify:

- `apps/frontend/components/landing/Hero.tsx` — split / stack; drop `ResumeSheet`
- `apps/frontend/app/[locale]/page.tsx` — `Hero`, `Notes`, `Roadmap`
- `apps/frontend/app/[locale]/globals.css` — hero grid, mock, highlight, reduced-motion freeze
- `apps/frontend/messages/{en,zh-TW,zh-CN}.json` — keys above; delete `landing.sheet`, `landing.reasons`; add `landing.notes`, `landing.mock` (`chatPrompt`, `docTitle`)

Delete after the new page compiles:

- `apps/frontend/components/landing/ResumeSheet.tsx`
- `apps/frontend/components/landing/Reasons.tsx`

## Constraints (still in force)

- UI never says CareerOS, RenderResume, or Roleloop
- ATS copy is parseability only; no hireability claim
- Anonymous create/upload; Google for history
- CTAs skip `/new`

## Verification

No existing landing tests. Verify in the browser:

1. `/`, `/en`, `/zh-CN` — hero copy, notes, roadmap, mock prompt locale, Typst source still English
2. Desktop split; ~375px stacked copy then mock; loop runs
3. Light and dark — paper / reading room, not navy
4. `prefers-reduced-motion: reduce` — after-state, no tab flicker
5. Create → `/create`, upload → `/upload`, Log in → `/login`, no auth on create
6. Locale and theme switchers still work
7. `/create` and `/editor` still look like the app shell

## Decision log

- Job-seeker audience; Typst out of the headline, covered in mock + one paragraph
- Split hero, honest editor chrome, not a three-pane diagram, not a screenshot
- Tab-switch loop + real tiny diff, not a preview morph
- Offerfy as demo employer; crop to Experience; no invented metrics
- Guest hint in the sub: en **No account needed.**
- Drop execution-gap reasons; keep slim Now/Next
- Mobile plays the same loop
