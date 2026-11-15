# Offerfy — notes for agents

Read this before touching deploy, ports, processes, or the public site. It exists
because the same mistakes keep getting made.

## 1. `docs/deploy.md` is the runbook, and it is not in git

`docs/deploy.md` is gitignored, so it does not arrive with a clone and it is easy to
not know it exists. On abbey it is at `/home/ruby0322/offerfy/docs/deploy.md`.

**Read it before any deploy, DNS, Ingress, TLS, port, process, or firewall work.** It
owns the public routing, the pd-care ports this host must not steal, the Google OAuth
`redirect_uri` trap, and the live inventory. Do not re-derive any of that. Update it
when live state changes.

What follows is the short version plus what this file adds: how to do it **without
sudo**, which is the part the runbook assumes you have.

## 2. How the public site actually runs

| | |
|---|---|
| Public site | `https://offerfy.cc` |
| Frontend | systemd **`offerfy-web`** → `npx next start --port 3200`, cwd `/home/ruby0322/offerfy/apps/frontend` |
| API | systemd **`offerfy-api`** → uvicorn `127.0.0.1:8001` |
| Source of truth | the **`main`** branch, checked out at `/home/ruby0322/offerfy` |

There is **no CI** (no `.github/`), **no `gh` CLI on the host**, and Offerfy is **not** a
k8s Deployment — the manifests in `k8s/` are not applied for it. Nothing deploys itself.
A push alone changes nothing; the host must be rebuilt and restarted by hand.

## 3. Deploying a frontend change

Verified end to end 2026-10-23.

```bash
# 1. Land it on main. The HTTPS origin has no stored credentials — push over SSH.
cd /home/ruby0322/offerfy
git merge --no-ff <branch>
git push git@github.com:ruby0322/offerfy.git main:main

# 2. Keep a rollback. `next build` replaces files rather than writing in place, so a
#    hardlink copy is a real backup (checked: the copy keeps the old BUILD_ID).
cd /home/ruby0322/offerfy/apps/frontend
cp -al .next .next.bak-$(date +%Y%m%d-%H%M%S)

# 3. Build. NEVER a bare `next build` / `npm run build` — see docs/deploy.md rule 10.
npm run build:public

# 4. The /api rewrite is baked in at build time. It MUST be :8001.
python3 -c "import json; print(json.load(open('.next/routes-manifest.json'))['rewrites']['afterFiles'])"

# 5. Restart (see §4 if you have no sudo), then smoke (§5).
```

**Order, when the release also changes the API.** `docs/deploy.md` step 4 says
"restart web, then `kill -HUP` the API". That is right for a frontend-only
change and wrong when the release adds API routes: web-first leaves the new
frontend calling routes the old API does not have. **Reload the API first** —
the old frontend never calls the new routes, so the asymmetry only cuts one
way. Full order for a deploy carrying a migration and both halves:

```
alembic upgrade head   (hard rule 11 — before any reload, never after)
npm run build:public   (safe live; the old build serves until the restart)
kill -HUP  offerfy-api (zero downtime; verify by the listener, not worker count)
kill -TERM offerfy-web (the only downtime window)
smoke (§5)
```

Verified end to end 2026-11-13 deploying slice 3 (migration `032_applications`
plus new routers plus a rebuilt dashboard).

**The exception, and it is real: consent-bearing text.** If the release changes
user-facing text that the API also *records* something about, API-first is
actively wrong. A policy version stamped by the backend while the frontend still
serves the previous text is a **false** record — the row claims consent to text
the user never saw — which is worse than the stale record web-first would give.
Ordering cannot fix that case, only choose which way the window is wrong, so
collapse the window instead: HUP and restart back to back with no verification
step between them, and smoke after. A release that adds routes *and* changes
consent-bearing text wants a backward-compatible API or two deploys.

`docs/deploy.md` carries the same rule and the same exception as of 2026-11-13
(its *Which half goes first* section); the two agree. If you change one, change
the other — deploy.md is gitignored, so only this file's version is reviewable.

Rollback is a directory swap plus a restart:

```bash
cd /home/ruby0322/offerfy/apps/frontend
mv .next .next.failed && mv .next.bak-<stamp> .next
```

The old build keeps serving until the restart, so steps 1–4 are safe to run live. The
`.next.bak-*` directories become real disk once the build unlinks the originals —
delete old ones after a deploy sticks.

