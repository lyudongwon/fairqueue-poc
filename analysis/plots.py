"""Figures from per-run summaries.

fig1_s1_human_p95.png: human p95 vs crawler concurrency C (S1, rotating), with
the S0 baseline and the Q1 threshold (3x S0) as reference lines. Each rep is a
dot; the line connects per-C medians.
"""
import csv
import glob
import json
import os
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker

OUT = "results/figures"
BLUE, INK, INK2, GRID, SURFACE = "#2a78d6", "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def runs(scen):
    out = []
    for p in glob.glob(f"results/runs/{scen}_*/summary.json"):
        m = json.load(open(p.replace("summary.json", "meta.json")))
        if m["status"] == "ok":
            out.append((m, json.load(open(p))))
    return out


def fig1():
    s0 = next(float(r["human_p95_ms"]) for r in csv.DictReader(open("results/summary.csv")) if r["scenario"] == "S0")
    by_c = {}
    for m, s in runs("S1"):
        if m["crawler"] == "rotating":
            by_c.setdefault(m["c"], []).append(s["human"]["p95_ms"] / 1000)
    cs = sorted(by_c)
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150, facecolor=SURFACE)
    style(ax)
    ax.axhline(s0 / 1000, color=INK2, linewidth=1, linestyle=(0, (2, 2)))
    ax.axhline(3 * s0 / 1000, color=INK2, linewidth=1, linestyle=(0, (6, 3)))
    ax.text(cs[-1], s0 / 1000, f" S0 baseline {s0:.0f} ms", va="bottom", ha="right", fontsize=8.5, color=INK2)
    ax.text(cs[-1], 3 * s0 / 1000, f" Q1 threshold 3×S0 = {3 * s0:.0f} ms", va="bottom", ha="right",
            fontsize=8.5, color=INK2)
    med = [statistics.median(by_c[c]) for c in cs]
    ax.plot(cs, med, color=BLUE, linewidth=2, marker="o", markersize=7,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    for c in cs:
        if len(by_c[c]) > 1:
            ax.scatter([c] * len(by_c[c]), by_c[c], s=14, color=BLUE, alpha=0.45, zorder=2)
    for c, v in zip(cs, med):
        ax.annotate(f"{v:.1f} s", (c, v), textcoords="offset points", xytext=(0, 9), ha="center",
                    fontsize=8.5, color=INK)
    ax.set_xscale("log")
    ax.set_xticks(cs, [str(c) for c in cs])
    ax.minorticks_off()
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g} s"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel("crawler concurrency C (rotating, cookieless)", color=INK2, fontsize=9)
    ax.set_ylabel("human p95 latency (s, log)", color=INK2, fontsize=9)
    n = max(len(v) for v in by_c.values())
    ax.set_title(f"S1: human p95 vs crawler concurrency (n={n} rep/C)", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, "fig1_s1_human_p95.png"), facecolor=SURFACE)
    print("wrote fig1")


if __name__ == "__main__":
    fig1()
