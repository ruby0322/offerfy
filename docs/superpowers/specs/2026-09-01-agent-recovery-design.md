# Agent recovery pack (honest surface)

**Date:** 2026-09-01
**Status:** approved in companion (approach A)

## Goal

Make `https://offerfy.cc` recoverable for agents without inventing a machine API. Unknown URLs return HTTP 404 with a short map of where to go. Marketing pages can negotiate `text/markdown`. Trust pages and Organization JSON-LD describe the real product: a browser resume editor plus a public jobs catalog. Offerfy is not the employer and does not apply, match, or issue API keys.

## Locked

- HTTP 404 (never 200 app shell) for nonexistent paths
- HTML 404: landing chrome; links to home, `/llms.txt`, `/sitemap.xml`, `/jobs`, `/blog`
- `Accept: text/markdown` (higher q than `text/html`) → markdown body, `Content-Type: text/markdown; charset=utf-8`, `Vary: Accept`
- Markdown negotiation only for `/`, locale home, `/jobs`, `/blog`, `/terms`, `/privacy`, `/about`, `/contact` (optional locale prefix). Job detail and blog posts stay HTML
- `/llms.txt`: when-to-use / when-not; canonical links; no OAuth AS or API key fiction
- Featured marquee label is `<h2 class="jobs-featured-label">`; cards stay `<h3>`; Hero `<h1>` unchanged
- `/about` and `/contact` ≥500 characters per locale, landing chrome, indexable, footer + sitemap
- Organization JSON-LD: `name`, `description`, `url`, `contactPoint` (email `james@offerfy.cc`, `contactType` customer support). No `PostalAddress`
- Visual design, CTAs, marquee motion, Google login unchanged

## Non-goals

OAuth 2.0 authorization-server metadata, scoped permissions, public agent API, OpenAPI, self-serve keys, sandbox, RateLimit headers, brand SERP, invented street address
