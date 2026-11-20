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
| Frontend | **three** `next start` instances — `offerfy-web` (system unit, `:3200`) plus `offerfy-web@3201` and `offerfy-web@3202` (**user** units), all cwd `/home/ruby0322/offerfy/apps/frontend` |
| Frontend load balancer | **`web-lb`** compose service — nginx on `192.168.49.1:3210`, round-robins the three. This is what the cluster's Service points at |
| API | systemd **`offerfy-api`** → uvicorn `127.0.0.1:8001`, 2 workers, DB pool 30+10 per worker (see §10) |
| Source of truth | the **`main`** branch, checked out at `/home/ruby0322/offerfy` |

**The frontend path gained a hop on 2026-11-16, so that it can be restarted without
downtime** (`next start` is one process and has to die before it is replaced):

```
ingress-nginx → Service offerfy-native-3100  targetPort 3210
  → 192.168.49.1:3210   web-lb (nginx container, host networking)
      → 127.0.0.1:3200  offerfy-web.service    (system unit)
      → 127.0.0.1:3201  offerfy-web@3201       (user unit)
      → 127.0.0.1:3202  offerfy-web@3202       (user unit)
```

Three things about it that are easy to get wrong:

- **ingress-nginx routes by the Service's `targetPort`, not by the Endpoints/EndpointSlice
  port.** Patching the Endpoints alone changes nothing. Change `targetPort`.
- **Only `:3200` and `:3210` are open to the cluster** — UFW allows
  `192.168.49.0/24 → tcp 3200` and `→ tcp 3210` and nothing else, so 3201/3202 are
  deliberately unreachable except through the proxy.
- **Multiple Endpoints subsets cannot replace the proxy.** The EndpointSliceMirroring
  controller flattens all subsets into one slice with a single shared `ports` list, and two
  subsets both named `http` fail validation. Tried and reverted; see
  `docs/host-scaling-plan.md` §2.1.

There is **no CI** (no `.github/`), **no `gh` CLI on the host**, and Offerfy is **not** a
k8s Deployment — the manifests in `k8s/` are not applied for it. Nothing deploys itself.
A push alone changes nothing; the host must be rebuilt and restarted by hand.

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