## 4. Restarting when `sudo` wants a password

Agent sessions have no passwordless sudo (`sudo -n true` fails), and `docs/deploy.md`
writes every restart as `sudo systemctl restart …`. **You do not need sudo.** Both units
run `User=ruby0322` with `Restart=always`, so the owning user can signal the main
process and systemd brings it back.

```bash
# Frontend: SIGTERM, systemd respawns it. Budget ~40 s of downtime — the npx/next tree
# can sit in `deactivating` for ~36 s — but it is VARIABLE, not reliably 40 s: on
# 2026-11-13 a new MainPID was active 4 s after the signal and the site answered 200
# immediately. Announce 40 s; do not conclude something broke if it returns sooner.
kill -TERM $(systemctl show -p MainPID --value offerfy-web)
# Wait for a NEW MainPID and ActiveState=active before smoking:
systemctl show -p MainPID -p ActiveState -p NRestarts offerfy-web

# API: SIGHUP instead — uvicorn respawns workers while the master holds the socket,
# so there is no downtime. Reloads code only, never the environment or .next.
kill -HUP $(systemctl show -p MainPID --value offerfy-api)
```

If you do have sudo, `sudo systemctl restart offerfy-web` is faster and cleaner; prefer
it. Either way, say the downtime window out loud first — this is the live site.

## 5. Smoke after every deploy

```bash
systemctl is-active offerfy-api offerfy-web
curl -sS -o /dev/null -w '%{http_code}\n' -L https://offerfy.cc/          # 200
curl -sS -o /dev/null -w '%{http_code}\n' https://offerfy.cc/api/healthz  # 200, not optional
curl -sS -D - -o /dev/null --max-time 15 https://offerfy.cc/api/v1/auth/google/start \
  | grep -i '^location:'   # redirect_uri MUST be offerfy.cc, never 127.0.0.1
```

HTML 200 alone proves nothing: the API can be down while pages still render. To prove
the *new* build is live, grep a string only it has out of a served chunk — a matching
chunk filename does not mean matching content.

## 6. Things that have bitten us

- **`apps/frontend/next-env.d.ts`**: never commit it. `next dev` and `next build` flip it
  between `.next/dev/types` and `.next/types`. Check `git status` before committing.
- **`react-resizable-panels` stays at 3.0.5.**
- **Product copy** must never say CareerOS, Roleloop, Offerly, Offerloop or RenderResume.
- **`messages/{en,zh-TW,zh-CN}.json`** must gain keys together; a missing key throws at
  render time, it does not fall back.

## 7. Frontend conventions

- **Mobile breakpoint**: `NARROW_SCREEN` in `lib/use-media-query.ts` (`max-width: 899px`)
  is the one place that decides "phone layout". Use it rather than inventing another
  threshold. Below it the editor is one column: the resume preview is the page and the
  tools live in a pull-up sheet (`components/editor/EditorMobileLayout.tsx`).
- **Any focusable field needs ≥16px type on a phone**, or iOS zooms the page on focus and
  never zooms back. The pattern is `text-base md:text-sm`.
- **Heights**: `dvh`, not `vh` — `100vh` includes the mobile URL bar. The editor shell
  additionally binds to `visualViewport` so the keyboard cannot cover the composer.
- **There is no frontend test runner wired up**, but a few pure modules do have
  `*.test.ts` beside them (`lib/seo.test.ts`, `lib/blog/*`). Run them by hand:
  `./node_modules/.bin/tsx --test lib/seo.test.ts`.
- **`npm run build:public` is the real gate** — it typechecks. **`npm run lint` is not
  usable as one**: bare `eslint` with no path walks generated and vendored files and
  reports ~121k problems (9k of them errors) on a clean tree, so a real error in your
  change is invisible in the noise and the exit code is meaningless. Lint the files you
  touched instead: `./node_modules/.bin/eslint path/to/file.tsx`. Beyond that, verify UI
  changes by actually running the app. A stub serving
  `/v1/resumes/<id>`, `/preview`, `/ats` and `/messages` is enough to drive the editor
  without the real backend; point `next dev` at it with `BACKEND_INTERNAL_URL`.

## 8. The job catalog ingest (and why it went dark)

The ~11k job pages are the only part of the site with real organic reach: they are
sitemapped and carry `JobPosting` JSON-LD, which is what gets a job board into Google
Jobs. They are also the part that silently broke.

