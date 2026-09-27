"""Single-request cost per route type: sequential, no concurrency, cookieless.

Each sample is a distinct URL (except home, which has only one URL), so
repeats do not hit per-URL caches. Writes raw CSV + summary CSV.
"""
import csv
import os
import random
import statistics
import sys
import time

import httpx

GITEA = os.environ.get("GITEA_URL", "http://gitea:3000")
POOL = os.environ.get("POOL", "urlpool")
N = int(os.environ.get("N", "30"))
OUT = sys.argv[1] if len(sys.argv) > 1 else "results/stage1"
TYPES = ["home", "src", "commits", "commit", "blame", "compare"]


def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main():
    rng = random.Random(7)
    os.makedirs(OUT, exist_ok=True)
    rows = []
    # Fresh client per request => no cookie carried over.
    with httpx.Client(timeout=120) as warm:
        warm.get(GITEA + "/")  # wake Gitea after restart
    for t in TYPES:
        urls = open(os.path.join(POOL, f"{t}.txt")).read().split()
        picks = [urls[0]] * N if len(urls) == 1 else rng.sample(urls, N)
        for u in picks:
            with httpx.Client(timeout=120) as c:
                t0 = time.perf_counter()
                try:
                    r = c.get(GITEA + u)
                    status, size = r.status_code, len(r.content)
                except httpx.HTTPError as e:
                    status, size = f"ERR:{type(e).__name__}", 0
                ms = (time.perf_counter() - t0) * 1000
            rows.append({"type": t, "url": u, "status": status, "bytes": size, "ms": round(ms, 1)})
            print(f"{t:8s} {status} {ms:8.1f}ms {u[:90]}", flush=True)

    with open(os.path.join(OUT, "cost_raw.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    summary = []
    for t in TYPES:
        ok = [r["ms"] for r in rows if r["type"] == t and r["status"] == 200]
        n_all = sum(1 for r in rows if r["type"] == t)
        summary.append({
            "type": t, "n": n_all, "ok": len(ok),
            "median_ms": round(statistics.median(ok), 1) if ok else "",
            "p95_ms": round(pct(ok, 0.95), 1) if ok else "",
            "max_ms": round(max(ok), 1) if ok else "",
            "mean_bytes": round(statistics.mean(r["bytes"] for r in rows if r["type"] == t)),
        })
    home_med = summary[0]["median_ms"]
    for s in summary:
        s["x_home"] = round(s["median_ms"] / home_med, 1) if s["median_ms"] != "" and home_med else ""
    with open(os.path.join(OUT, "cost_summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=summary[0].keys())
        w.writeheader()
        w.writerows(summary)
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()
