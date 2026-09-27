"""Aggregate all runs into results/summary.csv, grouped by (scenario, mode, crawler, c).

Human percentiles are computed on the pooled measure-phase samples of all ok
reps (p99 from a single 3-minute run is too noisy). Per-rep p95s are listed
alongside so rep-to-rep spread stays visible. Failed runs are listed, not dropped.
"""
import csv
import glob
import json
import os

from summarize import pct

OUT = "results/summary.csv"


def mean_opt(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def max_opt(xs):
    xs = [x for x in xs if x is not None]
    return max(xs) if xs else None


def main():
    groups = {}
    for d in sorted(glob.glob("results/runs/*/")):
        try:
            meta = json.load(open(os.path.join(d, "meta.json")))
            s = json.load(open(os.path.join(d, "summary.json")))
        except FileNotFoundError:
            meta, s = {"run_id": os.path.basename(d.rstrip("/")), "status": "incomplete"}, None
        key = (meta.get("scenario"), meta.get("mode"), meta.get("crawler"), meta.get("c"))
        groups.setdefault(key, []).append((d, meta, s))

    rows = []
    for (scen, mode, crawler, c), runs in sorted(groups.items(), key=lambda kv: [str(x) for x in kv[0]]):
        ok = [(d, m, s) for d, m, s in runs if m.get("status") == "ok" and s]
        lat, fails, n = [], 0, 0
        for d, _, _ in ok:
            for r in csv.DictReader(open(os.path.join(d, "human.csv"))):
                if r["phase"] == "measure":
                    lat.append(float(r["latency_ms"]))
                    n += 1
                    fails += r["status"] != "200"
        g = [s["gitea"] for _, _, s in ok if "gitea" in s]
        rows.append({
            "scenario": scen, "mode": mode, "crawler": crawler, "c": c,
            "reps_ok": len(ok), "reps_failed": len(runs) - len(ok),
            "human_n": n, "human_p50_ms": pct(lat, .5), "human_p95_ms": pct(lat, .95),
            "human_p99_ms": pct(lat, .99), "human_fail_rate": round(fails / n, 4) if n else None,
            "human_p95_per_rep": " / ".join(str(s["human"]["p95_ms"]) for _, _, s in ok),
            "crawler_ok_rps": round(sum(s["crawler_ok_rps"] for _, _, s in ok) / len(ok), 2) if ok else None,
            "crawler_work_index": round(sum(s.get("crawler_work_index", 0) for _, _, s in ok) / len(ok), 3) if ok else None,
            "crawler_ok_rps_per_rep": " / ".join(str(s["crawler_ok_rps"]) for _, _, s in ok),
            "human_gw_wait_p95_ms": max((s.get("human_gateway", {}).get("wait_p95_ms") or 0) for _, _, s in ok) if ok else None,
            "cookie_slot_crawler_share_of_used": mean_opt(
                [s.get("slot_occupancy", {}).get("cookie", {}).get("crawler_share_of_used") for _, _, s in ok]),
            "cookie_slot_human_share_of_capacity": mean_opt(
                [s.get("slot_occupancy", {}).get("cookie", {}).get("human_share_of_capacity") for _, _, s in ok]),
            "cookie_slot_crawler_share_of_capacity": mean_opt(
                [s.get("slot_occupancy", {}).get("cookie", {}).get("crawler_share_of_capacity") for _, _, s in ok]),
            "human_cookie_pool_wait_p95_ms": max_opt(
                [s.get("slot_occupancy", {}).get("cookie", {}).get("human_wait_p95_ms") for _, _, s in ok]),
            "fq_slot_crawler_share_of_used": mean_opt(
                [s.get("slot_occupancy", {}).get("fq", {}).get("crawler_share_of_used") for _, _, s in ok]),
            "human_fq_wait_p95_ms": max_opt(
                [s.get("slot_occupancy", {}).get("fq", {}).get("human_wait_p95_ms") for _, _, s in ok]),
            "fq_human_cost_s": mean_opt([s.get("principal_costs", {}).get("human_cost_s") for _, _, s in ok]),
            "fq_crawler_cost_s": mean_opt([s.get("principal_costs", {}).get("crawler_cost_s") for _, _, s in ok]),
            "gitea_cpu_pct_mean": round(sum(x["cpu_pct_mean"] for x in g) / len(g), 1) if g else None,
            "gitea_psi_cpu_mean": round(sum(x["psi_cpu_some_mean"] for x in g) / len(g), 1) if g else None,
            "gitea_mem_peak_mb": max(x["mem_peak_mb"] for x in g) if g else None,
            "gitea_mem_peak_pct": max(x["mem_peak_pct"] for x in g) if g else None,
            "sessions_created_max": max(s["gateway"]["new_sessions_total"] for _, _, s in ok) if ok else None,
            "memory_bottleneck": any(x["memory_bottleneck"] for x in g),
            "failed_runs": " ".join(m["run_id"] for _, m, _ in runs if m.get("status") != "ok"),
        })
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
