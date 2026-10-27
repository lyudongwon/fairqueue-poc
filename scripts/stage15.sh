#!/bin/sh
# Stage 15: adaptive cookie bounce. The gateway only bounces cookieless first visits while the anonymous
# queues are congested (BOUNCE_ADAPTIVE=8 waiting requests, held for 10 s). Cookie-accepting crawlers keep
# using the service's own cookie as before. S26 = BOUNCE=1 BOUNCE_ADAPTIVE=8 NEW_SPLIT=class.
set -eu
cd "$(dirname "$0")/.."
export C=${C:-50} FQ_SLOTS=${FQ_SLOTS:-4} PROBATION_S=${PROBATION_S:-600} MODE=fq-cost-probation ANON_SPLIT=class HUMAN_FIRSTVISIT=1 IP_SPLIT= NEW_ORDER=
run() {
  for f in results/runs/${1}_*_r${2}_*/summary.json; do
    if [ -f "$f" ]; then echo "== skip $1 r$2"; return 0; fi
  done
  sh scripts/run.sh "$@" || true
}
for rep in $(seq 1 ${REPS:-1}); do
  for kind in rotating cookie-keeper cookie-farm; do
    CRAWLER=$kind BOUNCE=1 BOUNCE_ADAPTIVE=${BOUNCE_ADAPTIVE:-8} NEW_SPLIT=class run S26_$kind $rep
  done
done
python3 analysis/aggregate.py || true
