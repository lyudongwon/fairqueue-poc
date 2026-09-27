# Decisions and deviations from FAIRQUEUE_POC.md

Each line records a decision that goes beyond or departs from the spec, with its reason.

## Environment
- `.wslconfig`: processors 8 → **12** (memory 16GB, swap 8GB unchanged). `docker info` reports NCPU=12, MemTotal=15.6GiB, Docker 29.1.3, kernel 6.6.87.2-microsoft-standard-WSL2.
- CPU pinning (cpuset): 0-1 reserved for VM/dockerd overhead, 2-3 gitea, 4-5 gateway, 6 human, 7-10 crawler, 11 tools/stats. Gitea also gets `cpus: 2` and `mem_limit: 2g`, with swap disabled.
- Gitea is pinned to `gitea/gitea:1.27.3` with sqlite3 and default cache settings. The only settings added are INSTALL_LOCK, ROOT_URL/DOMAIN, DISABLE_SSH and DISABLE_REGISTRATION.
- Gitea data lives in a Docker named volume, not a `/mnt/c` bind mount, so 9p I/O does not distort latency.
- Gitea is restarted before every measurement run (memory caches cleared). The OS page cache is not cleared.

## Data
- Source repo is redis/redis. `unstable` HEAD is `20bb2cf` (13,304 commits reachable, 9,966 first-parent). It has 115 branches and 415 tags.
- The commits-list page size is 50 → 267 pages.
- blame pool: (sha, path) pairs from `git log --first-parent --diff-filter=AM` (the file exists at that sha). 20,000 were sampled (seed 42).
- compare pool: first-parent commit pairs, distance uniform in 1–50, which bounds the cost of a single request.

## Route types (6)
The spec's "heavy path candidates" are 4 types. The 2 human-only types (home, src) are added, for **6 in total**. The gateway also classifies anything else as `other`.

| type | URL | used by |
|---|---|---|
| home | `/poc/redis` | human |
| src | `/poc/redis/src/branch/unstable/{path}` | human |
| commits | `/poc/redis/commits/branch/unstable?page=N` | human (page 1), crawler |
| commit | `/poc/redis/commit/{sha}` | human, crawler |
| blame | `/poc/redis/blame/commit/{sha}/{path}` | crawler |
| compare | `/poc/redis/compare/{a}...{b}` | crawler |

## Cookie criterion (verified in stage 1 from response headers)
- Gitea 1.27.3 issues exactly one cookie on the first anonymous request: `Set-Cookie: i_like_gitea=<16 hex>; Path=/; HttpOnly; SameSite=Lax`. There is **no** `_csrf` cookie.
- When the request carries that cookie, the response has no new Set-Cookie.
- Gateway rule: **a request counts as "has cookie" if and only if its `Cookie` header contains `i_like_gitea`.** The value is not validated.
  - Limitation: a crawler could send a fabricated value. This is outside Q3's scope and goes in the README limitations.
- Side effect: every cookieless request makes Gitea create a new session, so a cookieless crawler also creates sessions in bulk.

## Sessions and memory (stage 2)
- Confirmed on Gitea's admin config page: **Session Provider = memory**, Session Life Time = 86400 s, GC Interval = 86400 s.
  - Sessions accumulate on Gitea's heap and are never collected within a run.
  - The count cannot be read directly, so the gateway logs responses carrying `Set-Cookie: i_like_gitea` as `new_session`.
  - Gitea restarts before each run, so the cumulative `new_session` count equals the number of live sessions.
- Resources are recorded by `scripts/monitor.py` reading cgroup v2 files directly every second, **instead of `docker stats`**.
  - It is the same data source, with exact 1 s spacing. It adds the anon/file memory split, PSI (cpu/memory pressure), memory.events and oom_kill.
  - The gateway, human and crawler containers are recorded too, to catch load-generator saturation.
