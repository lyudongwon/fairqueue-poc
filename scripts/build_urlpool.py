"""Build URL pools from the real redis history (run inside the tools container).

Route types (6):
  home     /{o}/{r}                                  human
  src      /{o}/{r}/src/branch/{br}/{path}           human
  commits  /{o}/{r}/commits/branch/{br}?page=N       human (page 1) + crawler
  commit   /{o}/{r}/commit/{sha}                     human + crawler
  blame    /{o}/{r}/blame/commit/{sha}/{path}        crawler
  compare  /{o}/{r}/compare/{a}...{b}                crawler
"""
import json
import math
import os
import random
import subprocess
from urllib.parse import quote

BARE = os.environ.get("BARE", "/work/redis.git")
OWNER, REPO, BRANCH = "poc", "redis", "unstable"
OUT = os.environ.get("OUT", "urlpool")
SEED = 42
PAGE_SIZE = 50          # Gitea setting.Git.CommitsRangeSize default
COMPARE_MAX_DIST = 50   # bounds compare cost (see DECISIONS.md)
BLAME_POOL = 20000

base = f"/{OWNER}/{REPO}"


def git(*args):
    return subprocess.run(["git", "-C", BARE, *args], check=True,
                          capture_output=True, text=True).stdout


def write(name, urls):
    with open(os.path.join(OUT, f"{name}.txt"), "w") as f:
        f.write("\n".join(urls) + "\n")
    return len(urls)


def main():
    rng = random.Random(SEED)
    os.makedirs(OUT, exist_ok=True)
    head = git("rev-parse", BRANCH).strip()

    all_shas = git("rev-list", BRANCH).split()
    first_parent = git("rev-list", "--first-parent", BRANCH).split()
    head_paths = git("ls-tree", "-r", "--name-only", BRANCH).splitlines()

    # (sha, path) where path was added/modified in sha => exists at sha.
    pairs, sha = [], None
    for line in git("log", "--first-parent", BRANCH, "--diff-filter=AM",
                    "--name-only", "--format=@%H").splitlines():
        if line.startswith("@"):
            sha = line[1:]
        elif line:
            pairs.append((sha, line))
    rng.shuffle(pairs)
    pairs = pairs[:BLAME_POOL]

    compare = []
    for i in range(len(first_parent) - COMPARE_MAX_DIST):
        d = rng.randint(1, COMPARE_MAX_DIST)
        compare.append((first_parent[i + d], first_parent[i]))
    rng.shuffle(compare)

    pages = math.ceil(len(all_shas) / PAGE_SIZE)  # Gitea's list walks all commits, not first-parent
    q = lambda p: quote(p, safe="/")

    counts = {
        "home": write("home", [base]),
        "src": write("src", [f"{base}/src/branch/{BRANCH}/{q(p)}" for p in head_paths]),
        "commits": write("commits", [f"{base}/commits/branch/{BRANCH}?page={n}" for n in range(1, pages + 1)]),
        "commit": write("commit", [f"{base}/commit/{s}" for s in rng.sample(all_shas, len(all_shas))]),
        "blame": write("blame", [f"{base}/blame/commit/{s}/{q(p)}" for s, p in pairs]),
        "compare": write("compare", [f"{base}/compare/{a}...{b}" for a, b in compare]),
    }
    meta = {"source": "https://github.com/redis/redis", "branch": BRANCH, "head": head,
            "commits_reachable": len(all_shas), "commits_first_parent": len(first_parent),
            "seed": SEED, "compare_max_dist": COMPARE_MAX_DIST, "counts": counts}
    with open(os.path.join(OUT, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
