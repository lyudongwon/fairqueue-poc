# FairQueue PoC

Spec: `FAIRQUEUE_POC.md`. Decisions and deviations: [DECISIONS.md](DECISIONS.md).

Questions:
- **Q1.** When crawlers pile onto a local Gitea, does human p95 get noticeably worse?
- **Q2.** Does a simple cap on concurrent anonymous (cookieless) requests restore human latency?
- **Q3.** Does that cap break once crawlers keep a cookie, or farm many cookies?

## Environment

| item | value |
|---|---|
| host | Windows 11, 16 logical CPUs, 31 GB RAM |
| WSL2 VM | 12 vCPU, 16 GB (`.wslconfig`), kernel 6.6.87.2-microsoft-standard-WSL2 |
| Docker | Docker Desktop, Engine 29.1.3, cgroup v2 |
| Gitea | `gitea/gitea:1.27.3`, sqlite3, default cache, **session provider = memory** (life 86400 s, GC 86400 s) |
| Gitea limits | cpuset 2-3, `cpus: 2`, 2 GiB memory, swap off |
| gateway | Go 1.25 `httputil.ReverseProxy`, cpuset 4-5 |
| human sim | Python 3.12 asyncio + httpx 0.28.1, cpuset 6 |
| crawler | cpuset 7-10 |
| tools / monitor | cpuset 11 |
| unpinned | CPUs 0-1 (VM / dockerd overhead) |

Gitea settings that differ from image defaults:
- `INSTALL_LOCK=true`
- `ROOT_URL=http://gitea:3000/`
- `DISABLE_SSH=true`
- `DISABLE_REGISTRATION=true`

Gitea is restarted before every run, which clears its in-memory caches and sessions.

## Reproduce

```sh
make stage1                    # seed redis/redis, build URL pools, cost table
sh scripts/run.sh S0 1         # one run: MODE=none, no crawler
make s0                        # S0 x3
```

Each run writes `results/runs/<run_id>/`:

| file | contents |
|---|---|
| `human.csv` | every human request |
| `monitor.csv` | 1 s cgroup samples of gitea, gateway, human and crawler |
| `timeline.csv` | gitea CPU, memory and cumulative sessions per second |
| `summary.json` | per-run summary |
| `meta.json` | run parameters |
| `raw/gateway.jsonl` | gateway log (not committed) |

## Data

- redis/redis, `unstable` @ `20bb2cf`.
  - 13,304 commits (9,966 first-parent), 115 branches, 415 tags.
- URL pools (`urlpool/`, seed 42):

| pool | size |
|---|---|
| src | 1,871 |
| commits pages | 267 |
| commit | 13,304 |
| blame | 20,000 |
| compare | 9,916 (distance 1–50) |

## Stage 1 — single-request cost per route type

Requests were sequential and cookieless, with 30 distinct URLs per type, after a Gitea restart. The run was done twice (r1, r2) with the same URL set. Times are in ms. Regenerate with `python analysis/cost_table.py`.

| type | n | median r1 | median r2 | pooled median | pooled p95 | max | mean size | × home | × commits |
|---|---|---|---|---|---|---|---|---|---|
| home | 60 | 12.5 | 12.2 | 12.4 | 21.4 | 72.4 | 68 KB | 1.0 | 0.38 |
| src | 60 | 34.2 | 28.4 | 32.1 | 118.1 | 135.5 | 187 KB | 2.6 | 0.99 |
| commits | 60 | 33.2 | 32.5 | 32.5 | 51.1 | 70.6 | 208 KB | 2.6 | 1.00 |
| commit | 60 | 78.9 | 82.7 | 82.7 | 749.4 | 1218.9 | 196 KB | 6.7 | 2.54 |
| blame | 60 | 171.6 | 156.2 | 163.4 | 1306.2 | 1902.3 | 3096 KB | 13.2 | 5.02 |
| compare | 60 | 1550.1 | 1522.2 | 1543.8 | 2783.8 | 3221.4 | 2366 KB | 125.0 | 47.43 |

How to read the two multiples:
- **× home** overstates the spread. home has only one URL, so it was requested 30 times and served warm.
- **× commits** is the conservative baseline. It uses 30 distinct pages, so there are no per-URL cache hits. Branch-level shared caches, such as the commit count, may still apply.
  - Even on this baseline, compare is **~47×** and blame **~5×** the cost of a commits page.
  - commit and blame have heavy tails: p95 is about 9× their median.

## Stage 2 — baseline (S0)

Gateway `MODE=none`, no crawler. The human load is open-loop Poisson at 3 req/s: 10 users, each with its own cookie and XFF. Each run is 30 s warmup + 180 s measurement, repeated 3 times. Latency is measured from the scheduled send time. Aggregated in `results/summary.csv`.

| | n | p50 | p95 | p99 | fail rate |
|---|---|---|---|---|---|
| **S0 pooled (3 reps)** | 1,680 | 46.0 ms | **278.8 ms** | 661.6 ms | 0 % |
| per rep | 558 / 549 / 573 | 44.2 / 48.3 / 44.8 | 270.6 / 267.4 / 298.8 | 550.7 / 758.1 / 528.6 | 0 / 0 / 0 |

Human p95 by route, per rep (ms):

| route | r1 | r2 | r3 |
|---|---|---|---|
| home | 28 | 47 | 44 |
| src | 110 | 84 | 104 |
| commits | 86 | 91 | 97 |
| commit | 422 | 656 | 499 |

Overall p95 is driven by the heavy tail of the `commit` route, which is consistent with the stage 1 cost table.

Resources during the measure window (per rep):

| | r1 | r2 | r3 |
|---|---|---|---|
| gitea CPU mean (% of 1 core; limit 200) | 23.6 | 29.3 | 27.4 |
| gitea CPU PSI "some" mean | 1.2 % | 1.4 % | 1.4 % |
| gitea memory start → peak | 93 → 194 MB | 98 → 233 MB | 93 → 213 MB |
| peak / 2 GiB limit | 9.5 % | 11.4 % | 10.4 % |
| memory PSI max / memory.events max / oom_kill | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| sessions created (live) | 10 | 10 | 10 |
| memory bottleneck | no | no | no |

Sanity checks:
- The generator is not lagging: send-lag p99 is ≤ 2.9 ms.
- Load-generator containers are not saturated: gateway CPU p95 ≈ 2 %, human CPU p95 ≈ 2 %.
- The gateway adds nothing measurable: `wait_ms` max is 0.09 ms. Client-side p50 minus gateway upstream p50 is ≈ 5 ms, which includes the Python client and one extra hop.
- Sessions: exactly one per user (10), so humans reuse their cookies as intended.

**Q1 threshold derived from S0:** S1 counts as a success when human p95 ≥ 3 × 278.8 ≈ **836 ms**.

## Known limitations

- `X-Forwarded-For` only simulates IP rotation. The requests do not come from different network paths.
- Everything runs on a single machine, so network latency is negligible.
- The human simulator is not a browser. It skips static assets.
- Cookie presence is judged by the cookie name only. A fabricated `i_like_gitea` value would pass.
- The session provider is memory. Every cookieless request creates a server-side session that lives 24 h, so a cookieless crawler also grows Gitea's heap.
