#!/bin/sh
# Stage 4: S2 (ratelimit-ip; naive + rotating), S3 (global-cap), S4 (anon-cap). C fixed at 50, 3 reps each.
set -eu
export C=${C:-50}
for rep in 1 2 3; do
  MODE=ratelimit-ip CRAWLER=naive    sh scripts/run.sh S2 $rep
  MODE=ratelimit-ip CRAWLER=rotating sh scripts/run.sh S2 $rep
  MODE=global-cap   CRAWLER=rotating sh scripts/run.sh S3 $rep
  MODE=anon-cap     CRAWLER=rotating sh scripts/run.sh S4 $rep
done
