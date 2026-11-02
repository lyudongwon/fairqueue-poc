#!/bin/sh
# Skip a run if a finished one (summary.json) already exists for this scenario+rep.
cd "$(dirname "$0")/.."
for f in results/runs/${1}_*_r${2}_*/summary.json; do
  if [ -f "$f" ]; then echo "== skip $1 r$2 (done: $f)"; exit 0; fi
done
exec sh scripts/run.sh "$@"
