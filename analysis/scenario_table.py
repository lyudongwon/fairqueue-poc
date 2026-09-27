"""Scenario comparison (S0, S1@C=50, S2..S6) from results/summary.csv as Markdown.

Human percentiles are pooled over reps. Q2 criterion: human p95 <= 1.5 x S0.
"""
import csv

C_FIXED = 50


def scenario_rows():
    rows = list(csv.DictReader(open("results/summary.csv")))
    pick = []
    for r in rows:
        if r["scenario"] == "S0" or (r["scenario"] != "S0" and int(r["c"]) == C_FIXED):
            pick.append(r)
    order = lambda r: (r["scenario"], r["crawler"])
    return sorted(pick, key=order)


def fmt_pct(v):
    return f"{float(v):.0%}" if v not in (None, "") else "–"


def label(r):
    return "S0" if r["scenario"] == "S0" else f"{r['scenario']} {r['mode']} / {r['crawler']}"


def main():
    rows = scenario_rows()
    s0 = next(float(r["human_p95_ms"]) for r in rows if r["scenario"] == "S0")
    print("| scenario | reps | human p50 | human p95 | ×S0 | human p99 | human fail | p95 per rep | "
          "human gw wait p95 | cookie-slot crawler share of used | cookie slots held (human / crawler, of M) | "
          "crawler 2xx/s | work index | gitea CPU | mem peak | sessions | mem bottleneck |")
    print("|" + "---|" * 17)
    for r in rows:
        p95 = float(r["human_p95_ms"])
        print(f"| {label(r)} | {r['reps_ok']} | {r['human_p50_ms']} | **{p95:.1f}** | {p95 / s0:.2f} | "
              f"{r['human_p99_ms']} | {float(r['human_fail_rate']):.1%} | {r['human_p95_per_rep']} | "
              f"{r.get('human_gw_wait_p95_ms', '')} | {fmt_pct(r.get('cookie_slot_crawler_share_of_used'))} | "
              f"{fmt_pct(r.get('cookie_slot_human_share_of_capacity'))} / "
              f"{fmt_pct(r.get('cookie_slot_crawler_share_of_capacity'))} | {r['crawler_ok_rps']} | {r.get('crawler_work_index', '')} | "
              f"{r['gitea_cpu_pct_mean']} % | {r['gitea_mem_peak_mb']} MB ({r['gitea_mem_peak_pct']} %) | "
              f"{r['sessions_created_max']} | {'**yes**' if r['memory_bottleneck'] == 'True' else 'no'} |")
        if r["failed_runs"]:
            print(f"|  ↳ failed runs: {r['failed_runs']} |" + " |" * 16)


if __name__ == "__main__":
    main()
