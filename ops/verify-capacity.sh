#!/usr/bin/env bash
# Check that the API's worker count, Postgres' connection ceiling and the
# frontend instance set are what we think they are -- and that the three do not
# contradict each other.
#
# Run it after any change to worker count, max_connections, or the number of
# frontend instances. It only reads.
#
# Two traps it exists to avoid:
#   * Never count workers with pgrep on a cmdline: the caller's own command line
#     matches, and `next-server` carries no port in its line at all. Go by the
#     listening sockets (`ss -ltnp`) and the process tree.
#   * Never conclude an API reload worked from the worker count: a SIGHUP can
#     leave a stale worker. The listener is the truth.
set -uo pipefail

PG_CONTAINER=offerfy-postgres-1
NS=offerfy-native-tmp
EP=offerfy-native-3100
LB_ADDR=192.168.49.1
LB_PORT=3210
LB_HEALTH=3211
UPSTREAM_FILE=/home/ruby0322/offerfy/ops/nginx/upstream.d/offerfy-web-upstream.conf
fail=0

hdr() { printf '\n== %s ==\n' "$1"; }
ok()  { printf '  ok    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fail=1; }

hdr "API: uvicorn master and workers"
main=$(systemctl show -p MainPID --value offerfy-api 2>/dev/null)
if [[ -z "$main" || "$main" == "0" ]]; then
  bad "offerfy-api has no MainPID"
else
  # Count by who holds the LISTENING SOCKET, not by children of the master.
  # `ps --ppid` over-counts: uvicorn's master also parents a multiprocessing
  # helper, so `--workers 2` shows 3 children. Every real worker inherits fd 3,
  # so the socket's holder list is master + N workers.
  sock=$(ss -ltnp 2>/dev/null | grep '127.0.0.1:8001' || true)
  if [[ -z "$sock" ]]; then
    bad "nothing is listening on 127.0.0.1:8001"
    workers=0
  else
    ok "listening on 127.0.0.1:8001"
    holders=$(grep -o 'pid=[0-9]*' <<< "$sock" | sort -u | wc -l | tr -d ' ')
    workers=$(( holders - 1 ))          # minus the master
    declared=$(systemctl show -p ExecStart --value offerfy-api | grep -oE '\-\-workers [0-9]+' | grep -oE '[0-9]+' || echo "")
    printf '  MainPID %s, %s socket holder(s) -> %s worker(s); unit declares %s\n' \
      "$main" "$holders" "$workers" "${declared:-'(no --workers flag)'}"
    if [[ -n "$declared" ]]; then
      if (( workers == declared )); then
        ok "running worker count matches the unit"
      else
        # The classic shape: a SIGHUP left a stale worker behind, or the unit was
        # edited without a daemon-reload + restart so ExecStart never took effect.
        bad "unit declares $declared workers but $workers are holding the socket -- stale worker, or the unit was edited without daemon-reload + restart"
      fi
    fi
  fi
fi

hdr "Postgres: ceiling vs what the app can ask for"
settings=$(docker exec "$PG_CONTAINER" psql -U postgres -d offerfy -tAc \
  "select setting from pg_settings where name='max_connections';" 2>/dev/null | tr -d '[:space:]')
if [[ -z "$settings" ]]; then
  bad "could not read max_connections from $PG_CONTAINER"
else
  used=$(docker exec "$PG_CONTAINER" psql -U postgres -d offerfy -tAc \
    "select count(*) from pg_stat_activity;" 2>/dev/null | tr -d '[:space:]')
  shb=$(docker exec "$PG_CONTAINER" psql -U postgres -d offerfy -tAc \
    "select setting::bigint*8/1024 from pg_settings where name='shared_buffers';" 2>/dev/null | tr -d '[:space:]')
  printf '  max_connections=%s  shared_buffers=%sMB  in use now=%s\n' "$settings" "$shb" "$used"

  # Read the per-worker pool from the source rather than hardcoding it. A copy of
  # the number here would drift the moment app/db.py is tuned, and then this
  # script would reassure you with arithmetic about a pool that no longer exists
  # -- the same failure as UVICORN_WORKERS sitting in .env.production unread.
  DB_PY=/home/ruby0322/offerfy/apps/backend/app/db.py
  per_worker=$(python3 - "$DB_PY" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
def grab(name):
    # Module-level constants now (POOL_SIZE = 30), not literals inside the
    # create_engine call (pool_size=30,). They moved there so this script and
    # app/concurrency.py read one source for the number.
    m = re.search(rf'^{name}\s*=\s*(\d+)\s*$', src, re.M)
    return int(m.group(1)) if m else None
ps, mo = grab("POOL_SIZE"), grab("POOL_MAX_OVERFLOW")
print(ps + mo if ps is not None and mo is not None else "")
PY
)
  if [[ -z "$per_worker" ]]; then
    bad "could not read pool_size/max_overflow out of app/db.py -- check the arithmetic by hand"
    per_worker=0
  fi
  if [[ -n "${workers:-}" && "$workers" -gt 0 && "$per_worker" -gt 0 ]]; then
    api_ceiling=$(( workers * per_worker ))
    # Each ingest timer runs the same engine in its own process, so each carries
    # its own pool. Three of them is the worst case.
    worst=$(( api_ceiling + 3 * per_worker + 3 ))
    printf '  pool is %s per worker (from app/db.py)\n' "$per_worker"
    printf '  API ceiling %s (= %s workers x %s), worst case with all 3 timers %s\n' \
      "$api_ceiling" "$workers" "$per_worker" "$worst"
    if (( worst <= settings )); then
      ok "worst case $worst fits inside max_connections=$settings"
    else
      bad "worst case $worst EXCEEDS max_connections=$settings -- raise the ceiling or lower the workers"
    fi
  fi
fi

hdr "Backpressure: in-flight ceiling vs the pool"
# The pool is not what caps concurrency any more -- the in-flight gate is, and
# it has to stay under the pool or the gate is decorative. A request holds a
# pool connection from its first query until its dependency teardown gets a
# threadpool thread, so connections in use track requests IN FLIGHT, not
# threads: that is how 2026-11-20 put 40 connections (a whole worker's pool)
# into `idle in transaction` and served ~10k 500s an hour. See
# apps/backend/app/concurrency.py.
CONC_PY=/home/ruby0322/offerfy/apps/backend/app/concurrency.py
gate_limit=$(grep -oE '^DEFAULT_MAX_IN_FLIGHT\s*=\s*[0-9]+' "$CONC_PY" 2>/dev/null | grep -oE '[0-9]+$')
if [[ -z "$gate_limit" ]]; then
  bad "could not read DEFAULT_MAX_IN_FLIGHT out of app/concurrency.py"
elif [[ -z "${per_worker:-}" || "$per_worker" -eq 0 ]]; then
  bad "no pool number to compare the gate against"
else
  printf '  in-flight ceiling %s per worker, pool %s per worker\n' "$gate_limit" "$per_worker"
  if (( gate_limit < per_worker )); then
    ok "gate $gate_limit is below the pool's $per_worker -- the pool cannot be exhausted by request load"
  else
    bad "gate $gate_limit is NOT below the pool's $per_worker -- requests can still exhaust the pool and 500"
  fi
fi

# And what the running workers actually believe, which is the only authority.
live_gate=$(curl -sS --max-time 5 http://127.0.0.1:8001/readyz 2>/dev/null \
  | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
g=d.get("gate") or {}
print(g.get("limit") if g.get("limit") is not None else "")' 2>/dev/null)
if [[ -z "$live_gate" ]]; then
  bad "/readyz reported no gate -- the running workers predate the in-flight gate, or it is not installed"
elif [[ "$live_gate" == "$gate_limit" ]]; then
  ok "running workers report the same ceiling ($live_gate)"
else
  bad "app/concurrency.py says $gate_limit but the running workers say $live_gate -- they need a HUP"
fi

hdr "Routing: does the cluster point at the load balancer?"
# ingress-nginx routes by the Service's targetPort, NOT by the Endpoints/slice port.
tport=$(kubectl -n "$NS" get svc "$EP" -o jsonpath='{.spec.ports[0].targetPort}' 2>/dev/null)
[[ "$tport" == "$LB_PORT" ]] && ok "Service targetPort is $tport" \
  || bad "Service targetPort is '$tport', expected $LB_PORT -- the cluster is bypassing the load balancer"
# What the controller itself believes, which is the only authority on where traffic goes.
ipod=$(kubectl -n ingress-nginx get pod -l app.kubernetes.io/component=controller -o name 2>/dev/null | head -1)
if [[ -n "$ipod" ]]; then
  ups=$(kubectl -n ingress-nginx exec "$ipod" -- curl -s --max-time 5 localhost:10246/configuration/backends 2>/dev/null \
        | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
for b in d:
    if "offerfy" in b.get("name",""):
        print(",".join(str(e.get("address"))+":"+str(e.get("port")) for e in (b.get("endpoints") or [])))' 2>/dev/null)
  [[ "$ups" == "$LB_ADDR:$LB_PORT" ]] && ok "ingress upstream is $ups" \
    || bad "ingress upstream is '$ups', expected $LB_ADDR:$LB_PORT (it can lag minutes behind a targetPort change)"
fi

hdr "Load balancer and the instances behind it"
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://$LB_ADDR:$LB_PORT/" 2>/dev/null || echo 000)
[[ $code =~ ^[23] ]] && ok "lb on $LB_ADDR:$LB_PORT answering ($code)" \
  || bad "lb on $LB_ADDR:$LB_PORT answered $code -- the public site will be 504"
lbh=$(curl -sS --max-time 5 "http://127.0.0.1:$LB_HEALTH/lb-health" 2>/dev/null | tr -d '\n')
[[ "$lbh" == "ok" ]] && ok "lb health endpoint ok" || bad "lb health endpoint said '$lbh'"

# The instance set lives in the nginx upstream file now, not in the Endpoints.
mapfile -t up_ports < <(grep -oE 'server 127\.0\.0\.1:[0-9]+' "$UPSTREAM_FILE" 2>/dev/null | grep -oE '[0-9]+$')
mapfile -t down_ports < <(grep -E 'server 127\.0\.0\.1:[0-9]+.*\bdown;' "$UPSTREAM_FILE" 2>/dev/null | grep -oE '127\.0\.0\.1:[0-9]+' | cut -d: -f2)
if [[ ${#up_ports[@]} -eq 0 ]]; then
  bad "no upstreams found in $UPSTREAM_FILE"
else
  printf '  upstreams: %s\n' "${up_ports[*]}"
  (( ${#down_ports[@]} == 0 )) && ok "none marked down" \
    || bad "marked down: ${down_ports[*]} -- a rolling restart is mid-flight, or one was left drained"
  for p in "${up_ports[@]}"; do
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$p/" 2>/dev/null || echo 000)
    [[ $code =~ ^[23] ]] && ok ":$p answering ($code)" \
      || bad ":$p is an upstream but answered $code -- the lb will send it traffic it cannot serve"
  done
  (( ${#up_ports[@]} >= 2 )) && ok "${#up_ports[@]} instances, so a rolling restart is possible" \
    || bad "only one instance: a restart is downtime, not a rolling update"
fi

hdr "Public smoke (AGENTS.md section 5)"
for probe in "https://offerfy.cc/|200|site" "https://offerfy.cc/api/healthz|200|api/healthz" "https://offerfy.cc/api/readyz|200|api/readyz (hits the DB)"; do
  IFS='|' read -r url want label <<< "$probe"
  got=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 -L "$url" 2>/dev/null || echo 000)
  [[ "$got" == "$want" ]] && ok "$label $got" || bad "$label returned $got, wanted $want"
done
redir=$(curl -sS -D - -o /dev/null --max-time 20 https://offerfy.cc/api/v1/auth/google/start 2>/dev/null \
  | grep -i '^location:' | grep -oE 'redirect_uri=[^&]*' || true)
case "$redir" in
  *offerfy.cc*) ok "oauth redirect_uri points at offerfy.cc" ;;
  "")           bad "no Location header on /api/v1/auth/google/start" ;;
  *)            bad "oauth redirect_uri is NOT offerfy.cc: $redir" ;;
esac

printf '\n%s\n' "$( (( fail )) && echo 'RESULT: something above FAILED' || echo 'RESULT: all checks passed' )"
exit "$fail"