# 5. Roll the three instances one at a time. No downtime, no sudo. Then smoke (§5).
ops/rolling-restart-web.sh
```

**All three instances share one `.next`, and that has a consequence during a deploy.**
`next build` replaces files rather than writing in place (which is what makes the `cp -al`
in step 2 a real backup), so between the build and the last instance's restart, an instance
that has not yet rolled is serving HTML that references chunk files already unlinked. The
browser gets a `ChunkLoadError` and reloads onto the new build. **This is not new with three
instances** — it already happened with one, for the whole gap between build and restart —
but the window now lasts until the last instance has rolled, roughly a minute. The real fix
is per-release directories, which is not built; see `docs/host-scaling-plan.md` §5.

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

**Frontend: use the script. There is no downtime window any more.**

```bash
ops/rolling-restart-web.sh            # all three instances, one at a time
ops/rolling-restart-web.sh --dry-run  # prints what it would do, touches nothing
PORTS="3201 3202" ops/rolling-restart-web.sh
```

It drains each instance out of the load balancer (marks it `down` in
`ops/nginx/upstream.d/`, graceful nginx reload), waits for the replacement to answer,
then puts it back — checking the public site between every step and stopping on the first
non-200. **Measured 2026-11-16 across a full three-instance restart: 109/109 GETs and
75/75 POSTs with zero failures.** The old `~40 s of downtime` budget is gone.

Do **not** just `kill -TERM` an instance that is still in rotation. nginx retries another
upstream on a connect failure, but `non_idempotent` is deliberately off, so a POST already
delivered to a dying instance is not retried — it fails to a real user. The drain is the
whole point.

```bash
# API: SIGHUP — uvicorn respawns workers while the master holds the socket, so there is
# no downtime. Reloads code only, never the environment or .next. Verify by the listener,
# never by worker count: `ss -ltnp | grep 8001` should show master + 2 workers.
kill -HUP $(systemctl show -p MainPID --value offerfy-api)
```

A HUP does **not** re-read the unit file, so a change to `ExecStart` (the worker count)
needs `daemon-reload` + `restart`, which needs root. Concurrency now lives in
`app/db.py`'s pool instead, precisely so it can ship with a HUP — see §10.

**An `.env.production` change needs a full restart, and a HUP will silently not do it.**
The master is what read `EnvironmentFile`; a HUP only respawns its workers, so the old
value stays live and the edit looks like it did nothing. Still no sudo — TERM the master
and `Restart=always` (`RestartSec=3`) brings back a new one:

```bash
kill -TERM $(systemctl show -p MainPID --value offerfy-api)
# then wait for a NEW MainPID; measured 2026-11-19: healthy ~5 s after the TERM.
```

Unlike a HUP **this is a real outage**: there is one API instance and nothing in front of
:8001, and a single job-board page view fans out ~10 API calls. Say so before you do it.

Two things to check, because neither shows up in `systemctl`:

- **Before**: a full restart runs `Base.metadata.create_all()` (`app/db.py`), so confirm
  `app/models.py` and `alembic/` are clean in this shared tree first — a restart from a
  dirty tree creates tables prod has no migration for.
- **After**: verify the *environment*, not the process. The unit and the listener look
  identical whether or not the new value arrived. Read the master's own environ, and
  count or fingerprint — never echo a secret into a terminal:

```bash
PID=$(systemctl show -p MainPID --value offerfy-api)
tr '\0' '\n' < /proc/$PID/environ | grep -c '^OPENAI_API_KEY='
```

For the two LLM keys there is an end-to-end check as well: `/api/v1/models` lists a
provider's models only when that provider has a credential, so a short list means the API
never saw the key. Both keys take a comma-separated **list** now — see
`app/services/api_keys.py`.

Emergency single-instance restart, if the load balancer itself is the problem:
`kill -TERM $(systemctl show -p MainPID --value offerfy-web)`, then wait for a NEW MainPID.
Expect up to ~40 s; the system unit has no `TimeoutStopSec` cap (the user units do).

## 5. Smoke after every deploy

**`ops/verify-capacity.sh` runs most of this for you** — worker count by listener, the
connection budget read out of `app/db.py`, every instance in the load balancer answering,
and the four public probes. Run it after any change to workers, `max_connections`, or the
instance set. The manual version:

```bash
systemctl is-active offerfy-api offerfy-web
systemctl --user is-active offerfy-web@3201 offerfy-web@3202
curl -sS -o /dev/null -w '%{http_code}\n' -L https://offerfy.cc/          # 200
curl -sS -o /dev/null -w '%{http_code}\n' https://offerfy.cc/api/healthz  # 200, not optional
curl -sS -o /dev/null -w '%{http_code}\n' https://offerfy.cc/api/readyz   # 200 — this one hits the DB
curl -sS https://offerfy.cc/api/readyz   # and read the `gate` block — see §10
curl -sS -D - -o /dev/null --max-time 15 https://offerfy.cc/api/v1/auth/google/start \
  | grep -i '^location:'   # redirect_uri MUST be offerfy.cc, never 127.0.0.1
```

HTML 200 alone proves nothing: the API can be down while pages still render. To prove
the *new* build is live, grep a string only it has out of a served chunk — a matching
chunk filename does not mean matching content.

**And a 200 does not prove the request went where you think.** Two separate failures on
2026-11-16 came from exactly this. After changing the Service's `targetPort`, the site kept
answering 200 from the *old* path for several minutes while ingress-nginx resynced — so the
smoke passed, and a later restart of the old target took the site down for 4 s. Check the
controller's own view, not the status code:

```bash
POD=$(kubectl -n ingress-nginx get pod -l app.kubernetes.io/component=controller -o name | head -1)
kubectl -n ingress-nginx exec "$POD" -- curl -s localhost:10246/configuration/backends \
  | grep -o '"address":"[^"]*","port":"[0-9]*"'     # MUST show 192.168.49.1:3210
