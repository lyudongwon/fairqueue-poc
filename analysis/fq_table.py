"""Stage 7 table: anon-cap (S4/S5/S6) vs fq-cost (S7) vs fq-cost-probation (S8),
per crawler kind, from results/summary.csv plus per-run principal costs.
"""
import csv
import glob
import json
import os
import statistics

KINDS = ["rotating", "cookie-keeper", "cookie-farm"]
MODES = ["anon-cap", "fq-cost", "fq-cost-probation"]


def runs_for(mode, kind):
    out = []
    for p in glob.glob("results/runs/*/summary.json"):
        m = json.load(open(os.path.join(os.path.dirname(p), "meta.json")))
        if m["mode"] == mode and m["crawler"] == kind and m["c"] == 50 and m["status"] == "ok":
            out.append(json.load(open(p)))
    return out


def main():
    rows = {(r["mode"], r["crawler"]): r for r in csv.DictReader(open("results/summary.csv")) if r["c"] == "50"}
    s0 = next(float(r["human_p95_ms"]) for r in csv.DictReader(open("results/summary.csv")) if r["scenario"] == "S0")
    print("| crawler | mode | scen | reps | human p95 | ×S0 | human fail | p95 per rep | human gw wait p95 | "
          "crawler 2xx/s | work index | human cost (s) | crawler cost (s) | top crawler principal (share of all cost) | "
          "crawler sessions · cost/session |")
    print("|" + "---|" * 15)
    for kind in KINDS:
        for mode in MODES:
            r = rows.get((mode, kind))
            if not r:
                continue
            ss = runs_for(mode, kind)
            pcs = [s["principal_costs"] for s in ss if "principal_costs" in s]
            if pcs:
                tops = [next((t for t in pc["top"] if t["class"] == "crawler"), None) for pc in pcs]
                top = " / ".join(f"{t['principal']} {t['share']:.0%}" for t in tops if t)
                hc = f"{statistics.mean(pc['human_cost_s'] for pc in pcs):.0f}"
                cc = f"{statistics.mean(pc['crawler_cost_s'] for pc in pcs):.0f}"
                sess = f"{pcs[0]['crawler_sessions']} · {statistics.mean(pc['crawler_cost_per_session_s'] or 0 for pc in pcs):.1f} s"
            else:
                top = hc = cc = sess = "–"
            gw = r.get("human_fq_wait_p95_ms") or r.get("human_gw_wait_p95_ms")
            p95 = float(r["human_p95_ms"])
            print(f"| {kind} | {mode} | {r['scenario']} | {r['reps_ok']} | **{p95:.0f}** | {p95 / s0:.2f} | "
                  f"{float(r['human_fail_rate']):.1%} | {r['human_p95_per_rep']} | {gw} | {r['crawler_ok_rps']} | "
                  f"{r['crawler_work_index']} | {hc} | {cc} | {top} | {sess} |")


if __name__ == "__main__":
    main()
