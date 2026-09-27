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
    reps = ", ".join(f"C={c}: {len(by_c[c])}" for c in cs if len(by_c[c]) > 1)
    note = f"reps — {reps}; others: 1 (line = median)" if reps else "1 rep per C"
    ax.set_title(f"S1: human p95 vs crawler concurrency\n{note}", color=INK, fontsize=10.5, loc="left")
    fig.tight_layout()
    os.makedirs(OUT, exist_ok=True)
    fig.savefig(os.path.join(OUT, "fig1_s1_human_p95.png"), facecolor=SURFACE)
    print("wrote fig1")


def hbar(ax, labels, values, fmt, color=BLUE):
    y = range(len(labels))
    ax.barh(y, values, height=0.6, color=color, edgecolor=SURFACE, linewidth=2, zorder=2)
    ax.set_yticks(list(y), labels)
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    top = max(values) if values else 1
    for i, v in enumerate(values):
        ax.text(v + top * 0.01, i, fmt(v), va="center", fontsize=8.5, color=INK)
    ax.set_xlim(0, top * 1.18)


def fig2_fig3():
    from scenario_table import label, scenario_rows
    rows = scenario_rows()
    labels = [label(r) for r in rows]
    s0 = next(float(r["human_p95_ms"]) for r in rows if r["scenario"] == "S0")

    fig, ax = plt.subplots(figsize=(7.5, 0.5 * len(rows) + 1.4), dpi=150, facecolor=SURFACE)
    style(ax)
    p95 = [float(r["human_p95_ms"]) / 1000 for r in rows]
    hbar(ax, labels, p95, lambda v: f"{v:.2f} s")
    ax.axvline(1.5 * s0 / 1000, color=INK2, linewidth=1, linestyle=(0, (6, 3)), zorder=3)
    ax.text(1.5 * s0 / 1000, len(rows) - 0.4, f" Q2: 1.5×S0 = {1.5 * s0:.0f} ms", fontsize=8.5, color=INK2, va="bottom")
    ax.set_xlabel("human p95 latency (s), pooled over reps", color=INK2, fontsize=9)
    ax.set_title(f"Human p95 by scenario (crawler C={50})", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig2_scenario_human_p95.png"), facecolor=SURFACE)

    crows = [r for r in rows if r["scenario"] != "S0"]
    cl = [label(r) for r in crows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 0.5 * len(crows) + 1.6), dpi=150, facecolor=SURFACE, sharey=True)
    for a in (a1, a2):
        style(a)
    hbar(a1, cl, [float(r["crawler_ok_rps"]) for r in crows], lambda v: f"{v:.2f}")
    a1.set_title("crawler 2xx requests / s", color=INK, fontsize=10, loc="left")
    hbar(a2, cl, [float(r["crawler_work_index"]) for r in crows], lambda v: f"{v:.2f}")
    a2.set_title("crawler work index (Gitea-s / s)", color=INK, fontsize=10, loc="left")
    fig.suptitle(f"Crawler throughput by scenario (C={50}, mean of reps)", color=INK, fontsize=11,
                 x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig3_scenario_crawler_throughput.png"), facecolor=SURFACE)
    print("wrote fig2, fig3")


def fig4():
    """Stage 7: human p95 per crawler kind, anon-cap vs fq-cost vs fq-cost-probation (small multiples)."""
    rows = {(r["mode"], r["crawler"]): r for r in csv.DictReader(open("results/summary.csv")) if r["c"] == "50"}
    s0 = next(float(r["human_p95_ms"]) for r in csv.DictReader(open("results/summary.csv")) if r["scenario"] == "S0")
    kinds = ["rotating", "cookie-keeper", "cookie-farm"]
    modes = ["anon-cap", "fq-cost", "fq-cost-probation"]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4), dpi=150, facecolor=SURFACE, sharex=True)
    top = max(float(rows[(m, k)]["human_p95_ms"]) for m in modes for k in kinds if (m, k) in rows) / 1000
    for ax, k in zip(axes, kinds):
        style(ax)
        labels = [m for m in modes if (m, k) in rows]
        vals = [float(rows[(m, k)]["human_p95_ms"]) / 1000 for m in labels]
        hbar(ax, labels, vals, lambda v: "≥30 s (timeout)" if v >= 29.9 else f"{v:.2f} s")
        ax.set_xlim(0, top * 1.5)
        ax.axvline(1.5 * s0 / 1000, color=INK2, linewidth=1, linestyle=(0, (6, 3)), zorder=3)
        ax.set_title(f"crawler: {k}", color=INK, fontsize=10, loc="left")
        ax.set_xlabel("human p95 (s)", color=INK2, fontsize=9)
    fig.suptitle(f"Human p95 by gateway policy (C=50, pooled over reps; dashed = 1.5×S0 = {1.5 * s0:.0f} ms)",
                 color=INK, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig4_fq_human_p95.png"), facecolor=SURFACE)
    print("wrote fig4")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    fig1()
    if len(sys.argv) > 1 and sys.argv[1] == "all":
        fig2_fig3()
        fig4()
