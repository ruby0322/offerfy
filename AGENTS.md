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
- **There are no frontend tests.** `npm run lint` and `npm run build:public` are the only
  automated gates, so verify UI changes by actually running the app. A stub serving
  `/v1/resumes/<id>`, `/preview`, `/ats` and `/messages` is enough to drive the editor
  without the real backend; point `next dev` at it with `BACKEND_INTERNAL_URL`.
