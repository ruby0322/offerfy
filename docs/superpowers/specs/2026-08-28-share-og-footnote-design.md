# Share page Open Graph, site OG card, and footnote

**Date:** 2026-08-28
**Status:** approved in design review

## Goal

Public `/s/{token}` links unfurl with a real first-page preview (contained on cream paper) and generic copy that names neither the document nor the owner. The page itself hides the resume title and ends with a quiet Offerfy attribution plus a text CTA. Every other route uses one marketing Open Graph card (option C).

## Non-goals

- Showing resume title, Google name, email, or Typst-parsed name in tab, OG title, OG description, header, or footnote
- Dark-mode OG images (always light cream `#F6F1E8`)
- Snapshot OG at share time (image is live, same as preview/PDF)
- Persistent OG cache table, Redis, or S3
- Password, expiry, sitemap, or `index` on share URLs
- Frontend unit tests (no test runner)
- Product names CareerOS, RenderResume, Roleloop, Offerly, Offerloop in UI copy

## Decisions

- **No names on the public surface.** Header is brand only. Tab / OG title is `Resume · Offerfy` (i18n). Footnote has no `{username}`.
- **Share OG = live page 1, contain.** *(Superseded for compositing by [2026-08-28-share-og-topcrop-design.md](./2026-08-28-share-og-topcrop-design.md): top-half width-fit crop, three-side pad, Offerfy mark.)* `GET /v1/shares/{token}/og.png` still compiles Typst PNG for page 1 onto 1200×630 cream paper.
- **Everywhere else = marketing card C.** Next `opengraph-image` (English headline on the image; localized title/description still come from `pageMetadata`). Share `generateMetadata` overrides `images` with the live PNG URL when the token exists.
- **OG pipeline stays on the backend** for share images (same Typst path as preview/PDF). Next only points `og:image` at that URL.
- **Cache without a table.** ETag from `sha256(typst_source + canvas version)`. In-process LRU of composed PNGs (max 32). `Cache-Control: public, max-age=300`. `If-None-Match` → 304.
- **Footnote A.** One muted centered line under the preview: attribution sentence + `Create yours` text link to `/create`. Not a primary button. No “or upload one”. 404 pages omit the footnote.
- **Still `robots: noindex, nofollow`** on share routes. Unlisted.

## Authz

Unchanged. `GET /v1/shares/{token}/og.png` is anonymous. Missing or deleted token → **404**, never 403. No cookies required. Response is PNG bytes only (no JSON, no source, no owner).

## Canvas

Compositing for `og.png` is defined in [2026-08-28-share-og-topcrop-design.md](./2026-08-28-share-og-topcrop-design.md) (`og-v2`, three-side pad, width-fit top crop). Size remains **1200×630**, background **`#F6F1E8`**, always light cream.

## APIs

`GET /v1/shares/{token}` stays `{ title, locale }` for PDF filename. Title is not shown on the page or in meta.

New:

`GET /v1/shares/{token}/og.png`

- **200** `image/png`, body is the composed 1200×630 PNG
- **304** if `If-None-Match` matches current ETag
- **404** unknown/revoked token
- Compile failure: **400** (Typst error) or **504** (timeout), same family as existing compile endpoints
- Headers: `ETag`, `Cache-Control: public, max-age=300`

Typst: share OG compiles **page 1 only** (`pages="1"`) with `--ppi 144`. See the topcrop spec. Preview PNG is unchanged.

Pillow composites the page onto the canvas. New dependency: `pillow`.

In-process cache key: `(token, etag)`. Store composed PNG bytes. Bound with LRU, max **32** entries. On source change, ETag changes, miss, recompile.

## Next metadata

`pageMetadata` in `lib/seo.ts` sets `openGraph.images` and `twitter.images` to the App Router file `app/[locale]/opengraph-image.tsx` (Next serves it as `/[locale]/opengraph-image`). Twitter stays `summary_large_image`.

Share `generateMetadata`:

