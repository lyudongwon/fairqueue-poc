"""Markdown table for a C sweep with the crawler-throughput knee marked.

Two throughput measures are shown:
  2xx/s       raw successful crawler requests per second
  work index  successes weighted by stage-1 unit cost (s of Gitea work per s)
Raw 2xx/s is inflated by mix shift under overload (compare requests time out
first), so the knee is marked on the work index: the first C whose work index
is < KNEE_GAIN x the previous C's.
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
    s0 = next(float(r["human_p95_ms"]) for r in csv.DictReader(open("results/summary.csv"))
              if r["scenario"] == "S0")  # pooled S0 p95
    print(f"S0 human p95 per rep: {sorted(b['human']['p95_ms'] for b in base)}, pooled {s0}\n")
    print("| C | rep | human p50 | human p95 | ×S0 | human p99 | human fail | crawler 2xx/s | compare share of 2xx | "
          "work index | crawler aborted | gitea CPU mean (%) | CPU PSI | mem peak MB (% of 2 GiB) | mem PSI max | "
          "sessions (measure) | mem bottleneck | knee |")
    print("|" + "---|" * 18)
    prev, found = None, False
    for c, rep, m, s in runs:
        g, cg = s.get("gitea", {}), s.get("crawler_gateway", {})
        work = s["crawler_work_index"]
        knee = ""
        if prev is not None and work < prev * KNEE_GAIN:
            knee = "↓ declining" if work < prev else ("plateau" if found else "◀ knee")
            found = True
        prev = work
        aborted = sum(v for k, v in cg.get("status", {}).items() if k != "200")
        h = s["human"]
        print(f"| {c} | {rep} | {h['p50_ms']} | {h['p95_ms']} | {h['p95_ms'] / s0:.1f} | {h['p99_ms']} | "
              f"{h['fail_rate']:.1%} | {s['crawler_ok_rps']} | {cg.get('ok_route_share', {}).get('compare', 0):.1%} | "
              f"{work} | {aborted} | {g.get('cpu_pct_mean')} | {g.get('psi_cpu_some_mean')}% | "
              f"{g.get('mem_peak_mb')} ({g.get('mem_peak_pct')}%) | {g.get('psi_mem_some_max')}% | "
              f"{cg.get('sessions_created_measure', 0)} | {'**yes**' if g.get('memory_bottleneck') else 'no'} | {knee} |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "S1")
