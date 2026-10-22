"""Charts for the README. One message per chart; titles state the finding."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC, FIG = ROOT / "data" / "processed", ROOT / "figures"
INK, MUTED, GRID = "#1f2933", "#6b7280", "#e5e7eb"
BLUE, ORANGE, GREEN, GREY = "#0072B2", "#D55E00", "#009E73", "#b6bbc4"
SRC = "Synthetic distributor data (seeded generator in src/gen_data.py). Author's analysis."

plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
                     "axes.axisbelow": True, "figure.dpi": 140, "axes.titleweight": "bold",
                     "axes.titlesize": 12.5, "axes.titlelocation": "left"})


def _finish(fig, ax, title, subtitle, name):
    ax.set_title(title, color=INK, pad=22)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=MUTED, fontsize=9)
    fig.text(0.01, 0.01, SRC, color=MUTED, fontsize=7)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIG / name, bbox_inches="tight")
    plt.close(fig)


def margin_squeeze(mo, s):
    m = mo[mo.weeks >= 4]
    fig, ax = plt.subplots(figsize=(9, 4.4))
    ax.plot(pd.to_datetime(m.month), m.gm_pct, color=BLUE, lw=2.2)
    for i in (0, len(m) - 1):
        ax.annotate(f"{m.gm_pct.iloc[i]:.1f}%", (pd.to_datetime(m.month.iloc[i]), m.gm_pct.iloc[i]),
                    xytext=(0, 9), textcoords="offset points", ha="center", fontsize=9, color=INK)
        ax.plot(pd.to_datetime(m.month.iloc[i]), m.gm_pct.iloc[i], "o", color=BLUE, ms=5)
    ax.set_ylim(0, m.gm_pct.max() * 1.25); ax.set_ylabel("Gross margin (% of revenue)")
    _finish(fig, ax, f"Gross margin fell by about {s['gm_drop_pp']:.0f} points as costs outran prices",
            f"Like-for-like, unit cost rose {s['cost_change_pct_wavg']:.0f}% but net price only {s['price_change_pct_wavg']:.0f}%",
            "01_margin_squeeze.png")


def pareto(p, s):
    fig, ax = plt.subplots(figsize=(9, 4.4))
    col = p.abc_class.map({"A": BLUE, "B": GREY, "C": "#dfe2e7"})
    share = p.revenue / p.revenue.sum() * 100
    ax.bar(range(len(p)), share, color=col, width=0.75)
    ax.plot(range(len(p)), p.cum_share * 100, color=ORANGE, lw=2)
    ax.axhline(80, color=MUTED, lw=0.8, ls=":"); ax.text(len(p) - 1, 82, "80%", ha="right", fontsize=8, color=MUTED)
    ax.set_ylim(0, 105); ax.set_xticks([]); ax.set_xlabel("SKUs, ranked by revenue")
    ax.set_ylabel("% of total revenue")
    note = f"Bars: each SKU's share\nLine: cumulative share\n{s['skus_class_a']} A-class SKUs = {s['class_a_revenue_share']:.0f}% of revenue"
    ax.text(len(p) * 0.38, 40, note, color=INK, fontsize=9)
    _finish(fig, ax, f"{s['skus_class_a']} of {s['skus_total']} products earn 80% of revenue",
            "Revenue concentration by SKU; A-class (blue) = first 80% of revenue", "02_pareto.png")


def channels(rc, s):
    g = rc.groupby("channel").apply(lambda d: pd.Series({"revenue": d.revenue.sum(),
                                                         "gm_pct": np.average(d.gm_pct, weights=d.revenue)}), include_groups=False)
    g = g.sort_values("gm_pct")
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ax.barh(g.index, g.gm_pct, color=[ORANGE if c == "Supermarket" else BLUE for c in g.index], height=0.55)
    for i, (c, r) in enumerate(g.iterrows()):
        ax.text(r.gm_pct + 0.2, i, f"{r.gm_pct:.1f}% margin · {r.revenue / g.revenue.sum() * 100:.0f}% of revenue", va="center", fontsize=9, color=INK)
    ax.set_xlim(0, g.gm_pct.max() * 1.9); ax.grid(axis="y", visible=False); ax.set_xlabel("Gross margin (% of revenue)")
    _finish(fig, ax, "Supermarkets are a quarter of revenue but the lowest-margin channel",
            "Gross margin by channel (deepest discounts go to supermarkets)", "03_channels.png")


def stockouts(so, s):
    so = so.copy(); so["week_start"] = pd.to_datetime(so.week_start)
    w = so.assign(kano=so.region == "Kano").groupby(["week_start", "kano"]).est_lost_revenue.sum().unstack(fill_value=0)
    w = w.reindex(pd.date_range(w.index.min(), w.index.max(), freq="W-MON"), fill_value=0)
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.bar(w.index, w.get(False, 0) / 1e3, width=5, color=GREY, label="Other regions")
    ax.bar(w.index, w.get(True, 0) / 1e3, width=5, bottom=w.get(False, 0) / 1e3, color=ORANGE, label="Kano")
    ax.legend(frameon=False, loc="upper left", fontsize=9); ax.set_ylabel("Estimated lost revenue (₦ thousand per week)")
    _finish(fig, ax, f"One Kano supply gap caused {s['kano_gap_share_of_all_lost_pct']:.0f}% of all stock-out losses",
            f"Estimated revenue lost to stock-outs ({s['stockout_loss_pct_of_revenue']:.1f}% of revenue; {s['lost_revenue_on_class_a_pct']:.0f}% on A-class SKUs)",
            "04_stockouts.png")


def reps(ra, s):
    last = ra[ra.month == ra.month.max()]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.hist(ra.attainment_pct.clip(40, 160), bins=np.arange(40, 165, 5), color=BLUE, rwidth=0.88)
    ax.axvline(100, color=ORANGE, lw=1.6); ax.text(101, ax.get_ylim()[1] * 0.92, "target", color=INK, fontsize=9)
    ax.set_xlabel("Monthly revenue as % of target (all rep-months)"); ax.set_ylabel("Rep-months")
    _finish(fig, ax, f"The median rep delivers {s['median_attainment_pct_all']:.0f}% of target; the spread is wide",
            f"10th to 90th percentile: {s['rep_attainment_p10_p90'][0]:.0f}% to {s['rep_attainment_p10_p90'][1]:.0f}% (targets = prior-quarter run-rate + 5%)",
            "05_rep_attainment.png")


def main():
    FIG.mkdir(exist_ok=True)
    s = json.loads((PROC / "summary.json").read_text())
    rd = lambda n: pd.read_csv(PROC / f"{n}.csv")
    margin_squeeze(rd("monthly"), s); pareto(rd("pareto"), s); channels(rd("region_channel"), s)
    stockouts(rd("stockout_loss"), s); reps(rd("rep_attainment"), s)


if __name__ == "__main__":
    main()
