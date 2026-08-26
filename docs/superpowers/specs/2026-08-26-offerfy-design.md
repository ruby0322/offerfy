# Offerfy

> Spec for implementers. Do not invent screens, locales, templates, tools, or scores not listed here.

Loop source (implementers, not UI): [docs/pitch/CareerOS-Pitch-Deck.pdf](docs/pitch/CareerOS-Pitch-Deck.pdf) (slides 1, 4, 8–11, 14, 16). UI copy never says CareerOS. Prefer gitignoring that PDF if `origin` is public (seed/TAM slides).

## Goal

Phase 1: **`/` is original Offerfy** and sells the agentic job loop. Users create or upload a **master resume** (Enhance) and edit immediately. Editor: left Typst source or AI chat (one tool: edit Typst); right live preview. Primary analysis feature: ATS parseability of the compiled PDF. Service selection, auth, dashboard, and the upload dropzone keep RenderResume’s look (style only). The editor is not RR smart-chat.

## Product loop

Cover line: **搜尋 · 客製 · 投遞 · 追蹤** 一步到位.

Agents (deck slide 8). Do **not** collapse this to search → tailor → apply. Enhance and Tailor are different. Track/A-B feeds Enhance.

```mermaid
flowchart LR
  search[Search]
  enhance[Enhance]
  tailor[Tailor]
  apply[Apply]
  search --> enhance
  enhance --> tailor
  tailor --> apply
  apply -->|"outcomes"| enhance
```

- **Search** — collect and match jobs. Phase 2.
- **Enhance** — strengthen the master resume. Phase 1: create starter Typst or upload → editor + ATS parseability (P1 wedge: ATS check without signup).
- **Tailor** — JD-specific version. Phase 3. Not “the AI editor”.
- **Apply** — submit + track; low-interest auto-apply vs mid/high wait-for-confirm. Resume A/B from outcomes. Phase 3.

**Dump email-as-interface.** No mailbox-as-UI, no `jobs@…` subscribe-by-email, no token/magic-link task pages, no “don’t open the app”. The interface is the **web app**. Notify/track are in-app. No `/t/[token]`, inbound mail, or Gmail integration in Phase 1.

Problem frame (landing, Offerfy-named): the gap is **execution** (fragmented tools, 8–12 nodes, quality under time pressure), not missing listings.

## Architecture

Next is UI only. FastAPI owns guest/Google, Typst source, compile, ATS report. Chat LLM may call a single Typst-source edit tool (sync or short Argo job). Print: vendored `@preview/basic-resume:0.2.9`.

## Tech stack

Next 16.1.6 · React 19.2.3 · Tailwind 4 · Radix · next-intl · FastAPI on Python 3.12 · SQLAlchemy · Alembic · psycopg · boto3 · Typst CLI 0.15.1 · Argo Workflows · Postgres 16 · SeaweedFS 3.76 · Kustomize/Argo CD as in pd-care

## Global constraints

