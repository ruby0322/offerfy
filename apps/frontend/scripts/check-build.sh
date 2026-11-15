#!/usr/bin/env bash
# Is .next a complete build, safe to restart offerfy-web onto?
#
# `npm run build:public` exits 0 even when the Google-font fetch fails: it
# emits CSS referencing faces it never downloaded and leaves .next with no
# BUILD_ID and no manifests. The live `next start` keeps serving what it
# already holds, so nothing looks wrong until the restart, which then has
# nothing to serve. Observed twice on 2026-11-15; a retry fixed it both
# times, and once the restart went ahead anyway and took the site to 502.
#
# Usage:  ./scripts/check-build.sh [build-log]   # exits non-zero if unsafe
# Run it as a guard, not as an echo:
#   ./scripts/check-build.sh log.txt && kill "$(systemctl show -p MainPID --value offerfy-web)"

set -uo pipefail

NEXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.next"
LOG="${1:-}"
fail=0

for f in BUILD_ID routes-manifest.json app-path-routes-manifest.json; do
  if [ -f "$NEXT_DIR/$f" ]; then
    printf '  ok      %s\n' "$f"
  else
    printf '  MISSING %s\n' "$f"
    fail=1
  fi
done

# The rewrite has to point at the public API, never dest (:8002) or the dev
# frontend (:3001) -- docs/deploy.md hard rule 10.
dest=$(python3 -c "
import json, sys
try:
    m = json.load(open('$NEXT_DIR/routes-manifest.json'))['rewrites']['afterFiles']
    print(','.join(r.get('destination', '') for r in m))
except Exception:
    print('')
" 2>/dev/null)
case "$dest" in
  *127.0.0.1:8001*) printf '  ok      rewrite -> %s\n' "$dest" ;;
  *)                printf '  BAD     rewrite -> %s\n' "${dest:-<none>}"; fail=1 ;;
esac

# Fonts are downloaded at build time; no faces means the CSS points at
# nothing. Match both spellings the log uses.
woff=$(ls "$NEXT_DIR"/static/media/*.woff2 2>/dev/null | wc -l)
if [ "$woff" -ge 8 ]; then
  printf '  ok      %s woff2 files\n' "$woff"
else
  printf '  BAD     only %s woff2 files\n' "$woff"
  fail=1
fi

if [ -n "$LOG" ] && [ -f "$LOG" ]; then
  errs=$(grep -ciE 'module-not-found|Module not found' "$LOG" || true)
  if [ "$errs" = "0" ]; then
    printf '  ok      no module-not-found in the log\n'
  else
    printf '  BAD     %s module-not-found lines in the log\n' "$errs"
    fail=1
  fi
fi

if [ "$fail" = "0" ]; then
  echo "BUILD COMPLETE - safe to restart offerfy-web"
else
  echo "BUILD INCOMPLETE - do NOT restart; rebuild instead"
fi
exit "$fail"
