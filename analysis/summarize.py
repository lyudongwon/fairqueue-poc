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


def slot_occupancy(meta, glog, is_human, w0, w1):
    """Who held the gateway's admission slots during the measure window.

    A request holds a slot from arrival+wait to arrival+wait+upstream. Its pool
    follows from the mode (anon-cap: cookie -> "cookie" pool else "anon";
    global-cap: "global"). Slot-seconds are clipped to [w0, w1).
    share_of_capacity = slot-seconds / (slots x window); share_of_used is the
    split of occupied slot-seconds between humans and crawlers.
    """
    mode, gp = meta["mode"], meta.get("gateway_params", {})
    if mode == "anon-cap":
        pool_of = lambda e: "cookie" if e["cookie"] else "anon"
        cap = {"cookie": gp.get("cookie_m"), "anon": gp.get("anon_n")}
    elif mode == "global-cap":
        pool_of = lambda e: "global"
        cap = {"global": gp.get("global_n")}
    elif mode.startswith("fq-"):
        pool_of = lambda e: "fq"
        cap = {"fq": gp.get("fq_slots")}
    else:
        return None
    win = w1 - w0
    held = {p: {"human": 0.0, "crawler": 0.0} for p in cap}
    waits = {p: {"human": [], "crawler": []} for p in cap}
    for e in glog:
        if e["route"] == "other" and not is_human(e):
            continue  # health checks
        if e["status"] in (429, 503) or (e["status"] == 499 and e["upstream_ms"] == 0):
            continue  # never admitted
        who = "human" if is_human(e) else "crawler"
        p = pool_of(e)
        s = e["ts"] / 1000 + e["wait_ms"] / 1000
        t = s + e["upstream_ms"] / 1000
        held[p][who] += max(0.0, min(t, w1) - max(s, w0))
        if w0 <= e["ts"] / 1000 < w1:
            waits[p][who].append(e["wait_ms"])
    out = {}
    for p, n in cap.items():
        used = held[p]["human"] + held[p]["crawler"]
        out[p] = {
            "slots": n,
            "human_share_of_capacity": round(held[p]["human"] / (n * win), 3) if n else None,
            "crawler_share_of_capacity": round(held[p]["crawler"] / (n * win), 3) if n else None,
            "crawler_share_of_used": round(held[p]["crawler"] / used, 3) if used else None,
            "human_wait_p50_ms": pct(waits[p]["human"], .5), "human_wait_p95_ms": pct(waits[p]["human"], .95),
            "crawler_wait_p50_ms": pct(waits[p]["crawler"], .5), "crawler_wait_p95_ms": pct(waits[p]["crawler"], .95),
            "human_requests": len(waits[p]["human"]), "crawler_requests": len(waits[p]["crawler"]),
        }
    return out


def principal_costs(glog, is_human, w0, w1):
    """Upstream time consumed per scheduling principal (fq modes), measure window.

    A principal is 'human' if all its requests came from the human IP range.
    Also reports per-session cost, since under probation many sessions share
    one principal ("new").
    """
    ev = [e for e in glog if e.get("principal") and w0 <= e["ts"] / 1000 < w1]
    if not ev:
        return None
    by_p, by_s = {}, {}
    for e in ev:
        d = by_p.setdefault(e["principal"], {"requests": 0, "cost_s": 0.0, "human": True})
        d["requests"] += 1
        d["cost_s"] += e["upstream_ms"] / 1000
        d["human"] &= is_human(e)
        if e.get("sess"):
            s = by_s.setdefault(e["sess"], {"cost_s": 0.0, "human": is_human(e)})
            s["cost_s"] += e["upstream_ms"] / 1000
    total = sum(d["cost_s"] for d in by_p.values()) or 1
    hum = [d for d in by_p.values() if d["human"]]
    top = sorted(by_p.items(), key=lambda kv: -kv[1]["cost_s"])[:5]
    crawler_sess = [s["cost_s"] for s in by_s.values() if not s["human"]]
    return {
        "principals": len(by_p), "human_principals": len(hum),
        "human_cost_s": round(sum(d["cost_s"] for d in hum), 1),
        "crawler_cost_s": round(sum(d["cost_s"] for d in by_p.values() if not d["human"]), 1),
        "human_cost_per_principal_s": round(sum(d["cost_s"] for d in hum) / len(hum), 2) if hum else None,
        "top": [{"principal": p, "class": "human" if d["human"] else "crawler", "requests": d["requests"],
                 "cost_s": round(d["cost_s"], 1), "share": round(d["cost_s"] / total, 3)} for p, d in top],
        "crawler_sessions": len(crawler_sess),
        "crawler_cost_per_session_s": round(sum(crawler_sess) / len(crawler_sess), 2) if crawler_sess else None,
    }


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
    hw = [e for e in glog if is_human(e) and w0 <= e["ts"] / 1000 < w1]
    out["human_gateway"] = {
        "wait_p50_ms": pct([e["wait_ms"] for e in hw], .5), "wait_p95_ms": pct([e["wait_ms"] for e in hw], .95),
        "upstream_p95_ms": pct([e["upstream_ms"] for e in hw if e["status"] == 200], .95),
        "cookie_share": round(sum(e["cookie"] for e in hw) / len(hw), 3) if hw else None,
    }
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
            "ok_wait_p50_ms": pct([e["wait_ms"] for e in crawl_ok], .5),
            "sessions_created_measure": sum(1 for e in cw if e["new_session"]),
        }
    occ = slot_occupancy(meta, glog, is_human, w0, w1)
    if occ:
        out["slot_occupancy"] = occ
    pc = principal_costs(glog, is_human, w0, w1)
    if pc:
        out["principal_costs"] = pc
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