**What happened.** `README.md` claimed the catalog refreshed every 6 hours via the
`jobs-ingest` CronJob in `k8s/`. Those manifests are never applied (§2), and nothing
else scheduled it — no systemd timer, no crontab. The last ingest ran **2026-09-01**.
`JobPosting.validThrough` is `last_seen_at + 30 days`, so on **2026-10-01** every
posting on the site expired at once, and the sitemap went on advertising 11k expired
URLs for three weeks. Nothing alerted, because nothing was watching and the ingest
exits 0 even when every source fails.

**The timer is a systemd _user_ unit**, not a system unit like `offerfy-api` /
`offerfy-web`, because agent sessions have no passwordless sudo (§4) and a user timer
needs none. It needs lingering, which is already enabled:

```bash
loginctl show-user ruby0322 --property=Linger   # must be Linger=yes
```

Install or refresh it after editing `ops/systemd/offerfy-ingest*`. Note the glob —
there are **three** files now, including the `OnFailure=` handler:

```bash
mkdir -p ~/.config/systemd/user
cp /home/ruby0322/offerfy/ops/systemd/offerfy-ingest*.{service,timer} ~/.config/systemd/user/
# the weekly digest's three units install the same way (offerfy-digest*), but stay un-enabled
systemctl --user daemon-reload
systemctl --user enable --now offerfy-ingest.timer
```

Check it, and run one pass by hand:

```bash
systemctl --user list-timers offerfy-ingest.timer     # NEXT / LAST / PASSED
systemctl --user start offerfy-ingest.service         # blocks; a full pass is minutes
journalctl --user -u offerfy-ingest.service -n 40 --no-pager
```

**Two guards keep it from going quiet again.** Do not undo either without replacing it:

1. `app.jobs.ingest_catalog` **exits non-zero when a pass refreshed nothing** — every
   source failed, or none returned any job. One dead board out of many still exits 0,
   because the catalog did get refreshed. A stalled catalog therefore shows up as a
   failed unit instead of as a slow SEO death.
2. **Staleness is derived from `last_seen_at`, not from `is_active`.** `is_active` only
   flips when an ingest run sees a posting disappear, so a dead ingest leaves every row
   active forever. `JOB_FRESHNESS_DAYS` (30) lives in exactly two places and **must stay
   in step**: `apps/backend/app/services/jobs/store.py` (with `freshness_cutoff()`, which
   both the jobs sitemap and the admin health block use) and `apps/frontend/lib/seo.ts`
   (which derives `validThrough` and noindexes past the window). A dead ingest now
   degrades to honest noindex. Do not add a third copy — import it.

The rule for "did this pass accomplish anything" is likewise single-sourced:
`refreshed_nothing()` in `apps/backend/app/services/jobs/ingest.py` backs both the CLI
exit code and the admin view's `failed` verdict, so the dashboard and the unit can never
disagree.

## 8b. The board list and what the catalogue carries

Both are deliberate and both are tuned against live data, not guesses.

**`apps/backend/data/ats_boards.json` is curated, and every row must be live.** It once
held 120 boards of which **64 404ed on every pass** — companies that had moved ATS, or
that never used Greenhouse/Lever/Ashby at all (TSMC, MediaTek, Shopee, Netflix, Shopify,
Zendesk…). Those were not lost inventory: they had never returned a single job. They were
removed, 29 were repaired by correcting the provider or slug (Anthropic is on Greenhouse,
not Ashby; Anduril is `andurilindustries`), and the file is now 81 boards that all answer
(79 until Microsoft and LINE were added 2026-11-15).

Before adding a board, **probe it**; do not assume. The three endpoints are in
`greenhouse.py` / `lever.py` / `ashby.py`, plus `smartrecruiters.py`. Note that probing
many slugs concurrently gets you rate-limited and returns false 404s — space the requests
out and retry anything that is not a definite 404, or you will delete live boards.

**Google is not in the catalogue and cannot be.** Checked 2026-11-13: `google`, `googlellc`,
`googleinc` and `alphabet` all 404 on Greenhouse, Lever and Ashby, and 422 (wrong site) on
Workday; the old `careers.google.com/api/v2|v3` JSON endpoints now 404. Like TSMC and
MediaTek (§8c) it would need scraping, which this project does not do. Do not add a Google
row on the assumption that so large an employer must be reachable somewhere.

