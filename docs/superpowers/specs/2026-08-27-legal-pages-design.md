# Offerfy Terms and Privacy Policy pages

**Date:** 2026-08-27
**Status:** approved in design review

## Goal

Ship product-accurate Terms of Service and Privacy Policy for Phase 1 Offerfy (`offerfy.cc`), in `en`, `zh-TW`, and `zh-CN`, on the landing visual system, with footer links and a login agreement line.

Copy is written in-house from how the product actually works. It is not a substitute for a lawyer. Both documents say so in the intro.

## Operator

- Brand only: Offerfy / `https://offerfy.cc` (no company legal name)
- Contact: `james@offerfy.cc`
- Governing law: Taiwan (R.O.C.)

## Non-goals

- Cookie consent banner or login checkbox
- In-app account deletion
- Payments / billing clauses
- Company legal name, registered address, or DPO
- Lawyer review
- Markdown legal files or new dependencies
- Inventing analytics, selling data, or ATS hireability claims
- Product names CareerOS, RenderResume, Roleloop, Offerly, Offerloop

## Routes and chrome

Canonical URLs (locale prefix as-needed, default `zh-TW`):

- `/terms`, `/en/terms`, `/zh-CN/terms`
- `/privacy`, `/en/privacy`, `/zh-CN/privacy`

Shared `LegalDocument`: `landing-page` wrapper, `Nav variant="landing"`, `Footer variant="landing"`, article `max-w-[40rem]`. Pass landing variant explicitly so `/terms` and `/privacy` do not fall through to the app (RR) shell.

Each page: `h1`, last-updated `2026-08-27`, intro, mapped sections (`h2` + paragraphs; optional cookie rows table), cross-link to the other document.

Per-page `generateMetadata` from `legal.terms.metaTitle` / `legal.privacy.metaTitle`.

## Links

**Footer (landing and app):** `footer.terms` and `footer.privacy` beside the existing login link.

**Login:** under the Google button, `legal.loginAgree` via `t.rich` with `<terms>` / `<privacy>` wrapping locale-aware `Link`s. No checkbox.

## Copy storage

Namespace `legal` in `apps/frontend/messages/{en,zh-TW,zh-CN}.json`. Bodies as structured arrays, rendered with `t.raw("sections")`.

```json
"legal": {
  "termsLink": "Terms",
  "privacyLink": "Privacy",
  "lastUpdated": "Last updated {date}",
  "loginAgree": "By continuing, you agree to the <terms>Terms</terms> and <privacy>Privacy Policy</privacy>.",
  "seeAlsoTerms": "...",
  "seeAlsoPrivacy": "...",
  "cookieName": "...",
  "cookiePurpose": "...",
  "terms": { "metaTitle": "...", "title": "...", "intro": "...", "sections": [{ "heading": "...", "paragraphs": ["..."], "rows": [["name", "purpose"]] }] },
  "privacy": { "metaTitle": "...", "title": "...", "intro": "...", "sections": [...] }
}
```

`rows` is optional and used only for the cookies table.

## Terms sections

1. Who we are — Offerfy at `https://offerfy.cc`. Contact `james@offerfy.cc`.
2. The service — web resume editor (create/upload Typst, live preview, PDF export, ATS parseability of the compiled PDF). Anonymous use allowed. Google saves history. Search / tailor / apply are not part of the current service. ATS is parseability only.
3. Accounts — guest cookie vs Google sign-in (`openid email profile`; store Google subject id + email).
4. Your content — you keep rights; license to host, compile, ATS, and (when you chat) send content to the LLM provider.
5. Acceptable use — no abuse, malware, harmful scraping, or processing data you have no right to process.
6. AI — chat optional; output can be wrong; review before use. Guest rate limits: 10 chat / 20 exports per hour.
7. Availability — as-is, no uptime promise, Phase 1.
8. Liability — plain-language limitation; cap at amounts paid in the last 12 months (currently zero).
9. Changes — date at top; continued use is acceptance.
10. Governing law — Taiwan (R.O.C.).
11. Contact — `james@offerfy.cc`.

## Privacy sections

1. Who we are — same operator + contact.
2. What we collect — guest cookie/session/resumes/rate events; Google OAuth (`google_sub`, email, locale) + session cookie; locale cookie; theme in `localStorage` (not a cookie); resume Typst/title/compiled SVG/PDF/ATS/chat; uploads `pdf png jpg jpeg webp txt md`, max 10 MiB, object storage.
3. Why — editor, preview, ATS, auth, rate limits, language.
4. When we share — Google (sign-in); LLM provider only when the user chats; infrastructure (database, object storage, hosting). We do not sell personal data. No analytics product.
5. Retention — guest tied to cookie; signed-in until deletion request. No in-app delete; email `james@offerfy.cc`.
6. Your rights — access, correction, deletion via that email (plain-language PDPA-style; do not claim GDPR/CCPA).
7. Children — not directed at children under 13; no age-gate in the product.
8. International processing — Google and the LLM provider may process outside Taiwan.
9. Cookies table — `offerfy_guest`, `offerfy_session`, `offerfy_locale` (essential). No cookie banner.
10. Changes + contact.

Intro on both pages: these documents describe current practices and are **not legal advice**.

## Files

Create:

- `apps/frontend/components/legal/LegalDocument.tsx`
- `apps/frontend/app/[locale]/terms/page.tsx`
- `apps/frontend/app/[locale]/privacy/page.tsx`

Modify:

- `apps/frontend/components/Footer.tsx`
- `apps/frontend/app/[locale]/login/page.tsx`
- `apps/frontend/app/[locale]/globals.css` — legal article/table styles
- `apps/frontend/messages/{en,zh-TW,zh-CN}.json`

## Verification

No frontend unit tests. Verify in the browser:

1. `/terms`, `/en/terms`, `/zh-CN/terms` and the same for `/privacy` — landing paper chrome; copy matches locale.
2. Footer on `/` and `/login` links to both pages; login Google button has the agree line with working links.
3. Locale switcher on a legal page keeps you on that document.
4. Light/dark and ~375px: article readable, footer links wrap.
5. Cross-links Terms ↔ Privacy work.
