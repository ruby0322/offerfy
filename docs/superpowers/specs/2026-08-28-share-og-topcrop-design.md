# Share OG: top-half close-up + Offerfy mark

**Date:** 2026-08-28
**Status:** approved in design review

Supersedes the **Canvas** compositing rules in [2026-08-28-share-og-footnote-design.md](./2026-08-28-share-og-footnote-design.md). Endpoint, authz, Next metadata, footnote, and marketing card C are unchanged.

## Goal

Share unfurls (`GET /v1/shares/{token}/og.png`) show a **close-up of page 1’s top**: same layout, scaled so the page width fills the inner box, cropped at the bottom. Cream paper shows on **top / left / right** only. An Offerfy mark sits in the **top cream band**, not on the resume.

## Non-goals

- Changing share HTML, footnote, `og:title`, or card C
- Cover-cropping left or right of the page
- Four-sided letterbox / contain of the full page (that is `og-v1`)
- Overlaying a translucent mark on the resume pixels
- Snapshot-at-share-time, Redis/S3 cache, dark-mode OG

## Decisions

- **Three-side pad.** 48px cream on top, left, and right. **0px** on the bottom. Cropped page reaches the bottom edge of the 1200×630 canvas.
- **Width-fit, top-align.** `scale = inner_w / page_w`. Paste at `(pad, pad)`. Clip overflow. Do not use `min(w, h)` contain. Do not vertically center.
- **Mark in the pad.** 8px clay square `#A35C3A` + the word `Offerfy`, vertically centered in the 48px top band, left edge aligned with the inner box (`x = 48`). The mark’s bounding box stays in `y ∈ [0, 48)` so it cannot cover header text.
- **Cache bust.** New ETag version string so crawlers drop `og-v1` contain images.
- **Sharper source.** OG compile passes Typst `--ppi 144` (explicit). Preview PNG endpoints keep today’s default. At 144 ppi a US-letter page is ~1224px wide, then downscaled to 1104px.

## Canvas

| | |
|---|---|
| Size | 1200×630 |
| Background | `#F6F1E8` |
| Pad | top 48, left 48, right 48, bottom 0 |
| Inner | origin `(48, 48)`, size **1104×582** |
| Version | `og-v2-1200x630-f6f1e8-top-pad48-mark` |

Compose (`compose_og_png`):

1. Fill RGB canvas with cream.
2. Convert page PNG to RGBA. `new_w = 1104`, `new_h = max(1, round(page.height * (1104 / page.width)))`. LANCZOS resize. Flatten onto cream, paste at `(48, 48)`. Pixels with `y ≥ 630` are discarded (Pillow clips). If `new_h < 582` (rare landscape), leave cream below the page; do not stretch.
3. Draw the mark **after** the page paste:
   - Square: 8×8 at `(48, 20)` (vertical center of the 48px band).
   - Gap 8px, then `Offerfy` in ink `#1C1914`, 20px. Vertically center the text with the square. Clip or choose a y such that the text bbox stays in `y ∈ [0, 48)`.
   - Font: DejaVu Sans Bold at `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`. If that file is missing, draw the square only (tests still assert the clay pixel).
4. Save PNG.

Always cream, ignore visitor theme.

## Pipeline

Unchanged path: `public_og` → ETag from `sha256(OG_VERSION + "\n" + typst_source)` → LRU `(token, etag)` max 32 → `compile_typst_pages(..., "png", pages="1", ppi=144)` → `compose_og_png`.

Add optional `ppi: int | None = None` to `compile_typst_pages`. When set, pass `--ppi {ppi}` to Typst. Only the OG handler sets it.

HTTP contract unchanged: 200 PNG, 304 + `Cache-Control: public, max-age=300`, 404 missing token, 400/504 compile failure.

## Testing

In `tests/test_og_image.py`, replace the contain/letterbox assertions:

- Input: tall blue PNG (e.g. 200×400).
- Output 1200×630.
- `(0, 0)`, `(1199, 0)`, `(0, 47)` are cream (top/side pad).
- Bottom-center is **blue** (page reaches `y = 629`), not cream.
- `(48, 20)` (and a couple of interior square pixels) are clay `#A35C3A`.
- A pixel on the page just below the band, e.g. `(48, 50)`, is blue, not clay or cream.

`og_etag` changes when `OG_VERSION` changes (same source → different digest than a hardcoded `og-v1` string, or compare two version constants in the test by importing the live constant).

HTTP tests in `test_share.py` stay as they are (still 1200×630 PNG). Monkeypatch compile; they do not inspect crop.

## Out of scope here

Marketing `opengraph-image.tsx`, share footnote, `generateMetadata` image URL.