**The reason is robots.txt and licensing, not rendering** — worth knowing, because the
obvious workarounds are all closed too, and the search has already been done:

- `www.google.com/about/careers/applications/jobs/results` **is** server-rendered: page 1
  carries 20 postings and their detail links inline. But `google.com/robots.txt` disallows
  **every `page=` variant** of that path for `User-agent: *`, so a compliant reader is
  capped at those first 20 of ~stale-unknown thousands. (The blanket
  `Disallow: .../jobs/results` is in the **Yandex-only** group — do not misread it as
  applying to us, and do not read the `*` group as permission to enumerate by swapping
  filter params instead of `page=`.)
- There is **no machine-readable contract** to parse even within that cap: no careers entry
  in `google.com/sitemap.xml` (23 child sitemaps, none for careers), and **no `JobPosting`
  JSON-LD on either the results page or a job detail page** — checked both. Only obfuscated
  CSS classes (`h3.QJPWVe`), which rotate.
- **No free aggregator is both stocked and licensed.** Keyless ones carry no Google at all
  (Arbeitnow, Jobicy, Himalayas, Remotive — checked by employer name; Remotive additionally
  bans redistribution). The Muse's public API is gone (`api.themuse.com` no longer
  resolves). Of the free-key ones, Adzuna's terms limit use to personal job search and
  research — "any use in a product or service that generates revenue or leads [requires] a
  licence agreement" — and Jooble's terms forbid republication without written permission.
  Careerjet publishes no readable terms. So the blocker there is the licence, not the key.

**A 200 from SmartRecruiters is not evidence of a board.**
`GET /v1/companies/<slug>/postings` answers **200 with `totalFound: 0` for any slug at
all**, including `zzzznotacompany99` — the same shape of trap as the Workday wildcard host
in §8c. Only a non-empty `content` array proves a SmartRecruiters board exists.

**Two Alphabet subsidiaries are carried instead**, added 2026-11-13 after the Google probe
came back empty: `waymo` (379 postings) and `wing` (48), both confirmed by a 200 with a
non-empty `jobs` array. They are the closest the catalogue can get to Google. 368 of their
427 postings pass `title_in_scope` (Waymo 85%, Wing 89%) -- high, as you would expect from
two engineering-led companies.

**Waymo exposes a `deny_always` needle that is wrong for it: `driver`.** The needle was
written for the TaiwanJobs era of delivery drivers, but the Waymo Driver is Waymo's
*product*, so 11 postings -- nearly all of its senior ML roles, e.g. "Machine Learning
Engineer, Driver Refinement & Validation" and "Senior Staff ML Engineer, Driver
Understanding and Evaluation" -- are silently dropped. This is the `cto`-inside-Dire*cto*r
family of accident, one tier up: a whole word that is a role noun at one employer and a
product name at another.

**`driver` was moved to `deny_context` on 2026-11-13, measured both ways.** A keep needle
now overrides it, so `engineer` rescues the Waymo roles while `Delivery Driver`,
`Truck Driver`, `Bus Driver`, `Forklift Driver`, `Driver, Night Shift` and bare `Driver`
still have no keep needle and stay out. `test_driver_is_a_context_needle_because_it_is_also_a_product_name`
pins both directions.

The delta was taken over **15,684 live titles pulled from all 75 slug-based boards** (not
the stored catalogue — prod reads are refused from an agent session; the ATS payloads are
the same data one hop upstream). In scope went 11,516 → 11,527: **10 distinct titles
admitted, 0 excluded.** Every admitted title is a senior engineering or ML role, and one of
them — `Software Engineer, Driver Experience` — is not Waymo's at all, so the needle had
been quietly eating driver-facing engineering roles at the rideshare and delivery boards too.

Measure a scope retune this way, in both directions on the same corpus in the same pass. An
improvement count is not a delta: a change that rescues ten roles and breaks six looks
identical to this one if you only count the rescues.

**Wing brings a role family the deny list has never seen: aviation operations.**
`Assistant Chief Pilot`, `Pilot-in-Command Manager` and `sUAS Flight Operator` all pass
today. That is the self-correcting direction per `title_in_scope`'s docstring -- a false
positive is visible on the jobs page and fixed by adding a needle -- but if a `pilot` or
`flight operator` needle is added, note that `Head of Flight Test Engineering` is a real
engineering role and must survive it.

