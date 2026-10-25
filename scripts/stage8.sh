#!/bin/sh
# Stage 8: cookieless (first-visit) humans vs a rotating, cookieless crawler. C=50, 3 reps.
#   S9  anon-cap                      (reference: anon pool shared in 2 FIFO slots)
#   S10 fq-cost                       (one shared "anon" principal, stage 7 behaviour)
#   S11 fq-cost + ANON_SPLIT=class    (anon:heavy {blame,compare} / anon:light)
#   S12 fq-cost + ANON_SPLIT=route    (one anon principal per route type)
# Humans never hold a cookie (HUMAN_COOKIELESS=1), so every human request is anonymous.
set -eu
export C=${C:-50} FQ_SLOTS=${FQ_SLOTS:-4} CRAWLER=rotating HUMAN_COOKIELESS=1
for rep in 1 2 3; do
  MODE=anon-cap                                 sh scripts/run.sh S9  $rep
  MODE=fq-cost                                  sh scripts/run.sh S10 $rep
  MODE=fq-cost ANON_SPLIT=class                 sh scripts/run.sh S11 $rep
  MODE=fq-cost ANON_SPLIT=route                 sh scripts/run.sh S12 $rep
done
