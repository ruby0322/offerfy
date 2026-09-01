# Landing featured-job carousel

**Date:** 2026-09-01
**Status:** approved in companion (layout A, copy B, carousel)

## Goal

Put the same scored featured-job **CSS marquee** as `/jobs` **under** the landing hero so the editor proof stays first, then real open roles feel like what this resume is for. Do not claim Offerfy applies, matches, or gets you hired.

## Locked

- Placement: after `Hero` (copy + editor mock), still in `<main>`, `max-w-[72rem]`
- Motion: reuse `JobsMarquee` (duplicate strip, ~45s loop, pause on hover, reduced-motion = first ~6 static)
- Label: landing-only, not `jobs.featuredLabel`
  - en: Roles this resume is for
  - zh-TW: 這份履歷對準的職缺
  - zh-CN: 这份简历对准的职位
- Cards: same as `/jobs`; link `/jobs/[id]`; `GET /v1/jobs/featured`
- Empty/API fail: omit the section
- Hero copy, CTAs, EditorMock unchanged
- No glow, gradients, pill CTAs, invented hire stats

## Non-goals

- Replacing the editor mock
- Strip between nav and hero
- Resume–job match
- Changing spotlight scoring
