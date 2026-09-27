#!/bin/sh
# Stage 5: S5 (anon-cap + cookie-keeper), S6 (anon-cap + cookie-farm, 100 cookies). C=50, 3 reps each.
set -eu
export C=${C:-50} ANON_N=${ANON_N:-2} COOKIE_M=${COOKIE_M:-4}
for rep in 1 2 3; do
  MODE=anon-cap CRAWLER=cookie-keeper sh scripts/run.sh S5 $rep
  MODE=anon-cap CRAWLER=cookie-farm   sh scripts/run.sh S6 $rep
done
