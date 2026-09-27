"""Markdown table for a C sweep with the crawler-throughput knee marked.

Knee: first C whose crawler 2xx throughput is < KNEE_GAIN x the previous C's,
i.e. adding concurrency no longer buys throughput (Gitea saturated).
Usage: sweep_table.py [scenario] (default S1). Reads per-run summary.json.
"""
import csv
import glob
import json
import sys

KNEE_GAIN = 1.10


def main(scen):
    base = [json.load(open(p)) for p in glob.glob("results/runs/S0_*/summary.json")]
    runs = []
    for p in glob.glob(f"results/runs/{scen}_*/summary.json"):
        s = json.load(open(p))
        m = json.load(open(p.replace("summary.json", "meta.json")))
        runs.append((m["c"], m["rep"], m, s))
    runs.sort(key=lambda r: (r[0], r[1]))
    s0_p95 = sorted(b["human"]["p95_ms"] for b in base)
    print(f"S0 human p95 per rep: {s0_p95}\n")
    print("| C | rep | status | human p50 | human p95 | ×S0 p95 | human p99 | human fail | crawler 2xx/s | "
          "crawler non-2xx | gitea CPU mean / p95 (%) | CPU PSI | mem peak MB (%) | mem PSI max | "
          "sessions (measure) | mem bottleneck | knee |")
    print("|" + "---|" * 17)
    s0 = next(float(r["human_p95_ms"]) for r in csv.DictReader(open("results/summary.csv"))
              if r["scenario"] == "S0")  # pooled S0 p95
    prev = None
    for c, rep, m, s in runs:
        g, cg = s.get("gitea", {}), s.get("crawler_gateway", {})
        rps = s["crawler_ok_rps"]
        knee = ""
        if prev is not None and rps < prev * KNEE_GAIN:
            knee = "◀ knee" if rps >= prev else "◀ knee (↓)"
        prev = rps
        non2xx = {k: v for k, v in cg.get("status", {}).items() if k != "200"}
        h = s["human"]
        print(f"| {c} | {rep} | {m['status']} | {h['p50_ms']} | {h['p95_ms']} | {h['p95_ms'] / s0:.1f} | "
              f"{h['p99_ms']} | {h['fail_rate']:.1%} | {rps} | {non2xx or 0} | "
              f"{g.get('cpu_pct_mean')} / {g.get('cpu_pct_p95')} | {g.get('psi_cpu_some_mean')}% | "
              f"{g.get('mem_peak_mb')} ({g.get('mem_peak_pct')}%) | {g.get('psi_mem_some_max')}% | "
              f"{cg.get('sessions_created_measure', 0)} | {g.get('memory_bottleneck')} | {knee} |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "S1")
