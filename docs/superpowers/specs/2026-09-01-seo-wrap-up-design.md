# SEO wrap-up: Google Jobs + crawl hygiene

**Date:** 2026-09-01
**Status:** approved in companion (hygiene + Google for Jobs, approach A, validThrough = last_seen + 30d)

## Goal

Make existing public surfaces eligible and honest: active `/jobs/[id]` pages emit a Google-for-Jobs-shaped `JobPosting`, thin app routes stay out of the index, and marketing sitemaps stop pretending every URL changed today. Offerfy is not the employer. Apply stays on the original posting.

## Locked

- JobPosting `url` = Offerfy canonical job page; `directApply: false`; original `apply_url` is a page link only
- `identifier` = `{ "@type": "PropertyValue", "name": "Offerfy", "value": job.id }`
- `hiringOrganization.name` = company; no invented company URL
- `description` = sanitized `description_html`; if empty, wrap `description_text` in `<p>`
- `datePosted` = `posted_at ?? first_seen_at`
- `validThrough` = `last_seen_at + 30 days` (UTC ISO)
- Location:
  - remote + location → `jobLocationType: TELECOMMUTE` + `Place` / `PostalAddress` (`addressLocality` = location string, no invented country)
  - remote, no location → `TELECOMMUTE` only
  - on-site + location → `Place` only
  - neither remote nor location → omit JSON-LD (page still indexes)
- Closed jobs (`is_active: false`): no JSON-LD, `noindex,nofollow` (already)
- No `employmentType` / salary; no Indexing API; no extra URL types
- robots: locale-prefixed disallow for `/admin`, `/dashboard`, `/editor`, `/login`, `/api`, `/create`, `/upload`, `/new`
- noindex metadata on `/create`, `/upload`, `/new`
- Shares stay crawlable + `noindex,nofollow`
- `sitemap.xml`: `/`, `/jobs`, `/blog`, `/terms`, `/privacy`, published posts; no `lastModified` on static rows; posts keep frontmatter dates
- `jobs-sitemap.xml` unchanged (active only, lastmod = `last_seen_at` date)
- Landing meta, Organization JSON-LD, and featured marquee unchanged. No ItemList / WebSite SearchAction

## Non-goals

- Google Indexing API
- Company or location index pages
- Search Console automation
- Landing copy / CTAs / ingest