- `robots: { index: false, follow: false }`
- Localized title: `Resume · Offerfy` / `履歷 · Offerfy` / `简历 · Offerfy`
- Localized description: the footnote sentence **without** the CTA
- Canonical: the share URL for this locale
- If internal `GET /v1/shares/{token}` is **200**: `openGraph.images` / `twitter.images` = `{SITE_URL}/api/v1/shares/{token}/og.png` (1200×630)
- If **404**: do not set a share `images` URL; the locale `opengraph-image` (card C) remains
- `generateMetadata` does not compile Typst. A later crawler hit on `og.png` that fails (400/504) yields a link preview without an image
- Never put `resume.title` or an owner name in metadata

Internal existence check uses `BACKEND_INTERNAL_URL` (same host as the Next rewrite), not `SITE_URL`.

Site OG image (C): `app/[locale]/opengraph-image.tsx` via `next/og` `ImageResponse`, 1200×630, cream `#F6F1E8`, 8px clay `#A35C3A` square, English headline **The AI resume editor you’ll keep using.** and sub **Chat edits this file. No account needed.** Image copy is English on every locale so we do not bundle a CJK font into the OG renderer. Localized `og:title` / `og:description` still come from `meta.*` / page-specific strings.

Share does **not** add a second `opengraph-image.tsx`; it overrides `images` in `generateMetadata`.

## Public page UI

`ShareView`:

- Header: brand `Link` home only. Remove the title `<span>`.
- Main: existing `EditorPreview` (download PDF + zoom). No ATS.
- Footnote: `shrink-0`, centered, `text-sm text-muted-foreground`. Layout remains `h-screen` column; preview keeps `flex-1 min-h-0`.

Copy keys under `share.*` (en / zh-TW / zh-CN):

| Key | en | zh-TW | zh-CN |
| --- | --- | --- | --- |
| `metaTitle` | Resume · Offerfy | 履歷 · Offerfy | 简历 · Offerfy |
| `metaDescription` / `footnote` | This resume was made and shared on Offerfy. | 這份履歷在 Offerfy 製作並分享。 | 这份简历在 Offerfy 制作并分享。 |
| `cta` | Create yours | 建立你的 | 创建你的 |

CTA is a `Link` to `/create`, inline after the sentence, underlined text (not a filled button).

404: existing `notFound` copy only. No footnote, no CTA.

## Error handling

- Unknown token: page 404 UI; metadata without share PNG (falls back to C); `og.png` endpoint 404
- Typst fail on `og.png`: 400/504; metadata existence check still 200, but crawlers that hit a failing `og.png` get no image — acceptable; do not block the HTML page
- LRU eviction: next request recompiles
- Process restart: cache empty; ETag still enables 304 from downstream CDNs that stored the body

## Testing

Backend pytest (`apps/backend`, PATH includes `.tools`):

- Public `og.png` **200**, `Content-Type` starts with `image/png`, PNG magic `\x89PNG`, no cookies
- Body is 1200×630 (Pillow `Image.open`)
- Unknown token **404**
- After private, old token `og.png` **404**
- `ETag` present; second request with `If-None-Match` → **304**
- Changing `typst_source` (via owner PUT) changes ETag (monkeypatch compile still ok if the handler hashes source, not the PNG)
- Monkeypatch `compile_typst_pages` to return a tiny PNG so tests do not need Typst for the HTTP contract. Crop / mark pixel tests live in the topcrop spec (`test_og_image.py`).

Existing public meta/preview/export tests stay green. Public JSON still has no `id` / `typst_source` / owner.

Browser: share page has no title in the header, footnote + CTA to `/create`, 404 unchanged. Confirm `generateMetadata` in view-source: generic title, `og:image` to `/api/v1/shares/{token}/og.png`. Open that URL: cream on top/left/right, page-1 top close-up, Offerfy mark in the top band, page reaching the bottom edge. A non-share page (home) view-source uses the marketing card, not a resume page.
