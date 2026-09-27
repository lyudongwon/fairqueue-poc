#!/bin/sh
# One measured run. Usage: scripts/run.sh <SCENARIO> <REP>
# Env: MODE (gateway mode), CRAWLER (none|naive|rotating|...), C, WARMUP, DURATION, RATE
set -eu
SCEN=${1:?scenario id}
REP=${2:-1}
export MODE=${MODE:-none} CRAWLER=${CRAWLER:-none} C=${C:-0}
export WARMUP=${WARMUP:-30} DURATION=${DURATION:-180} RATE=${RATE:-3} SEED=$REP
RUN_ID="${SCEN}_${MODE}_${CRAWLER}_c${C}_r${REP}_$(date +%Y%m%dT%H%M%S)"
export RUN_DIR="results/runs/$RUN_ID"
mkdir -p "$RUN_DIR/raw"
export MSYS_NO_PATHCONV=1
DC="docker compose --profile run"
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "== $RUN_ID"

$DC rm -sf human monitor >/dev/null 2>&1 || true
docker compose restart gitea >/dev/null
docker compose up -d --force-recreate gateway >/dev/null
docker compose --profile tools run --rm tools sh -c \
  'until curl -sf -o /dev/null http://gateway:8080/api/healthz; do sleep 1; done'

$DC create human >/dev/null
export TARGETS="gitea=$(docker compose ps -q gitea) gateway=$(docker compose ps -q gateway) human=$($DC ps -aq human)"
$DC up -d monitor >/dev/null
sleep 2
$DC start human >/dev/null
status=ok
[ "$(docker wait "$($DC ps -aq human)")" = 0 ] || status=failed
$DC logs --no-log-prefix human > "$RUN_DIR/raw/human.log" 2>&1 || true

$DC stop monitor >/dev/null
docker compose stop gateway >/dev/null   # SIGTERM -> flush gateway log
ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cat > "$RUN_DIR/meta.json" <<EOF
{"run_id": "$RUN_ID", "scenario": "$SCEN", "rep": $REP, "mode": "$MODE", "crawler": "$CRAWLER",
 "c": $C, "warmup_s": $WARMUP, "duration_s": $DURATION, "human_rate": $RATE, "seed": $SEED,
 "git": "$(git rev-parse --short HEAD)$(git diff --quiet || echo -dirty)",
 "started": "$started", "ended": "$ended", "status": "$status"}
EOF
docker compose --profile tools run --rm tools python analysis/summarize.py "$RUN_DIR"
echo "== $RUN_ID $status"
