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

## Load generator
- The human simulator is **open-loop**: Poisson arrivals at about 3 req/s in total, so the send schedule does not depend on responses. This avoids coordinated omission. The spec's closed-loop design (10 users, 2–5 s think time) is replaced.
  - There are still 10 virtual users, each with its own cookie jar and its own fixed XFF (`10.0.0.1`–`10.0.0.10`).
  - Each arrival goes to a random user and advances that user's home → src → commits → commit cycle.
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
