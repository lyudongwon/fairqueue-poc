#!/bin/sh
# Stage 10: cookie bounce (first-visit fix). Every visit of the human simulator is a brand-new,
# cookieless visitor (HUMAN_FIRSTVISIT=1). Crawlers: rotating / cookie-keeper / cookie-farm, C=50, 3 reps.
#   S21  no bounce (baseline)   S22  BOUNCE=1 (gateway issues a signed cookie by 302)
# Same scheduler both times: fq-cost-probation + class-split anonymous queues.
# Finished runs are skipped, so this can be re-run after an interruption.
set -eu
cd "$(dirname "$0")/.."
export C=${C:-50} FQ_SLOTS=${FQ_SLOTS:-4} PROBATION_S=${PROBATION_S:-600} MODE=fq-cost-probation ANON_SPLIT=class HUMAN_FIRSTVISIT=1
run() { # scenario rep
  for f in results/runs/${1}_*_r${2}_*/summary.json; do
    if [ -f "$f" ]; then echo "== skip $1 r$2"; return 0; fi
  done
  sh scripts/run.sh "$@" || true
}
for rep in 1 2 3; do
  for kind in rotating cookie-keeper cookie-farm; do
    CRAWLER=$kind BOUNCE=       run S21_$kind $rep
    CRAWLER=$kind BOUNCE=1      run S22_$kind $rep
  done
done
python3 analysis/aggregate.py || true
