#!/bin/sh
# Stage 9: fix the overlap route. Cookieless humans vs a rotating, cookieless crawler, C=50, 3 reps.
# Reference (already measured in stage 8): S10 fq-cost, S11 class split, S12 route split.
#   S13 route split + LIFO inside anonymous queues
#   S14 route split + at most 2 anonymous heavy (blame/compare) requests in flight
#   S15 route split + LIFO + heavy cap 2
set -eu
export C=${C:-50} FQ_SLOTS=${FQ_SLOTS:-4} CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=fq-cost ANON_SPLIT=route
for rep in 1 2 3; do
  ANON_ORDER=lifo                      sh scripts/run.sh S13 $rep
  ANON_HEAVY_CAP=2                     sh scripts/run.sh S14 $rep
  ANON_ORDER=lifo ANON_HEAVY_CAP=2     sh scripts/run.sh S15 $rep
done
