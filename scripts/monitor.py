"""1s cgroup v2 sampler (same source docker stats reads, plus anon/file split and PSI).

TARGETS="gitea=<container id> gateway=<id> ..."; host cgroupfs mounted at /host_cgroup.
cpu_pct is in % of one core (200 = 2 full cores). psi_*_pct is the share of wall
time some task was stalled on that resource in the interval.
Runs until SIGTERM.
"""
import csv
import glob
import os
import signal
import sys
import time

ROOT = "/host_cgroup"
OUT = os.path.join(os.environ["RUN_DIR"], "monitor.csv")
INTERVAL = float(os.environ.get("INTERVAL", "1"))
stop = False


def on_term(*_):
    global stop
    stop = True


def kv(path):
    out = {}
    with open(path) as f:
        for line in f:
            k, v = line.split()[:2]
            out[k] = int(v)
    return out


def psi_total(path):
    with open(path) as f:
        return int(f.readline().rsplit("total=", 1)[1])  # "some" line


def read(d):
    cpu, mem, ev = kv(f"{d}/cpu.stat"), kv(f"{d}/memory.stat"), kv(f"{d}/memory.events")
    mx = open(f"{d}/memory.max").read().strip()
    return {
        "cpu_usec": cpu["usage_usec"], "thr_usec": cpu.get("throttled_usec", 0),
        "mem_current": int(open(f"{d}/memory.current").read()),
        "mem_anon": mem["anon"], "mem_file": mem["file"],
        "mem_max": 0 if mx == "max" else int(mx),
        "ev_high": ev["high"], "ev_max": ev["max"], "oom_kill": ev["oom_kill"],
        "psi_cpu": psi_total(f"{d}/cpu.pressure"), "psi_mem": psi_total(f"{d}/memory.pressure"),
    }


def find(cid):
    hits = glob.glob(f"{ROOT}/**/*{cid}*", recursive=False) or glob.glob(f"{ROOT}/docker/{cid}*")
    return hits[0] if hits else None


def main():
    signal.signal(signal.SIGTERM, on_term)
    targets = dict(t.split("=", 1) for t in os.environ["TARGETS"].split())
    dirs, prev = {}, {}
    f = open(OUT, "w", newline="")
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["ts", "container", "cpu_pct", "throttled_pct", "mem_current", "mem_anon", "mem_file",
                "mem_max", "mem_pct", "ev_high", "ev_max", "oom_kill", "psi_cpu_some_pct", "psi_mem_some_pct"])
    nxt = time.monotonic()
    while not stop:
        now, wall = time.monotonic(), time.time()
        for name, cid in targets.items():
            d = dirs.get(name) or find(cid)
            if not d or not os.path.exists(f"{d}/cpu.stat"):
                continue
            dirs[name] = d
            try:
                cur = read(d)
            except (OSError, ValueError):
                continue
            p = prev.get(name)
            prev[name] = (now, cur)
            if not p:
                continue
            dt = (now - p[0]) * 1e6
            pc = p[1]
            pct = lambda k: round((cur[k] - pc[k]) / dt * 100, 1)
            w.writerow([round(wall, 3), name, pct("cpu_usec"), pct("thr_usec"), cur["mem_current"],
                        cur["mem_anon"], cur["mem_file"], cur["mem_max"],
                        round(cur["mem_current"] / cur["mem_max"] * 100, 1) if cur["mem_max"] else "",
                        cur["ev_high"], cur["ev_max"], cur["oom_kill"], pct("psi_cpu"), pct("psi_mem")])
        f.flush()
        nxt += INTERVAL
        time.sleep(max(0.0, nxt - time.monotonic()))
    f.close()
    print("monitor stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
