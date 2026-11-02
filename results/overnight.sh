#!/bin/sh
# Overnight batch (lives in results/ so run.sh's "uncommitted code" check ignores it).
# Part A: reps 4-5 for the stage 8/9 candidates (cookieless humans vs rotating crawler, C=50).
# Part B: overhead without an attack (S19 fq-cost, S20 fq-cost-probation, cookie-holding humans, C=0, 3 reps;
#         baseline S0 already exists). Regression runs (old S16-S18) wait for the final design (cookie bounce).
# Finished runs (summary.json present) are skipped; a failed run does not stop the batch.
cd "$(dirname "$0")/.."
export C=50 FQ_SLOTS=4 PROBATION_S=600
docker compose up -d gitea

# Park unfinished run folders (no summary.json) so they cannot pollute the aggregate.
mkdir -p results/_incomplete
for d in results/runs/*/; do
  [ -f "${d}summary.json" ] || { echo "== parking incomplete ${d}"; mv "$d" results/_incomplete/; }
done
echo "== start $(date)"

# Part A
for rep in 4 5; do
  CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=anon-cap                                                      sh results/run_if_missing.sh S9  $rep || true
  CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=fq-cost                                                       sh results/run_if_missing.sh S10 $rep || true
  CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=fq-cost ANON_SPLIT=class                                      sh results/run_if_missing.sh S11 $rep || true
  CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=fq-cost ANON_SPLIT=route                                      sh results/run_if_missing.sh S12 $rep || true
  CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=fq-cost ANON_SPLIT=route ANON_ORDER=lifo                      sh results/run_if_missing.sh S13 $rep || true
  CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=fq-cost ANON_SPLIT=route ANON_HEAVY_CAP=2                     sh results/run_if_missing.sh S14 $rep || true
  CRAWLER=rotating HUMAN_COOKIELESS=1 MODE=fq-cost ANON_SPLIT=route ANON_ORDER=lifo ANON_HEAVY_CAP=2     sh results/run_if_missing.sh S15 $rep || true
done

# Part B
for rep in 1 2 3; do
  C=0 CRAWLER=none MODE=fq-cost           sh results/run_if_missing.sh S19 $rep || true
  C=0 CRAWLER=none MODE=fq-cost-probation sh results/run_if_missing.sh S20 $rep || true
done

python3 analysis/aggregate.py || true
echo "== done $(date)"
