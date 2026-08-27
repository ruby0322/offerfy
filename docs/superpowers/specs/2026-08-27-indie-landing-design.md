# Offerfy indie landing page

**Date:** 2026-08-27
**Status:** approved in design review; supersedes the Landing `/` section in `docs/superpowers/specs/2026-08-26-offerfy-design.md`

## Goal

Refactor `/` into a tight indie Product Hunt page: warm paper, one intimate headline, a floating resume sheet, three short reasons, an honest Now/Next. Promising without overselling. Neat without looking like a SaaS template.

The product truth stays: resume editor ships now; search, tailor, apply, and tracking are coming. App routes after CTA (`/create`, `/upload`, `/login`, editor) do not change visually.

## Non-goals

- Product Hunt badge, waitlist, logo cloud, testimonials, FAQ
- Fake editor chrome or a product screenshot of Typst/preview
- Interest grades, five-step journey, Search → Enhance → Tailor → Apply loop diagram
- Navy/teal pitch tokens on landing
- Repeating 「主履歷」/ "master resume" as marketing copy
- Changing `/create`, `/upload`, `/editor`, dashboard, or auth flows

## Visual system

Landing follows `next-themes` (`class` on `<html>`). Same composition in both themes.

### Light (cream paper)

| Token | Value | Use |
| --- | --- | --- |
| `--landing-paper` | `#F6F1E8` | Page background |
| `--landing-ink` | `#1C1914` | Headlines, body |
| `--landing-muted` | `#5C564E` | Subcopy, reasons, footer |
| `--landing-clay` | `#A35C3A` | Kicker, accent mark |
| `--landing-sheet` | `#FFFbf7` | Resume sheet fill |
| `--landing-rule` | `#E3D8C8` | Sheet and nav hairlines |
| `--landing-cta` | `#1C1914` | Primary button fill |
| `--landing-cta-ink` | `#F6F1E8` | Primary button text |

### Dark (warm reading room)

Not the old navy pitch. Warm charcoal, cream type, same clay.

| Token | Value | Use |
| --- | --- | --- |
| `--landing-paper` | `#161411` | Page background |
| `--landing-ink` | `#F3EDE4` | Headlines, body |
| `--landing-muted` | `#A39A8E` | Subcopy |
| `--landing-clay` | `#C47854` | Kicker (slightly brighter) |
| `--landing-sheet` | `#221E1A` | Resume sheet fill |
| `--landing-rule` | `#3A342E` | Hairlines |
| `--landing-cta` | `#F3EDE4` | Primary button fill |
| `--landing-cta-ink` | `#161411` | Primary button text |

No glow, no gradients, no pill buttons. Primary CTA radius `0.5rem`. Resume sheet: 1px rule, no drop shadow (border only). Accent is a 6px clay square, not a teal dot.

### Type

Loaded with `next/font/google`, scoped to `.landing-page` so the app shell stays system/sans.

- Display: **Fraunces** (`opsz` 144, `SOFT` 50 if the Google axis is available; otherwise defaults). Hero h1 and section labels.
- UI: **Source Sans 3** for nav, body, reasons, buttons, roadmap.

Hero h1: ~3rem mobile / ~4.25rem desktop, `font-weight: 500`, tight leading, max-width ~18ch so it breaks like a sentence, not a slogan.

## Page composition

```
Nav
Hero (kicker, h1, sub, CTA row, resume sheet)
Reasons (3 sentences, one row → stack on small screens)
Roadmap (Now | Next + ATS note)
Footer
```

Max width `40rem` for type; resume sheet and reasons may go `44rem`. More whitespace than the current `max-w-6xl` pitch. Section padding `py-16` / `py-24` desktop.

### Nav

Offerfy wordmark (Source Sans, not serif) → `/`. Locale switcher. Theme switcher. **Log in** → `/login` (use existing `nav.login`; do not show `nav.google` on landing). Hairline bottom using `--landing-rule`. Transparent / paper background, not a dark bar.

### Hero

1. Kicker: clay, small caps / tracked, with the 6px clay square
2. Intimate headline (Fraunces)
3. One-sentence sub
4. Primary button **Create resume** → `/create`. Adjacent text link **or upload one** → `/upload` (not a second filled button)
5. Resume sheet below: decorative, `aria-hidden="true"`. Schematic name bar + two blocks labeled from i18n (`Experience` / `經歷` / `经历`, `Education` / `學歷` / `学历`). Gray rules for lines, **no fake personal names or company names**. Slight rotation at most 1.2deg. Looks like a piece of paper, not a browser window.

No loop nodes. No microcopy paragraph under the CTAs — the sub already covers coming-soon.

### Reasons

No section title, or a quiet Fraunces label if the layout needs a beat (`Why this` is too SaaS — prefer no heading). Three sentences in a row, separated by hairlines. Not cards. Not "Fragmented flow / Switching cost / Unstable apply quality".

### Roadmap

Two columns: **Now** and **Next**. Plain lists. Quiet "coming" is implied by being in Next — no peach pills on every row. ATS disclaimer as one muted sentence under the grid.

Drop: five steps, interest grades, How it works lead.

### Footer

Tagline + locale + theme + Log in. Same paper/ink system. Footer login uses `footer.login` (new key, same strings as `nav.login`). Shared `footer.tagline` updates for landing and app so we do not keep 「主履歷」in the chrome.

## Copy

Do not repeat 主履歷 / 主简历 / master resume on this page or in `meta` / `footer.tagline`. Say 履歷 / 简历 / resume.

### Meta

| Locale | description |
| --- | --- |
| zh-TW | 一份真的能拿去投的履歷。搜尋、客製、投遞還在路上。 |
| en | A resume that can actually go to work. Search, tailor, and apply are on the way. |
| zh-CN | 一份真的能拿去投的简历。搜索、定制、投递还在路上。 |

