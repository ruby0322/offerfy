# Jobs catalog: posting-recency list, append load-more, spotlight marquee

**Date:** 2026-09-01
**Status:** approved in plan review

## Goal

`/jobs` is a public catalog, not a match product. The first screen must show **recent postings** (not whichever board ingested last) and a **featured strip** scored in the ingest pipeline. “More jobs” appends. Policy knobs live in JSON so retune does not require a code change.

Copy never claims tailor, apply-tracking, or resume match. No CareerOS / Roleloop / Offerly / Offerloop / RenderResume. No glow, gradients, or pill CTAs.

## Non-goals

- Argo / CronJob deploy
- Resume–job match
- New ingest sources
- Production image cut
- Frontend unit-test runner

## Pagination

Default list: `posted_at DESC NULLS LAST, id DESC`. Cursor `{t, i}` where `t` is `posted_at` ISO, or empty when `posted_at` is null. Filter continues that order (null `posted_at` rows come after every dated row).

SSR first page (`limit=20`). Client **Load more** `GET /api/v1/jobs` (Next rewrite) with the same `q` / `source` / `remote` and `cursor`; append items; do not remount the first page.

Empty copy:

- Filters present (`q`, `source`, or `remote`): no matches
- No filters: catalog empty / ingest may not have run

## Spotlight policy file

All knobs: `apps/backend/data/spotlight.json`. Python loads and applies; it does not own weights.

```json
{
  "recency_half_life_days": 21,
  "remote_boost": 1.08,
  "default_role_weight": 0.12,
  "source_weights": {
    "greenhouse": 1.0,
    "lever": 1.0,
    "ashby": 1.0,
    "taiwanjobs": 0.85
  },
  "role_rules": [
    { "weight": 1.15, "any": ["staff", "principal", "資深", "主任"] },
    { "weight": 1.0, "any": ["engineer", "developer", "designer", "scientist", "product", "工程師", "設計師", "研發", "後端", "前端", "資料"] },
    { "weight": 0.55, "any": ["analyst", "specialist", "專員", "助理"] }
  ],
  "featured_limit": 24,
  "candidate_pool": 400,
  "max_per_company": 2,
  "min_sources": 2,
  "max_consecutive_source": 2
}
```

Retune: edit JSON, run `rescore_all`. New title bucket: another `role_rules` row (first match wins). New numeric factor: JSON field + one multiply in `score_job`.

## Score

- `age_days` from `posted_at`, else `last_seen_at`
- `recency = 2 ** (-age_days / recency_half_life_days)`
- `role_weight` = first `role_rules[].any` substring in casefolded title, else `default_role_weight`
- `source_weight` from map (missing key → 1.0)
- `spotlight_score = recency * role_weight * source_weight * (remote_boost if remote else 1.0)`

`rescore_all` at the end of `ingest_catalog`. Column `jobs.spotlight_score` (float). Fresh DBs: 006. Dest table already exists: 007 `add_column`. Dest that is stamped 005 with tables from `create_all`: `alembic stamp 006_jobs_catalog` then `upgrade head`.

## Featured select

1. Top `candidate_pool` active by `spotlight_score`
2. Greedy pack `featured_limit`: max `max_per_company`, at least `min_sources` if candidates allow, no more than `max_consecutive_source` in a row

`GET /v1/jobs/featured` before `/v1/jobs/{id}`. `JobList` items, no HTML.

Tests use a **fixture JSON**. One test parses real `spotlight.json` and checks every `JOB_SOURCES` key has a weight.

## Marquee

Landing paper tokens, Fraunces titles, clay meta. Hero: kicker, Jobs h1, lead (~40rem), then a 72rem overflow-hidden card strip. Duplicate the card list for a CSS `translateX` loop (~45s), pause on hover. Card: source · company, title, location/remote, link `/jobs/[id]`.

`prefers-reduced-motion: reduce`: no animation; first ~6 cards wrap.

List rows: location once (meta or title line, not both).

`jobs.featuredLabel`: en “Open roles worth a look” / zh-TW 「值得看的職缺」 / zh-CN 「值得看的职位」.
