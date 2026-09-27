"""Crawler load: C closed-loop workers issuing random URLs from the real pools.

CRAWLER:
  naive     fixed XFF, no cookies
  rotating  random XFF per request (never inside 10.0.0.0/24, the human range), no cookies
On 429/503 a worker sleeps Retry-After (1 s) before its next request.
URL mix (see DECISIONS.md): commit 50 / blame 25 / src 15 / compare 10.
Each type's pool is shuffled once per run and consumed without replacement
(wraps around only if exhausted; wraps are counted).
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
KIND = os.environ.get("CRAWLER", "rotating")
C = int(os.environ.get("C", "10"))
WARMUP = float(os.environ.get("WARMUP", "30"))
DURATION = float(os.environ.get("DURATION", "180"))
TIMEOUT = float(os.environ.get("CRAWLER_TIMEOUT", "30"))
SEED = int(os.environ.get("SEED", "1"))
MIX = {"commit": 50, "blame": 25, "src": 15, "compare": 10}
NAIVE_IP = "198.51.100.7"
WALL_OFFSET = time.time() - time.monotonic()


class NoCookies(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


class Pools:
    def __init__(self, rng):
        self.rng, self.urls, self.pos, self.wraps = rng, {}, {}, {}
        for t in MIX:
            with open(os.path.join(POOL, f"{t}.txt")) as f:
                u = f.read().split()
            rng.shuffle(u)
            self.urls[t], self.pos[t], self.wraps[t] = u, 0, 0
        self.types, self.weights = list(MIX), list(MIX.values())

    def next(self):
        t = self.rng.choices(self.types, self.weights)[0]
        if self.pos[t] >= len(self.urls[t]):
            self.pos[t], self.wraps[t] = 0, self.wraps[t] + 1
        u = self.urls[t][self.pos[t]]
        self.pos[t] += 1
        return t, u


def rand_ip(rng):
    while True:
        a = rng.randint(11, 223)  # skips 0-10.x (incl. human 10.0.0.0/24) and multicast
        if a != 127:
            return f"{a}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def xff_for(rng):
    if KIND == "naive":
        return NAIVE_IP
    if KIND == "rotating":
        return rand_ip(rng)
    raise SystemExit(f"unknown CRAWLER {KIND}")


async def worker(client, pools, rng, t0, deadline, rows):
    while time.monotonic() < deadline:
        route, url = pools.next()
        ip = xff_for(rng)
        start = time.monotonic()
        status = ""
        try:
            r = await asyncio.wait_for(client.get(url, headers={"X-Forwarded-For": ip}),
                                       timeout=max(0.1, deadline - start))
            status = r.status_code
            await r.aclose()
            if status in (429, 503):  # honour Retry-After like a polite-enough crawler
                await asyncio.sleep(min(float(r.headers.get("Retry-After", "1")), max(0.0, deadline - time.monotonic())))
        except asyncio.TimeoutError:
            status = "cut"  # still in flight at run end
        except httpx.TimeoutException:
            status = "timeout"
        except httpx.HTTPError as e:
            status = f"error:{type(e).__name__}"
        end = time.monotonic()
        rows.append((round(start - t0, 3), round(start + WALL_OFFSET, 3), route, ip, status,
                     round((end - start) * 1000, 1)))


async def main():
    rng = random.Random(1000 + SEED)
    pools = Pools(rng)
    jar = http.cookiejar.CookieJar(policy=NoCookies())
    client = httpx.AsyncClient(
        base_url=BASE, timeout=TIMEOUT, cookies=jar, follow_redirects=False,
        limits=httpx.Limits(max_connections=C, max_keepalive_connections=C),
        headers={"User-Agent": f"crawler-sim/{KIND}"})
    t0 = time.monotonic()
    deadline = t0 + WARMUP + DURATION
    rows = []
    await asyncio.gather(*(worker(client, pools, random.Random(rng.random()), t0, deadline, rows)
                           for _ in range(C)))
    await client.aclose()
    os.makedirs(os.path.join(RUN_DIR, "raw"), exist_ok=True)
    with open(os.path.join(RUN_DIR, "raw", "crawler.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["t_s", "start_wall", "route", "ip", "status", "latency_ms"])
        w.writerows(sorted(rows, key=lambda r: r[0]))
    ok = sum(1 for r in rows if r[4] == 200 and r[0] >= WARMUP)
    print(f"crawler {KIND} C={C}: {len(rows)} requests, measure 2xx={ok} ({ok / DURATION:.2f}/s), "
          f"wraps={pools.wraps}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