### Hero

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| kicker | 現在可做 | Now shipping | 现在可做 |
| headline | 一份真的能拿去投的履歷。 | A resume that can actually go to work. | 一份真的能拿去投的简历。 |
| sub | 搜尋、客製、投遞還在路上。今天先從履歷開始。 | Search, tailor, and apply are on the way. Start with the resume today. | 搜索、定制、投递还在路上。今天先从简历开始。 |
| ctaCreate | 建立履歷 | Create resume | 创建简历 |
| ctaUpload | 上傳一份 | or upload one | 上传一份 |

Delete `landing.hero.microcopy` and `loopSearch` / `loopEnhance` / `loopTailor` / `loopApply`.

### Resume sheet labels

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| experience | 經歷 | Experience | 经历 |
| education | 學歷 | Education | 学历 |

### Reasons (`landing.reasons`)

No titles. Three bodies:

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| one | 職缺一直都在。卡住的是從看到到送出。 | Jobs aren't missing. Getting from found to sent is. | 职位一直都在。卡住的是从看到到送出。 |
| two | 搜尋、改履歷、投遞散在一堆分頁裡，每次都重來。 | Search, rewrite, and apply live in a pile of tabs. Every hop starts over. | 搜索、改简历、投递散在一堆标签页里，每次都重来。 |
| three | 趕的時候，投出去的品質會掉。 | When you rush, what you send gets worse. | 赶的时候，投出去的品质会掉。 |

### Roadmap (`landing.roadmap`)

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| nowTitle | 現在 | Now | 现在 |
| nextTitle | 接下來 | Next | 接下来 |
| nowEditor | 建立或上傳履歷，立刻進入編輯器 | Create or upload a resume and open the editor immediately | 创建或上传简历，立刻进入编辑器 |
| nowAts | 檢查編譯出的 PDF 能不能被 ATS 解析 | Checks whether the compiled PDF is readable by ATS parsers | 检查编译出的 PDF 能不能被 ATS 解析 |
| nowAnon | 可匿名使用；用 Google 登入才會保存紀錄 | Works anonymously; Google saves your history | 可匿名使用；用 Google 登录才会保存记录 |
| nextSearch | 搜尋與配對職缺 | Search and match jobs | 搜索与匹配职位 |
| nextTailor | 依職缺客製 | Tailor to the job | 按职位定制 |
| nextApply | 投遞與追蹤 | Apply and track | 投递与追踪 |
| nextAb | 用結果回饋履歷 | Feed outcomes back into the resume | 用结果回馈简历 |
| atsNote | ATS 只檢查編譯出的 PDF 能不能被解析，不預測錄取。 | ATS checks parseability of the compiled PDF. It does not predict hireability. | ATS 只检查编译出的 PDF 能不能被解析，不预测录取。 |

Delete the old `landing.problem`, `landing.how`, `landing.nowNext` trees.

### Nav / footer

Landing auth label is Log in / 登入 / 登录. Keep `nav.google` for any remaining app use; landing must not read it.

| Key | zh-TW | en | zh-CN |
| --- | --- | --- | --- |
| footer.tagline | Offerfy · 先把履歷做好 | Offerfy · get the resume right first | Offerfy · 先把简历做好 |
| footer.login | 登入 | Log in | 登录 |

## Files

Create:

- `apps/frontend/components/landing/Hero.tsx` — kicker, headline, sub, CTAs
- `apps/frontend/components/landing/ResumeSheet.tsx` — decorative sheet
- `apps/frontend/components/landing/Reasons.tsx` — three sentences
- `apps/frontend/components/landing/Roadmap.tsx` — Now / Next + ATS note

Modify:

- `apps/frontend/app/[locale]/page.tsx` — compose the four pieces
- `apps/frontend/app/[locale]/layout.tsx` — `next/font` Fraunces + Source Sans 3 as CSS variables
- `apps/frontend/app/[locale]/globals.css` — replace `.landing-*` navy block with paper tokens; `.dark .landing-page` overrides
- `apps/frontend/components/Nav.tsx` — landing uses `nav.login`
- `apps/frontend/components/Footer.tsx` — landing uses `footer.login`
- `apps/frontend/messages/{en,zh-TW,zh-CN}.json` — keys above; update `meta.description`

Delete after the new page compiles:

- `apps/frontend/components/landing/HeroLoop.tsx`
- `apps/frontend/components/landing/Problem.tsx`
- `apps/frontend/components/landing/HowItWorks.tsx`
- `apps/frontend/components/landing/NowNext.tsx`

## Constraints (from product spec, still in force)

- UI never says CareerOS, RenderResume, or Roleloop
- ATS copy is parseability only; no hireability claim
- Anonymous create/upload; Google for history
- CTAs still skip `/new` and go straight to create or upload

## Verification

No existing landing tests. Verify in the browser:

1. `/`, `/en`, `/zh-CN` — copy matches the tables
2. Light and dark — paper vs reading room, not navy
3. Desktop and ~375px — reasons stack; sheet does not overflow; headline wraps on word boundaries
4. Create → `/create`, upload link → `/upload`, Log in → `/login`
5. Locale and theme switchers still work on landing
6. `/create` and `/editor` still look like the RR app shell (unchanged)

## Decision log

- Hybrid structure (not restyle-in-place, not full marketing)
- Warm paper world, with dark as warm charcoal — not forced-light
- Resume sheet, not editor mock, not type-only
- Intimate headline, not gap-punch, not loop slogan
- New composition; drop pitch-deck cards
- Do not hammer 主履歷