docker logs --tail 20 offerfy-web-lb-1 | grep -oE '127\.0\.0\.1:32[0-9][0-9]' | sort | uniq -c
```

When counting the load balancer's log to prove requests are spread, **count only the new
lines** — take a `wc -l` before and after. A `--tail N` window silently includes earlier
probes, which is how "4/4/4" looked like proof when traffic was in fact still bypassing the
proxy entirely.

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
# the weekly digest's three units install the same way (offerfy-digest*) and are ENABLED
# (checked 2026-11-16 — this line used to say they stay un-enabled; see "The weekly digest timer")
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
not Ashby; Anduril is `andurilindustries`), and the file is now 82 boards that all answer
(79 until Microsoft, Synology and LINE were added 2026-11-15).

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

**The whole LINE family is filed as one employer, "LINE".** The site carries 13 legal
entities (LINE Taiwan, LINE Pay Taiwan, LINE Plus, LINE Bank Taiwan, IPX…) and `fetch_line`
deliberately discards that: every posting takes the board row's `company` and each
posting's own `companies[0].name` is ignored. This is also why `ats_boards.json` has a
second exception to "slug is the employer" — for `line` the slug is the *site*.

**It was built the other way first, and collapsed on 2026-11-15 by request.** Both
behaviours work, so this is a product call rather than a bug fixed, and the cost is real:
`/jobs/employers/line-taiwan` and nine siblings no longer exist, LINE Pay Taiwan is not
distinguishable from LINE Bank Taiwan (a bank), and `IPX` — whose name is not "LINE"
anything — is filed under LINE too. What it buys is one employer page over the whole
family carrying the LINE mark, instead of ten pages whose two-letter monograms collided
four ways on "LP" (LINE Pay Taiwan, LINE Plus, LINE Pay Plus, LINE Pay (Thailand) Korea
Office) and twice on "LF".

Reversing it is a one-line change in `_normalize_summary` and nothing upstream was lost —
the entity is still in the list payload. Note that `upsert_jobs` assigns `row.company`
unconditionally, so a flip in either direction renames every existing row on the next pass
rather than leaving half the catalogue under old names.

**The mark is keyed `"LINE"` exactly, which is what makes the collapse tidy.** `markFor` is
an exact-string lookup, so a per-entity catalogue would have needed either ten keys or a
family prefix rule — and a prefix rule is a trap here, because **`Linear` starts with
`Line`** and has a mark of its own. `/^line/i` hijacks it; `/^LINE\b/` does not. Collapsing
avoids the question entirely.

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

**Synology has a provider of its own** (`services/jobs/synology.py`, migration
`038_synology_source`, added 2026-11-15, the same day as `line` and independently of it —
neither is "the fifth", they are two). It is on none of the four ATS providers (every
slug tried 404s, and its SmartRecruiters 200 is the `totalFound: 0` trap) and it has no
Workday tenant (422, the same status the nonsense control tenant returns). What it has is
its own keyless JSON API at `career.synology.com/api`, with `robots.txt` set to
`Disallow:` with an empty value — allow all. Two endpoints, no key and no token:

- `GET /api/enum/{location}?locale=en-global` is the **list**, and it is unusually cheap:
  the full `description` is **inline on every row** — byte-identical to the detail call's,
  checked across all 32 postings. `HQ` is the only location and the endpoint is **not
  paginated**, so one request returns the whole board, Vietnam and Thailand postings
  included.
- `GET /api/position/{location}/{id}?locale=en-global` is the detail, and the only thing
  it adds that we keep is **`other_condition`** — the requirements block, filled on 32 of
  32 and absent from the list.

So this source pays for a detail call it does not strictly need, and that is deliberate:
`other_condition` is the half of a posting this product cares most about, and the whole
board is 32 postings (~19 after scoping), where the same choice at workday would be 12k.
**Because the list row is already a complete posting, a detail call that fails falls back
to it rather than dropping the posting** — `_detail_or_none` swallows, and
`test_a_failed_detail_call_keeps_the_posting` pins it. `MAX_DETAIL_CALLS` is a cost guard
of the same kind: a posting past it still ships from its list row, because a cap that
dropped postings would take them out of `seen_ids` and have `expire_missing` deactivate
live jobs.

**`slug` is a location id (`HQ`), not an employer handle** — the one row in
`ats_boards.json` where that is true, because Synology publishes one API for the company
rather than a board per employer. It stays in the slug field so `source_id` is `HQ:204`
and `expire_missing`'s prefix scopes per location exactly as it does elsewhere;
`test_jobs_sources.py` pins it, since a well-meaning "fix" to `synology` would ask the API
for a location that does not exist and read as a missing board.

Two quirks worth knowing. **There is no posted date anywhere, in either payload, at any
locale**, so `posted_at` is always `None` — not an oversight to be repaired by inventing
one. And the API serves **plain text**, not HTML: handing that to `sanitize_html`
unchanged renders a posting as one unbroken paragraph, so `_html_from_text` builds the
paragraphs and lists. Note what it has to do with bullets — Synology writes a list with
its lead-in inside the same block ("You are expected to have:" and then a column of
`- `), and in zh-TW it mixes `- ` with fullwidth `－` in one list, so lines are grouped
into runs rather than blocks being classified whole. Titles are mixed zh-TW/English
(`Product Developer (資安系統程式開發)`), and the English keep needle is what scopes them;
that works only because `title_in_scope` keeps unknown titles.

**Adding a `JOB_SOURCES` value is the one catalogue change that is NOT safe to land
before its migration.** Every other board edit is: `offerfy-ingest.service` runs
`-m app.jobs.ingest_catalog` out of this shared checkout, with no build step, so a new row
in `ats_boards.json` is simply live at the next 6-hourly pass. A new *source* is different,
because the row goes live against a `job_source_values` check constraint that production
has not widened yet, and **`upsert_jobs` sits outside `ingest_board`'s `try`** — only
`_fetch` is inside it. So the IntegrityError does not degrade to one failed board the way a
dead ATS does; it propagates out through `ingest_catalog` and takes down the rest of the
pass with it, including `rescore_all`. Run `alembic upgrade head` (deploy.md hard rule 11)
*before* the next timer fire, not as a follow-up. Verified 2026-11-15 by migrating a
throwaway Postgres and running `ingest_board` against it end to end; do it that way rather
than against prod.

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
Adobe and Workday itself, plus Trend Micro and Logitech. **15 boards, ~12k postings**, in
`apps/backend/data/workday_boards.json`.

**These boards sit behind Cloudflare, and `board not found` used to be how that looked.**
On 2026-11-19 all 15 boards failed with `board not found`, which reads as a config problem.
It was not one: the edge was answering this host's IP with an HTML *"Security Check — Enable
JavaScript and cookies to continue"* page under status **429**, naming the IP in the body.
httpx cannot solve a JS challenge, so every request failed. `workday._post` collapsed every
non-200 into `None` and `ingest_workday` labelled `None` as `board not found`, so the one
diagnosis the table offered pointed at the only thing that was right.

Fixed the same day: only `404/406/422` — the statuses that genuinely mean no such board, per
"Adding a board" below — still return `None`. Everything else raises `WorkdayUnavailable`
and the ingest row carries the real status. Mid-pagination failures raise too, which also
closes a data-loss hole: a short read used to be returned as if complete, and the caller
hands those ids to `expire_missing`, so one throttled page in a 145-page board deactivated
the whole tail of it.

**How little it takes to trip, measured.** During that investigation `micron` was the one
tenant still answering 200 with real data. Roughly **15 diagnostic requests later it was
challenged as well**, and the other tenants stayed challenged for hours. The reputation is
per-IP, it spans the whole `myworkdayjobs.com` estate, it decays slowly, and it is not
UA-dependent — a Chrome UA gets the same 429. So:

* **Do not probe these boards in a loop.** A handful of requests is a real cost. If you are
  checking whether a board is alive, check one, and know that a 429 tells you nothing about
  that board's config.
* `workday.DETAIL_DELAY_SECONDS` (0.4s) paces the detail calls, which is where the volume
  is — one request per new posting. It costs a cold 4000-posting board ~27 extra minutes and
  costs steady state almost nothing, since only genuinely new rows are fetched. Do not set
  it to 0 to make a backfill finish faster; that is what buys the outage.
* A `429` in the ingest table means **wait**, not **fix the board**. There is nothing to
  repair; the next daily pass recovers once the reputation decays.

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

**Logitech joined 2026-11-15; its site slug is `Logitech`, not `External`.**
`logitech.wd5` answers 200 with 220 postings on site `Logitech`, and **404s on `External`**
— worth knowing because `External` is the slug nine of the other fourteen boards use, so the
obvious first guess is the one that fails here. 145 of the 220 pass `title_in_scope` (66%),
the rest marketing, finance and territory sales. It brings Hsinchu and Taipei hardware and
firmware roles, which is the nearest the catalogue gets to the Taiwanese hardware tier that
TSMC and MediaTek would have supplied.

**It is not TSMC or MediaTek, and it cannot be.** Checked 2026-10-24: TSMC returns 403 to
every request including its own `robots.txt`, and MediaTek runs a bespoke Strapi site with
no public jobs API. Both would need scraping, which this project does not do. Do not add a
Workday row for either; they have no tenant.

**TSMC was re-probed 2026-11-15 on request and nothing has changed.** `careers.tsmc.com`,
`www.tsmc.com/english/careers` and `careers.tsmc.com/robots.txt` all still return 403 with a
~5.5 KB block page, and `tsmc`, `tsmcus`, `tsmcna`, `tsmcarizona`, `taiwansemiconductor`,
`tsmcamerica`, `tsmcnorthamerica` and `tsmceurope` all 404 on Greenhouse, Lever and Ashby;
`tsmc.wd1`, `wd3` and `wd5` each answer **422, the same status the nonsense control tenant
returns**, so none of that is evidence of a tenant. Note for whoever re-checks next: the
Workday wildcard host answered **422** for a nonsense tenant this time, not the 406 recorded
above — so compare a candidate against a control slug in the same pass rather than against a
status written down a month ago.

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
timer"). It spends real money.

**It is enabled and armed, and this file said otherwise until 2026-11-16.** Live state as
checked that day: `systemctl --user is-enabled offerfy-digest.timer` → `enabled`,
`is-active` → `active`, `NextElapseUSecRealtime` → Mon 2026-11-16 05:08 CST — and
`LastTriggerUSec` is **empty** with no journal entries, so it has never actually fired. In
other words a money-spending job that two places in this file described as "not enabled" was
one scheduling slot away from its first run. If you are reading this and the behaviour needs
to change, `systemctl --user disable --now offerfy-digest.timer`; do not assume from the
prose which way it is set, check it.

## 10. Capacity: workers, the pool, and the connection budget

Raised 2026-11-16, all of it with zero downtime and no sudo.

| | before | now | where |
|---|---|---|---|
| Postgres `max_connections` | 100 (stock) | **250** | `docker-compose.yml`, `command:` |
| Postgres `shared_buffers` | 128 MB (stock) | **1 GB** | same — the DB is ~540 MB, so it now fits in cache |
| API DB pool | 15+10 = 25/worker | **30+10 = 40/worker** | `apps/backend/app/db.py` |
| uvicorn workers | 2 | **2** (unchanged) | `ops/systemd/offerfy-api.service` |

**Why the pool and not the worker count.** The pool was the binding constraint, not the
workers: FastAPI runs every sync `def` handler in anyio's threadpool, which defaults to
**40 threads per process** (measured, not assumed), so at 25 connections per worker the pool
ran dry while threads were still free. It also ships with a `kill -HUP`, where a
worker-count change would need `daemon-reload` and root.

> **That raise matched the pool to the thread limiter, 40 and 40, "so neither is silently
> the next wall". That reasoning was wrong and it took the job board down on 2026-11-20.**
> A request holds its pool connection from its first query until its dependency teardown
> gets a threadpool thread *again*, so for part of its life it holds a connection and no
> thread — which means connections in use track requests **in flight**, not threads. Once
> in-flight passed 40 the worker jammed: handlers blocked in checkout holding threads, the
> teardowns that would free a connection queued behind them, and `pool_timeout=3` turned it
> into ~10k `QueuePool limit of size 30 overflow 10 reached` 500s an hour — a third to a
> half of all job board traffic — while Postgres sat idle at 76 of 250 connections and
> `/healthz`, `/readyz`, `systemctl` and the listener all looked fine.
>
> **Lowering the thread limit makes it worse, so do not reach for that.** Teardown needs a
> thread too; starve threads and connections are held *longer*. Measured on one worker
> against the live catalogue:
>
> | in flight | threads 40 | threads 16 | **gated at 32** |
> |---|---|---|---|
> | 60 | 100%, 40 req/s | 98.8%, 43 req/s | **100%, 56 req/s** |
> | 150 | 68.3%, 19 req/s | 21.2%, 8 req/s | **100%, 55 req/s** |
> | 300 | 24.3%, 15 req/s | 0%, 10 req/s | **100%, 50 req/s** |

**What actually bounds it now: the in-flight gate.** `apps/backend/app/concurrency.py`
installs `InFlightGate` as the outermost middleware, admitting **32 requests per worker** and
making the rest *wait* — a queued request is slow, a request that cannot get a connection is
a 500. Because a slot is held for the whole request, checked-out connections can never
exceed 32 against a 40-slot pool, so **the pool cannot be exhausted by request load at all**.
Requests waiting longer than 10s get a `503` with `Retry-After` instead of queueing forever.

Three things about it that are easy to get wrong:

- **The ceiling must stay below the pool** (`MAX_IN_FLIGHT < pool_size + max_overflow`).
  `tests/test_pool_thread_headroom.py` asserts it from both numbers in source, and
  `ops/verify-capacity.sh` checks it against what the *running* workers report.
- **`/healthz`, `/readyz` and the SSE streams bypass the gate.** A stream does not finish
  until it ends, so gating one would hold a slot for minutes — the same shape as the bug
  `tests/test_sse_sessions.py` pins for pool connections, one layer up. A new SSE route must
  be added to `UNGATED_PATTERN`; the test walks the real routes and fails if it is not.
- **`GET /readyz` now reports saturation**, so you do not have to infer it:
  `{"status":"ok","gate":{"limit":32,"in_flight":3,"waiting":0,"shed":0}}`. `in_flight` at
  `limit` with `waiting` above zero is a saturated worker. During the 2026-11-20 incident
  this endpoint answered a bare `"ok"` while a third of traffic failed.

It is a per-worker ceiling, so two workers admit 64 at once; the pool budget below is
per-worker and unchanged.

### One worker can hold nearly every connection, and that is expected

The two uvicorn workers share one listening socket, and the three `next-server` instances
keep HTTP keep-alive pools — so each socket stays pinned to whichever worker accepted it.
Whichever worker comes up first takes essentially everything. Measured 2026-11-20, twice,
including straight after a HUP:

```
worker A  650–963 established connections to :8001
worker B    2–60
```

**This is not a bug to chase, and `ops/verify-capacity.sh` cannot see it** — it counts
socket *holders* (master + N workers), which looks right either way. It mattered before the
gate because all that traffic landed on one worker's 40-slot pool; with the gate, a worker
admits 32 whatever its connection count, so the imbalance is harmless. If you want to see
it:

```bash
ss -tnp state established | grep ':8001' | grep -oE 'pid=[0-9]+' | sort | uniq -c | sort -rn
```

Do not try to "fix" it by matching pool to connections. Balancing it properly needs each
worker on its own port behind nginx `least_conn` (the shape §2 already uses for the
frontend), which is a real change and has not been needed.

**The budget, against `max_connections=250`:**

```
API       2 workers x 40                     =  80
ingest    3 timers  x 40 (worst case)        = 120   a batch pass really uses a handful
reserved  superuser_reserved_connections     =   3
                                               ---
                                               203  of 250
