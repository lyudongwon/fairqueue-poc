"""Per-run summary from raw files. Usage: summarize.py <run_dir>

Writes <run_dir>/summary.json and <run_dir>/timeline.csv (per-second gitea
CPU/memory + cumulative new Gitea sessions seen at the gateway).
"""
import bisect
import csv
import json
import os
import sys

MEM_NEAR_LIMIT = 0.90   # memory.current / memory.max
# Stage-1 pooled median single-request cost (ms). Weighting successes by this
# gives an estimated "work done" index that is not fooled by mix shift (heavy
# requests timing out first makes raw 2xx/s look better than it is).
UNIT_COST_MS = {"home": 12.4, "src": 32.1, "commits": 32.5, "commit": 82.7, "blame": 163.4, "compare": 1543.8}
PSI_MEM_SIGNIFICANT = 5  # % of wall time stalled on memory


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def lat_stats(rows):
    lat = [float(r["latency_ms"]) for r in rows]
    fails = [r for r in rows if r["status"] != "200"]
    return {"n": len(rows), "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99),
            "fail_rate": round(len(fails) / len(rows), 4) if rows else None,
            "fails_by_status": {s: sum(1 for r in fails if r["status"] == s) for s in {r["status"] for r in fails}}}


def main(run_dir):
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    t_warm = meta["warmup_s"]
    out = {"run_id": meta["run_id"], "status": meta["status"]}

    human = list(csv.DictReader(open(os.path.join(run_dir, "human.csv"))))
    m = [r for r in human if r["phase"] == "measure"]
    out["human"] = lat_stats(m)
    out["human_by_route"] = {rt: lat_stats([r for r in m if r["route"] == rt])
                             for rt in ("home", "src", "commits", "commit")}
    out["human_send_lag_p99_ms"] = pct([float(r["send_lag_ms"]) for r in m], .99)

    # Gateway log: new sessions over time, per-client-type counts.
    glog = [json.loads(l) for l in open(os.path.join(run_dir, "raw", "gateway.jsonl")) if l.strip()]
    glog.sort(key=lambda e: e["ts"])
    # Run clock origin = human t0 (first scheduled arrival minus its offset).
    t0 = float(human[0]["sched_wall"]) - float(human[0]["sched_s"])
    w0, w1 = t0 + t_warm, t0 + t_warm + meta["duration_s"]
    is_human = lambda e: e["ip"].startswith("10.0.0.")
    out["gateway"] = {
        "requests": len(glog),
        "human_requests": sum(1 for e in glog if is_human(e)),
        "crawler_requests": sum(1 for e in glog if not is_human(e) and e["route"] != "other"),
        "new_sessions_total": sum(1 for e in glog if e["new_session"]),
        "new_sessions_crawler": sum(1 for e in glog if e["new_session"] and not is_human(e)),
    }
    dur = meta["duration_s"]
    cw = [e for e in glog if not is_human(e) and e["route"] != "other" and w0 <= e["ts"] / 1000 < w1]
    crawl_ok = [e for e in cw if e["status"] == 200]
    out["crawler_ok_rps"] = round(len(crawl_ok) / dur, 2)
    out["crawler_work_index"] = round(sum(UNIT_COST_MS[e["route"]] for e in crawl_ok) / 1000 / dur, 3)
    if cw:
        out["crawler_gateway"] = {
            "requests_measure": len(cw),
            "status": {str(s): sum(1 for e in cw if e["status"] == s) for s in sorted({e["status"] for e in cw})},
            "with_cookie": sum(1 for e in cw if e["cookie"]),
            "distinct_ips": len({e["ip"] for e in cw}),
            "route_share": {rt: round(sum(1 for e in cw if e["route"] == rt) / len(cw), 3)
                            for rt in sorted({e["route"] for e in cw})},
            "ok_route_share": {rt: round(sum(1 for e in crawl_ok if e["route"] == rt) / len(crawl_ok), 3)
                               for rt in sorted({e["route"] for e in crawl_ok})} if crawl_ok else {},
            "ok_upstream_p50_ms": pct([e["upstream_ms"] for e in crawl_ok], .5),
            "sessions_created_measure": sum(1 for e in cw if e["new_session"]),
        }
    ccsv = os.path.join(run_dir, "raw", "crawler.csv")
    if os.path.exists(ccsv):
        cr = [r for r in csv.DictReader(open(ccsv)) if w0 <= float(r["start_wall"]) < w1]
        out["crawler_client"] = {
            "requests_measure": len(cr),
            "ok_rps": round(sum(1 for r in cr if r["status"] == "200") / dur, 2),
            "status": {s: sum(1 for r in cr if r["status"] == s) for s in sorted({r["status"] for r in cr})},
        }

    # Monitor: gitea resource usage during the measure window.
    mon = [r for r in csv.DictReader(open(os.path.join(run_dir, "monitor.csv"))) if r["container"] == "gitea"]
    mon_t0 = t0
    mw = [r for r in mon if w0 <= float(r["ts"]) < w1]
    f = lambda k: [float(r[k]) for r in mw if r[k] != ""]
    if mw:
        mem_max = float(mw[0]["mem_max"])
        peak = max(f("mem_current"))
        g = {
            "cpu_pct_mean": round(sum(f("cpu_pct")) / len(mw), 1), "cpu_pct_p95": pct(f("cpu_pct"), .95),
            "psi_cpu_some_mean": round(sum(f("psi_cpu_some_pct")) / len(mw), 1),
            "mem_start_mb": round(float(mon[0]["mem_current"]) / 2**20, 1) if mon else None,
            "mem_peak_mb": round(peak / 2**20, 1), "mem_anon_peak_mb": round(max(f("mem_anon")) / 2**20, 1),
            "mem_limit_mb": round(mem_max / 2**20), "mem_peak_pct": round(peak / mem_max * 100, 1),
            "psi_mem_some_max": max(f("psi_mem_some_pct")),
            "mem_events_max": int(mw[-1]["ev_max"]), "oom_kill": int(mw[-1]["oom_kill"]),
        }
        g["memory_bottleneck"] = bool(g["mem_peak_pct"] >= MEM_NEAR_LIMIT * 100 or g["oom_kill"] > 0
                                      or g["mem_events_max"] > 0 or g["psi_mem_some_max"] >= PSI_MEM_SIGNIFICANT)
        out["gitea"] = g
    other = {}
    for name in ("gateway", "human", "crawler"):
        rows = [float(r["cpu_pct"]) for r in csv.DictReader(open(os.path.join(run_dir, "monitor.csv")))
                if r["container"] == name]
        if rows:
            other[name] = {"cpu_pct_p95": pct(rows, .95), "cpu_pct_max": max(rows)}
    out["other_containers"] = other

    # Timeline: per second, gitea cpu/mem and cumulative sessions.
    sess_ts = [e["ts"] / 1000 for e in glog if e["new_session"]]  # already sorted
    with open(os.path.join(run_dir, "timeline.csv"), "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["t_s", "gitea_cpu_pct", "gitea_mem_mb", "gitea_anon_mb", "sessions_cum"])
        for r in mon:
            ts = float(r["ts"])
            w.writerow([round(ts - mon_t0, 1), r["cpu_pct"], round(int(r["mem_current"]) / 2**20, 1),
                        round(int(r["mem_anon"]) / 2**20, 1), bisect.bisect_right(sess_ts, ts)])

    json.dump(out, open(os.path.join(run_dir, "summary.json"), "w"), indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
