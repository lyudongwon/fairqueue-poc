"""Open-loop human simulator.

Arrivals are a Poisson process (RATE req/s total) scheduled up front, so a slow
response never delays the next send (no coordinated omission). Each arrival is
assigned to one of USERS virtual users; each user keeps its own cookie jar and
fixed X-Forwarded-For, and walks home -> src -> commits -> commit in a cycle.
Users are returning visitors: each gets its session cookie before t0 (prime()).
Latency is measured from the *scheduled* send time.
"""
import asyncio
import csv
import http.cookiejar
import os
import random
import time

import httpx

BASE = os.environ.get("TARGET", "http://gateway:8080")
POOL = os.environ.get("POOL", "/app/urlpool")
RUN_DIR = os.environ["RUN_DIR"]
RATE = float(os.environ.get("RATE", "3"))
USERS = int(os.environ.get("USERS", "10"))
WARMUP = float(os.environ.get("WARMUP", "30"))
DURATION = float(os.environ.get("DURATION", "180"))
TIMEOUT = float(os.environ.get("HUMAN_TIMEOUT", "30"))
SEED = int(os.environ.get("SEED", "1"))
PRIME_URL = os.environ.get("PRIME_URL", "http://gitea:3000/")  # "" disables priming
CYCLE = ["home", "src", "commits", "commit"]
WALL_OFFSET = time.time() - time.monotonic()


def load(name):
    with open(os.path.join(POOL, f"{name}.txt")) as f:
        return f.read().split()


class User:
    def __init__(self, idx, rng, pools):
        self.idx, self.rng, self.pools, self.step = idx, rng, pools, 0
        self.client = httpx.AsyncClient(
            base_url=BASE, timeout=TIMEOUT, follow_redirects=False,
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=50),
            headers={"X-Forwarded-For": f"10.0.0.{idx + 1}", "User-Agent": f"human-sim/{idx + 1}"})

    def next_url(self):
        route = CYCLE[self.step % len(CYCLE)]
        self.step += 1
        if route == "commits":
            return route, self.pools["commits"][0]  # page 1
        return route, self.rng.choice(self.pools[route])


async def fire(user, route, url, sched, t0, rows):
    await asyncio.sleep(max(0.0, sched - time.monotonic()))
    send = time.monotonic()
    cookie_sent = "i_like_gitea=" in user.client.headers.get("Cookie", "") or         any(c.name == "i_like_gitea" for c in user.client.cookies.jar)
    status, size, err = "", 0, ""
    try:
        r = await user.client.get(url)
        status, size = r.status_code, len(r.content)
    except httpx.TimeoutException:
        status, err = "timeout", "timeout"
    except httpx.HTTPError as e:
        status, err = "error", type(e).__name__
    end = time.monotonic()
    rows.append({
        "user": user.idx + 1, "route": route, "url": url,
        "phase": "warmup" if sched - t0 < WARMUP else "measure",
        "sched_s": round(sched - t0, 4), "sched_wall": round(sched + WALL_OFFSET, 4), "send_lag_ms": round((send - sched) * 1000, 2),
        "latency_ms": round((end - sched) * 1000, 2), "status": status, "bytes": size,
        "cookie_sent": cookie_sent, "error": err,
    })


async def prime(users):
    """Give each user a session cookie before t0 (returning visitors).

    Fetched straight from Gitea, not via the gateway, so the cookie is obtained
    the same way in every mode and never queues behind crawlers in anon-cap.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for u in users:
            c.cookies.clear()
            r = await c.get(PRIME_URL)
            val = r.cookies.get("i_like_gitea")
            if not val:
                raise SystemExit(f"priming failed for user {u.idx + 1}: status {r.status_code}")
            # Pin it as a header and stop the jar from storing any other cookie, so the
            # user presents exactly this session on every request (the jar's host
            # matching for dotless names like "gateway" would otherwise drop it).
            u.client.headers["Cookie"] = f"i_like_gitea={val}"
            u.client.cookies.jar.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))


async def main():
    rng = random.Random(SEED)
    pools = {k: load(k) for k in ("home", "src", "commits", "commit")}
    users = [User(i, random.Random(SEED * 1000 + i), pools) for i in range(USERS)]
    if PRIME_URL:
        await prime(users)
    total = WARMUP + DURATION
    t0 = time.monotonic() + 0.5
    rows, tasks, t = [], [], 0.0
    while True:
        t += rng.expovariate(RATE)
        if t >= total:
            break
        u = users[rng.randrange(USERS)]
        route, url = u.next_url()
        tasks.append(asyncio.create_task(fire(u, route, url, t0 + t, t0, rows)))
    print(f"scheduled {len(tasks)} requests over {total:.0f}s", flush=True)
    await asyncio.gather(*tasks)
    for u in users:
        await u.client.aclose()
    rows.sort(key=lambda r: r["sched_s"])
    with open(os.path.join(RUN_DIR, "human.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    m = [r for r in rows if r["phase"] == "measure"]
    fails = sum(1 for r in m if r["status"] != 200)
    print(f"done: {len(rows)} requests, measure={len(m)}, non-200={fails}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
