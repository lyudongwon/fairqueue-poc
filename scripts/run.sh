#!/bin/sh
# One measured run. Usage: scripts/run.sh <SCENARIO> <REP>
# Env: MODE (gateway mode), CRAWLER (none|naive|rotating|...), C, WARMUP, DURATION, RATE
set -eu
SCEN=${1:?scenario id}
REP=${2:-1}
export MODE=${MODE:-none} CRAWLER=${CRAWLER:-none} C=${C:-0}
export RATE_PER_IP=${RATE_PER_IP:-2} BURST_PER_IP=${BURST_PER_IP:-2} GLOBAL_N=${GLOBAL_N:-4} ANON_N=${ANON_N:-2} COOKIE_M=${COOKIE_M:-4}
export FQ_SLOTS=${FQ_SLOTS:-4} PROBATION_S=${PROBATION_S:-600} HUMAN_COOKIE_AGE_S=${HUMAN_COOKIE_AGE_S:-86400}
export ANON_SPLIT=${ANON_SPLIT:-} HUMAN_COOKIELESS=${HUMAN_COOKIELESS:-}
case "$MODE" in fq-*) export REGISTER_URL=http://gateway:9090/seen ;; *) export REGISTER_URL= ;; esac
export WARMUP=${WARMUP:-30} DURATION=${DURATION:-180} RATE=${RATE:-3} SEED=$REP

# Never measure uncommitted code (results/ is output, so it is exempt).
if [ -n "$(git status --porcelain -- . ':!results')" ]; then
  echo "refusing to run: uncommitted changes outside results/" >&2
  git status --short -- . ':!results' >&2
  exit 1
fi

RUN_ID="${SCEN}_${MODE}_${CRAWLER}_c${C}_r${REP}_$(date +%Y%m%dT%H%M%S)"
export RUN_DIR="results/runs/$RUN_ID"
mkdir -p "$RUN_DIR/raw"
export MSYS_NO_PATHCONV=1
DC="docker compose --profile run"
LOADERS=human
[ "$CRAWLER" != none ] && LOADERS="human crawler"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "== $RUN_ID"

$DC rm -sf human crawler monitor >/dev/null 2>&1 || true
docker compose restart gitea >/dev/null
docker compose up -d --force-recreate gateway >/dev/null
docker compose --profile tools run --rm tools sh -c \
  'until curl -sf -o /dev/null http://gateway:8080/api/healthz; do sleep 1; done'

$DC create $LOADERS >/dev/null
TARGETS="gitea=$(docker compose ps -q gitea) gateway=$(docker compose ps -q gateway)"
for s in $LOADERS; do TARGETS="$TARGETS $s=$($DC ps -aq $s)"; done
export TARGETS
$DC up -d monitor >/dev/null
sleep 2
$DC start $LOADERS >/dev/null
status=ok
for s in $LOADERS; do
  [ "$(docker wait "$($DC ps -aq $s)")" = 0 ] || status=failed
  $DC logs --no-log-prefix $s > "$RUN_DIR/raw/$s.log" 2>&1 || true
done

$DC stop monitor >/dev/null
docker compose stop gateway >/dev/null   # SIGTERM -> flush gateway log
ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cat > "$RUN_DIR/meta.json" <<EOF
{"run_id": "$RUN_ID", "scenario": "$SCEN", "rep": $REP, "mode": "$MODE", "crawler": "$CRAWLER",
 "c": $C, "gateway_params": {"rate_per_ip": $RATE_PER_IP, "burst_per_ip": $BURST_PER_IP, "global_n": $GLOBAL_N, "anon_n": $ANON_N, "cookie_m": $COOKIE_M, "fq_slots": $FQ_SLOTS, "probation_s": $PROBATION_S, "human_cookie_age_s": $HUMAN_COOKIE_AGE_S, "anon_split": "$ANON_SPLIT", "human_cookieless": "$HUMAN_COOKIELESS"},
 "warmup_s": $WARMUP, "duration_s": $DURATION, "human_rate": $RATE, "seed": $SEED,
 "git": "$(git rev-parse --short HEAD)",
 "started": "$started", "ended": "$ended", "status": "$status"}
EOF
docker compose --profile tools run --rm tools python analysis/summarize.py "$RUN_DIR" > /dev/null
echo "== $RUN_ID $status"