- **Memory bottleneck** is flagged when any of these holds in the measure window:
  - memory.current ≥ 90 % of the limit
  - `memory.events max` > 0
  - oom_kill > 0
  - memory PSI "some" ≥ 5 % in any 1 s interval

  The run's result must then be reported as a memory bottleneck, not a CPU one.
- The gateway port is not published to the host because Windows reserves 127.0.0.1:8080. The gateway is reachable only on the compose network.

## Stage 3 → 4 choices
- S1 is 1 rep at C = 2/5/10/100/200 and 3 reps at C = 50. C=50 is the comparison point for everything after it.
- **Crawler concurrency is fixed at C = 50 for S2–S6.**
  - The first 1-rep sweep (C = 10…200) put the work-index knee at C=50. That rationale was **revised after adding C=2/5 and C=50 r2/r3**.
    - Gitea CPU is already saturated at C=2 (185 %).
    - The work index gains +13 % from 2→5, +3.5 % from 5→10 and +9 % from 10→50 (median of 3). So the knee is at C≈5–10, and C=50 sits well past saturation.
  - C=50 was kept, because the stage 4 runs had already started with it and it remains a sound comparison point:
    - It is a saturating load with 3 reps at the baseline.
    - Gitea memory peaks at 44–47 % of the limit, so a memory bottleneck does not confound Q2/Q3 (C=200 hit 96 %).
    - The DECISIONS edit was deferred until the runs finished, so as not to dirty the tree mid-series.
- Degradation onset: the first C where human p95 ≥ 1.5× S0 is **C=2** (2.2×), the lowest C tested. The spec's 3× line is crossed at C=5 (3.7×). Onset below C=2 is untested.

## Gateway parameters (stage 4)
- `ratelimit-ip`: token bucket per XFF IP, **2 req/s, burst 2**, as in the spec. Excess gets 429 with `Retry-After: 1`.
  - Human users send ≈ 0.3 req/s per IP, so they never hit it.
  - Idle buckets are evicted every 10 s, so rotating IPs cannot grow the map without bound.
- `global-cap`: **N = 4** concurrent upstream requests. The FIFO wait is capped at 30 s, then 503.
  - S1 shows Gitea's 2 CPUs already saturated (183 % of 200 %) at C=10, and C=10→50 added only 9 % work. Gitea needs ≤ 10 concurrent requests to be CPU-bound, so any N ≥ 10 would let it thrash as in S1.
  - Humans alone need little: S0 averages 0.27 cores, and concurrency is ≈ λ·latency ≈ 3 × 0.1 s ≈ 0.3.
  - N = 2 × cores keeps both cores busy while some requests wait on git subprocess I/O or on writing large responses. It is small enough that excess queues visibly at the gateway (`wait_ms`) instead of inside Gitea.
  - Check: S3's crawler work index should stay close to S1@C=50's. If it drops a lot, N=4 starved Gitea.
- `anon-cap`: cookieless requests share **N = 2** slots (one per core, so crawlers get at most ≈ 1 core-equivalent of heavy requests in flight). Cookie holders get a separate **M = 4** (same size as global N; humans use ≈ 0.3 concurrent on average).
  - The pools are independent, so Gitea may run up to 6 requests at once.
  - Each human's very first request has no cookie yet and goes through the anon pool. That is 10 requests per run, all in warmup.
- Waiting clients that disconnect are removed from the FIFO and logged as 499.
- Crawler behaviour on 429/503: the worker sleeps `Retry-After` (1 s) before its next request. Without this, a naive crawler at C=50 would spin on instant 429s at thousands of req/s, measuring the load generator instead of Gitea.
- S2 is run with **both** naive (fixed IP: the case the rate limit is designed for) and rotating (the case it is expected to fail).
- The gateway is 387 lines of Go excluding tests (main.go 209 + admit.go 178), over the spec's 200-line target. The admission policies were kept in a separate file for reuse by FairQueue.

## Stage 5 (Q3)
- S5/S6 use `anon-cap` with anon N = 2 and cookie M = 4, C = 50, 3 reps each. These are the same parameters as S4, so S4 is the direct baseline.
- **cookie-keeper**:
  - Obtains **one** session cookie through the gateway (`GET /poc/redis`) at the start of warmup. That request waits in the anon pool like any first visit.
  - All 50 workers send that one cookie on every request.
  - XFF keeps rotating.