- Product name is Offerfy. Never CareerOS, Roleloop, Offerly, Offerloop, or RenderResume in UI copy.
- **`/` is original Offerfy** (navy/teal, `#f4be82` as a small accent only). Hero is the loop, not the editor and not an inbox. No RR pain-point carousel, no Fortune 500 hireability grades, no TAM/pricing/fundraising on `/`.
- Service selection, auth, dashboard, upload dropzone: RR look from [render-resume.com](https://render-resume.com). Do not copy RR source, prompts, six-dimension scoring, LangChain, Supabase, Puppeteer, html2canvas, jspdf, Vercel.
- **Editor is not RR smart-chat.** Left: two tabs only — Typst text editor, AI chat. Right: realtime preview. No wizards, no JSON patch UI, no forced analyze step, no extra flow chrome.
- AI is optional. Create and upload both open the editor with a starter `.typ`. No gate that requires an LLM run before editing.
- Chat tools: exactly one — edit the current Typst source (search/replace or full-document write). No other tools in Phase 1.
- Source of truth: Typst source string on the resume. Preview/export compile that source. No JSON-as-canonical-edit-model.
- Locales: `en`, `zh-TW`, `zh-CN` only. Default `zh-TW`. next-intl. Messages `apps/frontend/messages/{en,zh-TW,zh-CN}.json`. Cookie `offerfy_locale`.
- Typst CLI 0.15.1; `@preview/basic-resume:0.2.9` + `@preview/scienceicons:0.1.0` vendored. Starter document imports that package. `paper: "a4"`. `accent-color: "#f4be82"`. Fonts: New Computer Modern, Noto Serif CJK TC, Noto Serif CJK SC. Locale → font/lang as before.
- No Chromium/Playwright/WeasyPrint. No Universe/Google Fonts at runtime.
- Create: editor + starter `basic-resume` template (placeholders). Upload: `pdf png jpg jpeg webp txt md`, max 10 MiB, SeaweedFS; editor still opens immediately; file available to chat if the user asks. Non-LLM text extract from PDF/txt may prefill comments; must not block the editor.
- **ATS parseability is the Phase 1 analysis feature** (AI hiring/ATS screening). No RR grades. No hireability A–F. Job-relative match is Search (Phase 2).
- LLM: OpenAI-compatible. `OPENAI_API_KEY` `OPENAI_BASE_URL` `OPENAI_MODEL`=`gpt-5.6-terra` (product name GPT-5.6 Terra is an alias for that slug). Used only when the user chats (or optionally asks chat to import an upload).
- Anonymous: create, upload, edit, preview, ATS report, download. Google: claim, history, Phase 2+.
- Guest cookie `offerfy_guest`. Rate limit 10 chat/Typst-tool runs / hour / guest, 20 exports / hour / guest.
- Preview: SVG debounce 400ms on Typst source change. PDF on idle 2s and on download. ATS report recomputes on each successful compile.
- Next rewrite: `BACKEND_INTERNAL_URL` default `http://backend:8000`, pd-care pattern. No Grafana rewrite.
- Path `/home/ruby0322/offerfy`. Namespaces `offerfy-dev`, `offerfy-prod`. Remote: `https://github.com/ruby0322/offerfy.git`.
- Phase 1 out of scope: forced AI analysis, JSON Patch editor, RR scoring/prompts, Search/Tailor/Apply backends, mailbox/email-as-UI, token pages, payments, admin email, extra templates/locales, Chromium, job-performance claims.

## Landing `/`

Nav: Offerfy, locale, Google.

1. **Hero** — kicker “AI 原生求職引擎”; headline 搜尋 · 客製 · 投遞 · 追蹤; sub “問題不是缺職缺，而是缺執行力”. Loop diagram (Search → Enhance → Tailor → Apply). CTAs: 建立履歷 / 上傳履歷. Microcopy: 先從主履歷開始；搜尋、客製投遞、追蹤即將推出.
2. **Problem** — three cards: fragmented flow, switching cost, unstable apply quality.
3. **How it works** — web journey: match → enhance master CV → tailor to JD → confirm apply → track. Interest grades (low auto / mid wait / high wait) marked coming.
4. **Now vs next** — Now: master resume + ATS parseability, anonymous. Next: Search, Tailor, Apply/track, A/B. ATS copy: parseability of the compiled PDF; no hireability claim.
5. Footer — locales, Google.

Files when frontend exists: `apps/frontend/app/[locale]/page.tsx`, `apps/frontend/components/landing/*` (HeroLoop, Problem, HowItWorks, NowNext — no Inbox/Mailbox), `landing.*` keys in the three message files.

## Flow

```mermaid
flowchart TD
    landing[Landing]
    create[CreateOpensEditor]
    upload[UploadOpensEditor]
    picker[ServiceSelection]
    editor[Editor]
    dash[Dashboard]
    download[PDF]
    landing --> create
    landing --> upload
    dash --> picker
    picker --> create
    picker --> upload
    create --> editor
    upload --> editor
    editor --> download
```

Two CTAs on `/` skip service selection. Picker stays for dashboard “new resume”.

```mermaid
flowchart TB
    browser[Browser]
    nextApp[Next]
    api[FastAPI]
    typst[Typst]
    ats[ATSReport]
    pg[(Postgres)]
    s3[SeaweedFS]
    llm[OpenAI]
    browser --> nextApp
    nextApp -->|"API rewrite"| api
    api --> pg
    api --> s3
    api --> typst
    typst --> ats
    typst --> s3
    api --> llm
```

Chat Typst-edit may run in-process FastAPI for short patches or Argo if the job is long. Compile and ATS stay in FastAPI, not Argo.

## Phase 1 screens

Original landing. Create (minimal: name the resume → editor). Upload dropzone (RR-look) then editor. **No analyze-first screen.** No mailbox routes. Editor (below). Download. Dashboard (Google). Auth (RR-look). Locale switcher. ATS status on the preview pane (not a separate blocking page). Service selection only from dashboard.

## Editor

```
+------------------+------------------+
| Tab: Typst | Chat|  Preview (right) |
|                  |  SVG/PDF live    |
| source  or  chat |  ATS strip       |
+------------------+------------------+
```

- Typst tab: CodeMirror 6 + `typst_lezer` of the `.typ` source (not a textarea). See [2026-08-26-typst-codemirror-editor-design.md](2026-08-26-typst-codemirror-editor-design.md).
- Chat tab: message list + composer. Model may call **only** `apply_typst_edit` (arguments: replace range or full source). Apply updates the Typst tab and retriggers preview.
- Right: compiled preview. ATS parseability strip (pass/fail checks, not a letter grade).

## ATS parseability (`AtsReport`)

Primary Phase 1 analysis. Deterministic on compiled PDF + extracted text. Unit-tested. No LLM.

```
text_extractable          // not image-only PDF
single_column             // no multi-column/table layout traps
contact_in_body           // name+email in page body, not header/footer-only
standard_headings         // Experience/Education (or locale equivalents) as headings
dates_machine_readable    // YYYY-MM or Present patterns in text
no_embedded_images_as_text  // photos/icons not used as the only contact/name
fonts_embedded
parse_roundtrip_ok        // extract recovers author string from source if present
```

Display each as pass/fail. No weighted sum, no A–F. Copy may say the PDF is built for ATS parsers; must not say it predicts hireability.

**Validation:** ≥30 golden `.typ` fixtures (en/zh-TW/zh-CN). Compile → extract. 100% recover name/email when set in source; fixtures with known ATS traps fail the matching checks. Pytest per field.

Job-relative keyword match: Phase 2 (Search).

## Layout

```
/home/ruby0322/offerfy
  docs/pitch/        CareerOS-Pitch-Deck.pdf (loop source; not UI copy)
  apps/frontend/     original `/`; RR-look upload/auth/dashboard; editor split
    messages/en.json zh-TW.json zh-CN.json
  apps/backend/
    app/             guest, google, typst compile, ats report, chat+apply_typst_edit
    typst/packages/  basic-resume 0.2.9, scienceicons 0.1.0
    typst/starter.typ
    fonts/
    tests/
    docs/scoring/    ATS golden fixtures + validation.md
  workflows/         optional long chat jobs only
  k8s/               pd-care copy; Argo Workflows if chat offloaded
  docker-compose.yml postgres 16, seaweedfs 3.76, backend, frontend
```

## Data stores

- `users`: google_sub, email, locale
- `guest_sessions`: key_hash, locale, created_at
- `resumes`: typst_source, source create|upload, locale, guest xor user_id, claimed_at, optional upload_s3_key
- `chat_messages`: resume_id, role, content
- SeaweedFS: uploads, SVG, PDF

## FastAPI capabilities

Guest cookie. Create resume with starter Typst. Upload file, still return an editable resume. Get/put Typst source. Compile SVG/PDF. AtsReport. Chat completion with `apply_typst_edit` only. Export PDF. Google claim. Healthz/readyz. Rate limits.

## K8s

Copy pd-care `k8s/base` resources in that kustomization (frontend/backend, ingress, postgres, seaweedfs). Overlays dev/prod, argocd, cert-manager. Rename to offerfy. Drop LINE/LIFF/model-cache/Grafana.

Add Typst+fonts on backend. Argo Workflows only if chat is offloaded; compile/ATS stay on backend. No Chromium render Deployment.

## Env (names)

`DATABASE_URL` `S3_ENDPOINT` `S3_ACCESS_KEY` `S3_SECRET_KEY` `S3_BUCKET` `GOOGLE_CLIENT_ID` `GOOGLE_CLIENT_SECRET` `OPENAI_API_KEY` `OPENAI_BASE_URL` `OPENAI_MODEL` `BACKEND_INTERNAL_URL` `TYPST_PACKAGE_PATH` `TYPST_FONT_PATHS` `AUTH_TOKEN_SECRET`

## Phases

- **0** — repo, compose, k8s, starter.typ → SVG/PDF, ATS tests on fixtures
- **1** — original loop landing; create+upload skip picker → editor; Typst|chat tabs; preview; ATS strip; Google claim
- **2** — Search (job-relative match, Google)
- **3** — Tailor + Apply + track, human confirm; outcome A/B into Enhance