```

`pool_size` is a **ceiling, not a reservation** — SQLAlchemy opens connections lazily, so
steady state sits far below it (measured idle: ~22–33). Plan against the ceiling anyway,
because the ceiling is what gets reached under exactly the load that matters.

**If you ever raise `--workers`, redo this arithmetic first.** At 40 slots per worker, 4
workers is a 160-connection ceiling and three concurrent timers add 120 — that is 283, over
the limit. `ops/verify-capacity.sh` does the sum for you and reads the pool numbers out of
`db.py` rather than keeping its own copy.

**`UVICORN_WORKERS` was deleted from `.env.production` and from `k8s/base/configmap.yaml`.**
Nothing read it — the count is hardcoded in `ExecStart` — and a config key that silently does
nothing is worse than no key. `UVICORN_HOST`/`UVICORN_PORT` in the configmap are equally
unread by `scripts/start-backend.sh`; they only survive because they happen to agree with it.

### The firewall, because it will surprise you

UFW allows the cluster to reach **exactly two ports** on this host:

```
### tuple ### allow tcp 3200 0.0.0.0/0 any 192.168.49.0/24 in
### tuple ### allow tcp 3210 0.0.0.0/0 any 192.168.49.0/24 in   # added 2026-11-16
```

3201 and 3202 are deliberately **not** open — they are reachable only through `web-lb`. If
you add a fourth instance, it needs no rule; if you move the load balancer's port, it does.
The chain also ends with a pre-existing `-s 140.112.0.0/12 -j ACCEPT`, so campus can already
reach every port here; that is not new and it applies to 3200 just the same.

A corollary worth knowing: **a port that is listening on the host can still be unreachable
from the cluster**, and the symptom is a timeout, not a refusal. A *published docker port*
is the exception — those go through Docker's own DNAT chains and bypass the UFW INPUT
policy entirely, which is what `docs/deploy.md` hard rule 9 is warning about.
