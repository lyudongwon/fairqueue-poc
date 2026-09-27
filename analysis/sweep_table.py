"""Markdown tables for a C sweep with the crawler-throughput knee marked.

Two throughput measures are shown:
  2xx/s       raw successful crawler requests per second
  work index  successes weighted by stage-1 unit cost (s of Gitea work per s)
Raw 2xx/s is inflated by mix shift under overload (compare requests time out
first), so the knee is marked on the per-C median work index: the first C whose
work index is < KNEE_GAIN x the previous C's. The degradation onset is the
first C whose human p95 is >= ONSET_X x the pooled S0 p95.
Usage: sweep_table.py [scenario] (default S1). Reads per-run summary.json.
"""
import csv
import glob
import json
import statistics
import sys

KNEE_GAIN = 1.10
ONSET_X = 1.5


def main(scen):
    runs = []
    for p in glob.glob(f"results/runs/{scen}_*/summary.json"):
        s = json.load(open(p))
        m = json.load(open(p.replace("summary.json", "meta.json")))
        if m["status"] == "ok":
            runs.append((m["c"], m["rep"], m, s))
    runs.sort(key=lambda r: (r[0], r[1]))
    s0 = next(float(r["human_p95_ms"]) for r in csv.DictReader(open("results/summary.csv"))
              if r["scenario"] == "S0")  # pooled S0 p95
    print(f"S0 pooled human p95 = {s0} ms\n")

    print("Per run:\n")
    print("| C | rep | human p50 | human p95 | ×S0 | human p99 | human fail | crawler 2xx/s | compare share of 2xx | "
          "work index | crawler aborted | gitea CPU mean (%) | CPU PSI | mem peak MB (% of 2 GiB) | mem PSI max | "
          "sessions (measure) | mem bottleneck |")
    print("|" + "---|" * 17)
    for c, rep, m, s in runs:
        g, cg, h = s.get("gitea", {}), s.get("crawler_gateway", {}), s["human"]
        aborted = sum(v for k, v in cg.get("status", {}).items() if k != "200")
        print(f"| {c} | {rep} | {h['p50_ms']} | {h['p95_ms']} | {h['p95_ms'] / s0:.1f} | {h['p99_ms']} | "
              f"{h['fail_rate']:.1%} | {s['crawler_ok_rps']} | {cg.get('ok_route_share', {}).get('compare', 0):.1%} | "
              f"{s['crawler_work_index']} | {aborted} | {g.get('cpu_pct_mean')} | {g.get('psi_cpu_some_mean')}% | "
              f"{g.get('mem_peak_mb')} ({g.get('mem_peak_pct')}%) | {g.get('psi_mem_some_max')}% | "
              f"{cg.get('sessions_created_measure', 0)} | {'**yes**' if g.get('memory_bottleneck') else 'no'} |")

    print("\nPer C (median of reps):\n")
    print("| C | reps | human p95 | ×S0 | crawler 2xx/s | work index | gitea CPU mean (%) | mem peak MB | marks |")
    print("|---|---|---|---|---|---|---|---|---|")
    prev, knee_found, onset_found = None, False, False
    for c in sorted({r[0] for r in runs}):
        rs = [s for cc, _, _, s in runs if cc == c]
        med = lambda f: statistics.median(f(s) for s in rs)
        p95, work = med(lambda s: s["human"]["p95_ms"]), med(lambda s: s["crawler_work_index"])
        marks = []
        if not onset_found and p95 >= ONSET_X * s0:
            marks.append(f"degradation onset (≥{ONSET_X}×S0)")
            onset_found = True
        if prev is not None and work < prev * KNEE_GAIN:
            marks.append("↓ work declining" if work < prev else ("plateau" if knee_found else "◀ throughput knee"))
            knee_found = True
        prev = work
        print(f"| {c} | {len(rs)} | {p95:.1f} | {p95 / s0:.1f} | {med(lambda s: s['crawler_ok_rps']):.2f} | "
              f"{work:.3f} | {med(lambda s: s['gitea']['cpu_pct_mean']):.1f} | "
              f"{max(s['gitea']['mem_peak_mb'] for s in rs):.0f} | {', '.join(marks)} |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "S1")
