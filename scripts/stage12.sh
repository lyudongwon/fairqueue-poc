#!/bin/sh
# Stage 12: IP supplement for first visitors. Anonymous/probation queues are split per client IP (IP_SPLIT=1),
# the crawler uses only K distinct IPs (CRAWLER_IPS=K). No bounce. Humans: first-visit mode, each on its own IP.
#   S24_<kind>_ip<K>  K=1,10,100  ; compare with S21 (no IP split, unlimited IPs)
set -eu
cd "$(dirname "$0")/.."
export C=${C:-50} FQ_SLOTS=${FQ_SLOTS:-4} PROBATION_S=${PROBATION_S:-600} MODE=fq-cost-probation ANON_SPLIT=class HUMAN_FIRSTVISIT=1 IP_SPLIT=1 BOUNCE=
run() {
  for f in results/runs/${1}_*_r${2}_*/summary.json; do
    if [ -f "$f" ]; then echo "== skip $1 r$2"; return 0; fi
  done
  sh scripts/run.sh "$@" || true
}
for rep in $(seq 1 ${REPS:-1}); do
  for kind in rotating cookie-farm; do
    for k in ${KS:-1 100}; do
      CRAWLER=$kind CRAWLER_IPS=$k run S24_${kind}_ip$k $rep
    done
  done
done
python3 analysis/aggregate.py || true