- **cookie-farm**:
  - Obtains **100** cookies the same way (FARM_SIZE=100, as in the spec), then round-robins them per request.
  - XFF keeps rotating.
  - To the gateway, keeper and farm look the same: `anon-cap` only asks "is there a cookie?". The farm case matters for any policy that keys on the session (e.g. per-cookie limits). It is included to show the anon-cap outcome does not depend on how many cookies the crawler holds.
- Cookies are sent as a fixed `Cookie` header, and the crawler's cookie jar stays disabled.
- **Slot occupancy** is computed from the gateway log (`analysis/summarize.py: slot_occupancy`). No gateway change was made.
  - A request holds a slot from arrival + wait_ms to arrival + wait_ms + upstream_ms, clipped to the measure window.
  - Its pool follows from mode + cookie flag.
  - Reported per pool: human and crawler slot-seconds as a share of capacity (slots × window), the crawler share of occupied slot-seconds, and wait p50/p95 per class.
  - Requests rejected (429/503) or cancelled while waiting are excluded, because they held no slot.
  - For reference, in S4 the cookie pool was 0 % crawler (humans used ~16 % of its capacity), and the anon pool was 99.7 % crawler.

## Load generator
- The human simulator is **open-loop**: Poisson arrivals at about 3 req/s in total, so the send schedule does not depend on responses. This avoids coordinated omission. The spec's closed-loop design (10 users, 2–5 s think time) is replaced.
  - There are still 10 virtual users, each with its own cookie jar and its own fixed XFF (`10.0.0.1`–`10.0.0.10`).
  - Each arrival goes to a random user and advances that user's home → src → commits → commit cycle.
- **Humans are returning visitors (from stage 4 on):** before t0, each user fetches `http://gitea:3000/` directly (bypassing the gateway) and stores its `i_like_gitea` cookie. Every measured human request therefore carries a cookie.
  - Found in the stage 4 smoke test: without priming, a user's open-loop requests stay cookieless until its first response returns. In anon-cap those first requests queue behind crawlers in the anon pool, so only 61 % of human requests carried a cookie. A 503 there (no Set-Cookie) left the user cookieless indefinitely.
  - That is a cold-start artifact, not the phenomenon under test.
  - S0/S1 (unprimed) are unaffected in the measure window: every user had its cookie within the first seconds of warmup, and S0 sessions = 10.
  - Priming creates the same 10 sessions, just earlier.
- Crawler URL mix: **commit 50 % / blame 25 % / src 15 % / compare 10 %**, weighted random per request. Each type's pool is shuffled per run and consumed without replacement.
  - A link-following crawler reaches commit pages most often, because every commits-list page links 50 commits.
  - blame links sit on every file view.
  - src is the file views themselves.
  - compare links are rarer in the UI, but they are the most expensive (~47× a commits page).
  - This mix is a design assumption, not taken from real crawler logs.
  - Expected upstream cost per request, from the stage 1 pooled medians: 0.5·83 + 0.25·163 + 0.15·32 + 0.10·1544 ≈ 241 ms. compare is ≈ 64 % of that despite being 10 % of requests.
  - The commits list is left out of the crawler mix. With only 267 pages it would soon repeat URLs, and it is also the human's page-1 route.
- S1 uses the **rotating** crawler only. In `MODE=none` the gateway ignores IPs, so naive and rotating look identical to Gitea. naive is used only in S2 (IP rate limit).
- Crawler workers are closed-loop (C concurrent workers, 30 s timeout). Requests still in flight at the run deadline are cut and recorded as `cut`.
- naive/rotating crawlers do not store cookies. Each request uses an empty cookie jar and the Cookie header is removed.
- rotating crawler IPs are drawn at random from outside 10.0.0.0/24, so they never overlap the human IP range.
