#!/bin/sh
# Stage 7: fq-cost (S7) and fq-cost-probation (S8) under the S4/S5/S6 crawler conditions. C=50, 3 reps.
set -eu
export C=${C:-50} FQ_SLOTS=${FQ_SLOTS:-4} PROBATION_S=${PROBATION_S:-600}
for rep in 1 2 3; do
  for kind in rotating cookie-keeper cookie-farm; do
    MODE=fq-cost           CRAWLER=$kind sh scripts/run.sh S7 $rep
    MODE=fq-cost-probation CRAWLER=$kind sh scripts/run.sh S8 $rep
  done
done
