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

## Load generator
- The human simulator is **open-loop**: Poisson arrivals at about 3 req/s in total, so the send schedule does not depend on responses. This avoids coordinated omission. The spec's closed-loop design (10 users, 2–5 s think time) is replaced.
  - There are still 10 virtual users, each with its own cookie jar and its own fixed XFF (`10.0.0.1`–`10.0.0.10`).
  - Each arrival goes to a random user and advances that user's home → src → commits → commit cycle.
- naive/rotating crawlers do not store cookies. Each request uses an empty cookie jar and the Cookie header is removed.
- rotating crawler IPs are drawn at random from outside 10.0.0.0/24, so they never overlap the human IP range.
