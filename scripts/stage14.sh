#!/bin/sh
# Stage 14: overhead without an attack. Cookie-holding humans only (no crawler, C=0), final scheduler config.
#   S20 = fq-cost-probation + ANON_SPLIT=class ; baseline S0 (no gateway scheduling) already exists.
set -eu
cd "$(dirname "$0")/.."
export C=0 CRAWLER=none FQ_SLOTS=${FQ_SLOTS:-4} PROBATION_S=${PROBATION_S:-600} MODE=fq-cost-probation ANON_SPLIT=class BOUNCE= NEW_SPLIT= NEW_ORDER= IP_SPLIT=
run() {
  for f in results/runs/${1}_*_r${2}_*/summary.json; do
    if [ -f "$f" ]; then echo "== skip $1 r$2"; return 0; fi
  done
  sh scripts/run.sh "$@" || true
}
for rep in $(seq 1 ${REPS:-2}); do
  run S20 $rep
done
python3 analysis/aggregate.py || true
