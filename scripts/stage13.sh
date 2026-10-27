#!/bin/sh
# Stage 13: bounce + probation pool split by class + newest-first (LIFO) order inside the probation pools.
#   S25 = BOUNCE=1 NEW_SPLIT=class NEW_ORDER=lifo ; compare with S21 / S22 / S23.
set -eu
cd "$(dirname "$0")/.."
export C=${C:-50} FQ_SLOTS=${FQ_SLOTS:-4} PROBATION_S=${PROBATION_S:-600} MODE=fq-cost-probation ANON_SPLIT=class HUMAN_FIRSTVISIT=1 IP_SPLIT=
run() {
  for f in results/runs/${1}_*_r${2}_*/summary.json; do
    if [ -f "$f" ]; then echo "== skip $1 r$2"; return 0; fi
  done
  sh scripts/run.sh "$@" || true
}
for rep in $(seq 1 ${REPS:-1}); do
  for kind in rotating cookie-keeper cookie-farm; do
    CRAWLER=$kind BOUNCE=1 NEW_SPLIT=class NEW_ORDER=lifo run S25_$kind $rep
  done
done
python3 analysis/aggregate.py || true
