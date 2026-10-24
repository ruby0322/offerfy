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
# Frontend: SIGTERM, systemd respawns it. ~40 s of downtime — the npx/next process tree
# is slow to exit (it sat in `deactivating` for ~36 s), then `activating` → `active`.
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
not Ashby; Anduril is `andurilindustries`), and the file is now 77 boards that all answer.

Before adding a board, **probe it**; do not assume. The three endpoints are in
`greenhouse.py` / `lever.py` / `ashby.py`, plus `smartrecruiters.py`. Note that probing
many slugs concurrently gets you rate-limited and returns false 404s — space the requests
out and retry anything that is not a definite 404, or you will delete live boards.

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
Adobe and Workday itself. **12 boards, ~12k postings**, in
`apps/backend/data/workday_boards.json`.

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

`job_ingest_runs` holds one row **per board**, ~77 per pass. `ok_count - upserted_count`
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