**Microsoft is carried, but only its AI org.** Added 2026-11-15 on Greenhouse token
`microsoftcorporation` — 77 postings, every one naming Microsoft AI (MAI) in its
description, confirmed by a 200 with a non-empty `jobs` array. **This is not all of
Microsoft.** The main careers site has moved to Eightfold (`jobs.careers.microsoft.com`
redirects to `apply.careers.microsoft.com`, served by `app.eightfold.ai`), which is not a
provider we read, so the several thousand postings there are not in the catalogue. Do not
read the employer page's 77 as Microsoft's coverage, and do not "fix" it by adding a
Workday row — `microsoft` 422s on both wd1 and wd5.

All 77 pass `title_in_scope`, which is worth a glance rather than a celebration: the board
is engineering-led, but `Clinical Specialist`, `Creative Director, MAI Creative Studio
Lead`, `Sr. Visual Designer` and `Executive Business Administrator - MAI` pass only because
they match no deny needle, not because anything judged them technical. That is the
documented self-correcting direction (see `title_in_scope`'s docstring) — fix by adding a
needle if they prove noisy on the jobs page.

**LINE is carried by a fifth provider, `line`, added 2026-11-15.** It is the first
source that is not somebody's ATS. Every plausible slug 404s on Greenhouse, Lever and
Ashby, gives the meaningless SmartRecruiters 200/`totalFound: 0`, and 422s on Workday.
What LINE has instead is `careers.linecorp.com`, a Gatsby site over a Strapi backend, and
Gatsby ships each page's query result as JSON beside the HTML. `robots.txt` disallows only
two 2021 paths, so unlike Google there is no wall — which is the whole reason this source
could be built.

**The number that justified it: 42 in-scope Taipei postings**, from LINE Taiwan and LINE
Pay Taiwan — the largest block of Taiwan engineering roles the catalogue did not have,
including the `2027 TECH FRESH` new-graduate classes.

**`publish` is the load-bearing filter, and the counts are a trap.** The list payload
carries every posting Strapi knows about, draft or live: **306 of 387 are unpublished**.
Only **81 are published**, of which 53 are Taipei and 64 pass `title_in_scope`. An earlier
note in this file quoted 387 postings and 77 Taipei roles — those were the unfiltered
figures and they were wrong. If you re-measure this board, filter `publish is True`
first, and note `fetch_line` checks it with `is`, not for truthiness: a shape change that
made the field a string would make `"False"` truthy and quadruple the board silently.
`test_publish_is_checked_with_is_true_so_a_string_false_does_not_pass` pins that.

**The contract is a build artifact, not an API.** `/page-data/jobs/page-data.json` is how
Gatsby hydrates the page; nobody promised it to us and a redeploy could reshape it. So
`fetch_line` reads every field defensively and a shape change surfaces as **board not
found** — which `refreshed_nothing` already counts as a failed pass — rather than as "LINE
has no openings", which would expire every row we hold. That distinction is the one thing
not to simplify away; `test_a_shape_change_reads_as_board_not_found_rather_than_an_empty_board`
pins it.

**The employer comes off the posting, not the board.** One site carries 13 separate
employers (LINE Taiwan, LINE Pay Taiwan, LINE Plus, LINE Bank Taiwan, IPX…), so each
posting's own `companies[0].name` becomes its `company` and the single board row's `LINE`
is only a fallback. This is why `ats_boards.json` has a second exception to "slug is the
employer": for `line` the slug is the *site*. Note one Strapi name is stored with a
trailing space, which would otherwise derive a second employer slug for the same company.

**`job_fields` is a structured taxonomy, deliberately not used yet.** Each posting carries
one ("Server-side", "Data Engineering", "Human Resources"), and it would be a far better
scope signal than a title — LINE's titles are Korean, Japanese and Thai as often as
English, and the deny list has needles for neither Korean nor Japanese, so
`Senior HR Generalist` and `CS representative(EPI)` currently pass `title_in_scope` as
technical roles. This is the same gap Trend Micro's Japanese titles expose (§8c). Gating on
`job_fields` would fix it, but the taxonomy has 58 values of which a dozen are literally
prefixed `(not in use)`, and §8b's rule is that a scope change is measured both directions
over the whole live corpus in one pass. Measure it before wiring it in.

**SmartRecruiters is the fourth provider**, added because Grab, Canva and Wise publish
there and nowhere we could read. It differs from the others in two ways that matter:
descriptions are **not** in the list payload, so each posting costs its own request, and it
publishes a structured `function` per posting. Both facts shape the fetcher — it scopes
from the cheap list metadata first so a posting we will not keep never costs a detail call.

**TaiwanJobs is retired** (migration `015_scope_catalog`). It was 1,549 active postings of
which **91 were technical** — the rest room attendants, delivery drivers, caregivers. The
source is gone, but `'taiwanjobs'` stays in `JOB_SOURCES` and in the `job_source_values`
check constraint because historical rows still carry it; narrowing either would fail
against them.

**`data/job_scope.json` decides which postings we carry.** The board list picks employers,
this picks their roles: the audience is technical candidates, so a Finance Manager at a
great engineering company is out. Two deny tiers, and the distinction is load-bearing:

- `deny_always` — the role is non-technical whatever domain it serves. "Technical
  Recruiter, Hardware" is a recruiting job, and it leaked through a single-tier list
  because `hardware` rescued it.
- `deny_context` — a domain, not a role, so a keep needle overrides. "Compliance Engineer"
  and "Engineering Manager, Billing" are engineers.

Three substring accidents are called out in `tests/test_jobs_scope.py` and must not come
back: **`cto` is inside Dire*cto*r**, **`ux` is inside L*ux*e**, and `development` rescues
every Sales Development Representative. Topical keep-words (`data`, `cloud`, `platform`,
`security`, `technical`) are absent on purpose — the roles they would rescue are already
caught by a role noun, and as keep needles they let sales titles back in.

The filter runs in `ingest_board` **before `seen_ids` is computed**, which is what makes a
posting that falls out of scope get `is_active = False` on that same pass and leave the
sitemap. Do not move it after.

Retune by editing the JSON, then **measure against the real catalogue before committing** —
run the predicate over live titles and read a sample of both sides. A deny-list written
from imagination is how you lose "Infrastructure Security Engineer" to the word "security".

## 8c. Workday is a second, slower pass

Workday reaches the competitive semiconductor tier nothing else could — NVIDIA, Micron,
Applied Materials, Intel, NXP, Marvell — plus CrowdStrike, Zendesk, Red Hat, Salesforce,
Adobe and Workday itself, plus Trend Micro. **14 boards, ~12k postings**, in
`apps/backend/data/workday_boards.json`.

**Trend Micro joined 2026-11-15 and brings two sites on one tenant.**
`trendmicro.wd3` serves `External` (180 postings, global, Taipei included) and
`External_TrendLife` (19, mostly Taipei and Tokyo, and where the Applied AI roles live).
Both carry `company: "Trend Micro"` on purpose: one employer page over both boards, while
`WorkdayBoard.slug` keeps `expire_missing` scoped per site, so neither board can deactivate
the other's rows.

**"TrendAI" is a brand, not a board.** TrendAI is Trend Micro's own name for its enterprise
business unit, so third-party aggregators list these postings under it — but `TrendAI` and
`External_TrendAI` both 404, and those roles are on `External`. Do not go looking for a
third site.

**Trend Micro is the first board since TaiwanJobs to bring Japanese titles, and the deny
list cannot see them.** `data/job_scope.json` has 17 CJK `deny_always` needles, but they are
all **Traditional Chinese** from the TaiwanJobs era. Japanese writes sales as 営業, and the
zh-TW needle is 業務 — different characters, so no match. The result is that
`法人営業（リテールチャネル担当・名古屋）`, `キーアカウント営業（家電量販店 本部担当）` and
their siblings pass `title_in_scope` as technical roles. Four of the 14 in-scope
`External_TrendLife` titles are Japanese sales jobs this way.

This is the Wing-aviation case in a second script: a whole role family the deny list has
never seen. It was **left in place rather than patched blind** — §8b's rule is that a scope
retune is measured in both directions over the whole live corpus in one pass, and a 営業
needle has to be checked against Japanese titles on every other board (NVIDIA, Micron and
Intel all post in Japan) and against 営業技術, which is a real engineering role. Measure it
before adding it.

**It is not TSMC or MediaTek, and it cannot be.** Checked 2026-10-24: TSMC returns 403 to
every request including its own `robots.txt`, and MediaTek runs a bespoke Strapi site with
no public jobs API. Both would need scraping, which this project does not do. Do not add a
Workday row for either; they have no tenant.

**Adding a board needs a confirmed 200, not a guess.** `*.myworkdayjobs.com` is a wildcard
host, so a nonsense tenant like `mtk.wd1` still answers 406 — that is not evidence of
anything. A wrong site slug returns 422. Only a `POST /wday/cxs/<tenant>/<site>/jobs` that
returns 200 with an integer `total` proves a board exists. AMD, Qualcomm, Lam Research, Dell
and Analog Devices all have tenants whose site slug we have not found yet.

**Why it is a separate unit and a separate config file.** Workday's list endpoint is capped
at 20 per call and carries no description, and `postedOn` is the English phrase *"Posted
Yesterday"* rather than a date. Everything usable — description, `startDate`, apply url —
is behind a per-posting detail call at ~0.7s. Fetching all 12k every pass is 20–36 minutes
against 12,600 requests; the whole 6-hourly ATS pass is 80 seconds. So Workday gets
`offerfy-workday.timer`, **daily at 03:40**, and `ingest_workday` rather than
`ingest_board`.

Two things make it affordable, and **both are load-bear**:

1. **Scope from the free list metadata first**, so a posting we would not carry never costs
   a detail call.
2. **Skip the detail call for a posting already held.** `known_source_ids` reads what we
   have, and the fetcher returns those ids as `still_live` instead of re-fetching. Steady
   state therefore only pays for genuinely new listings.

The trap in (2): a skipped posting still needs its freshness clock bumped, which is what
`store.touch_jobs` is for. Miss it and the row ages out of `validThrough` and goes noindex
while the job is still open — the same failure that took the catalogue dark in October.
`WorkdayFetch.seen_ids` likewise unions new **and** still-live ids, because handing
`expire_missing` only the new ones would deactivate the entire board on every pass. Both
are covered by tests in `tests/test_jobs_workday.py`; do not simplify either away.

Note also that `refreshed_nothing` means something different here: upserting zero is the
normal steady state, so a Workday pass counts as failed only when **no board answered**.

## 9. Monitoring the ingest

Three signals, most useful first:

1. **`/admin/jobs`** (behind `ADMIN_EMAILS`, robots-disallowed) — freshness verdict,
   indexable vs noindexed page counts, recent passes, and the boards that failed in the
   last bad pass. Backed by `GET /v1/admin/jobs/ingest`.
2. **`journalctl --user -p err`** — the `OnFailure=` handler
   (`offerfy-ingest-failure.service`) tags one `err`-priority line, so this catches a
   failed pass whichever unit it came from.
3. **`systemctl --user list-timers offerfy-ingest.timer`** — did it fire, when is next.

`job_ingest_runs` holds one row **per board**, ~79 per pass. `ok_count - upserted_count`
is how many postings the scope rules dropped, which `/admin/jobs` shows as "out of scope"
without needing a column for it. (For smartrecruiters that figure undercounts: it scopes
itself before fetching descriptions, so its `ok_count` is already post-filter.) `run_id` (migration 014)
groups them; rows written before that migration have `run_id IS NULL` and are reported as
`legacy_row_count` rather than folded into a fake pass.

**Known blind spot.** `OnFailure=` only fires when a pass ran and failed. A timer that
silently stopped, or a host that went down, produces no event at all — that is caught only
by the `overdue` flag on the admin page, which needs someone to look. Closing it properly
needs an off-host dead-man's-switch. Also note there is still **no analytics and no Search
Console** on the site, so "is Google crawling the job pages" remains unanswerable here.

### The weekly digest timer

`offerfy-digest.timer` runs `app.jobs.build_digests` **Mondays 05:00 Asia/Taipei**
(`Persistent=true`, 10 min jitter), clear of the ingest slots. Same shape as ingest: a
systemd **user** unit in `~/.config/systemd/user/`, no sudo, `OnFailure=` writes to
`journalctl --user -p err`. The unit files are in `ops/systemd/offerfy-digest*` (install
with the same `cp` glob as ingest); runbook details are in `docs/deploy.md` ("Weekly job digest
timer"). It spends real money, so it is installed but **not enabled**; enabling it is the human's last deploy step (see deploy.md).
