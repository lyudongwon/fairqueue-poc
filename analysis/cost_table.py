"""Stage-1 cost table as Markdown, with multiples vs home and vs commits.

home is a single URL requested repeatedly (served warm), so its multiples are
inflated; commits uses 30 distinct pages and is free of per-URL cache effects.
Usage: cost_table.py results/stage1 results/stage1/rep2 ...
"""
import csv
import statistics
import sys

TYPES = ["home", "src", "commits", "commit", "blame", "compare"]


def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main(dirs):
    reps = [list(csv.DictReader(open(f"{d}/cost_raw.csv"))) for d in dirs]
    ok = lambda rows, t: [float(r["ms"]) for r in rows if r["type"] == t and r["status"] == "200"]
    med = {t: [statistics.median(ok(rows, t)) for rows in reps] for t in TYPES}
    pooled = {t: sum((ok(rows, t) for rows in reps), []) for t in TYPES}
    pm = {t: statistics.median(pooled[t]) for t in TYPES}
    size = {t: statistics.mean(int(r["bytes"]) for rows in reps for r in rows if r["type"] == t) for t in TYPES}

    rep_cols = " | ".join(f"median r{i + 1}" for i in range(len(reps)))
    print(f"| type | n | {rep_cols} | pooled median | pooled p95 | max | mean size | × home | × commits |")
    print("|" + "---|" * (8 + len(reps)))
    for t in TYPES:
        xs = pooled[t]
        reps_s = " | ".join(f"{m:.1f}" for m in med[t])
        print(f"| {t} | {len(xs)} | {reps_s} | {pm[t]:.1f} | {pct(xs, .95):.1f} | {max(xs):.1f} | "
              f"{size[t] / 1024:.0f} KB | {pm[t] / pm['home']:.1f} | {pm[t] / pm['commits']:.2f} |")


if __name__ == "__main__":
    main(sys.argv[1:] or ["results/stage1", "results/stage1/rep2"])
