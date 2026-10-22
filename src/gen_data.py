"""Synthetic FMCG distributor dataset (reproducible, seeded).

A Nigerian distributor selling 30 packaged goods to ~300 retail outlets in four regions,
weekly, from Jan 2025 to Sep 2026. No real company data is used.

The generator PLANTS a handful of effects so the analysis has something to find, and so the
estimators can be checked against ground truth. They are listed in PLANTED below and in the README.

Usage:
    python src/gen_data.py --out data/raw [--outlets 300] [--seed 42]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2025-01-06")  # a Monday
WEEKS = 90

REGIONS = ["Lagos", "Abuja", "Kano", "Port Harcourt"]
REGION_P = [0.40, 0.22, 0.18, 0.20]
REGION_SIZE = {"Lagos": 1.25, "Abuja": 1.0, "Kano": 0.85, "Port Harcourt": 0.95}
CHANNELS = ["Open market", "Kiosk", "Mini-mart", "Supermarket"]
CHANNEL_P = [0.30, 0.30, 0.28, 0.12]
CHANNEL_SIZE = {"Open market": 1.1, "Kiosk": 0.5, "Mini-mart": 1.0, "Supermarket": 2.6}
CHANNEL_DISC = {"Open market": 0.04, "Kiosk": 0.02, "Mini-mart": 0.03, "Supermarket": 0.06}

# category -> (items, base shelf price in NGN at Jan 2025, gross margin at Jan 2025)
CATEGORIES = {
    "Beverages": (["Bottled water 75cl", "Soft drink 50cl", "Malt drink 33cl", "Juice 1L", "Energy drink 33cl"], 300, 0.31),
    "Noodles & pasta": (["Instant noodles 70g", "Instant noodles 120g", "Spaghetti 500g", "Macaroni 500g", "Cup noodles"], 250, 0.27),
    "Staples": (["Rice 5kg", "Sugar 1kg", "Cooking oil 1L", "Garri 1kg", "Beans 1kg"], 2200, 0.19),
    "Home care": (["Detergent powder 500g", "Bar soap", "Dish liquid 500ml", "Bleach 750ml", "Toilet tissue 4pk"], 900, 0.29),
    "Personal care": (["Toothpaste 100g", "Body lotion 200ml", "Shampoo 250ml", "Deodorant", "Sanitary pads"], 1100, 0.35),
    "Dairy & seasoning": (["Powdered milk 400g", "Tomato paste 70g", "Seasoning cubes 400g", "Evaporated milk", "Margarine 250g"], 700, 0.25),
}

PLANTED = {
    "cost_pass_through": "Input cost grows ~1.4%/month; list prices follow 2 months later at 65% pass-through, squeezing margin.",
    "kano_supply_gap": "Kano runs out of its 5 best-selling SKUs for weeks 55-66 (partial stock-out days each week).",
    "outlet_lapse": "About 12% of outlets stop buying after week 45 (no churn flag is recorded).",
    "december_peak": "Demand is +25% in December and -8% in January.",
    "supermarket_discounts": "Supermarkets get the deepest discount, so they are large but lower-margin.",
}


def build_products(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for cat, (items, price, gm) in CATEGORIES.items():
        for item in items:
            p = round(price * rng.uniform(0.7, 1.5) / 10) * 10
            rows.append((cat, item, p, p * (1 - np.clip(gm + rng.normal(0, 0.03), 0.08, 0.4))))
    df = pd.DataFrame(rows, columns=["category", "sku_name", "base_price", "base_cost"])
    df.insert(0, "sku_id", [f"SKU{i:03d}" for i in range(1, len(df) + 1)])
    # popularity: heavy-tailed so a few SKUs dominate (Pareto effect)
    df["popularity"] = rng.permutation(1 / np.arange(1, len(df) + 1) ** 0.6)
    return df


def build_outlets(rng: np.random.Generator, n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    region = rng.choice(REGIONS, n, p=REGION_P)
    channel = rng.choice(CHANNELS, n, p=CHANNEL_P)
    reps = pd.DataFrame({"rep_id": [f"R{i:02d}" for i in range(1, 25)],
                         "region": np.repeat(REGIONS, [10, 5, 4, 5])})
    outlets = pd.DataFrame({"outlet_id": [f"O{i:04d}" for i in range(1, n + 1)], "region": region, "channel": channel})
    outlets["rep_id"] = [rng.choice(reps.loc[reps.region == r, "rep_id"]) for r in outlets.region]
    outlets["size_factor"] = rng.lognormal(0, 0.55, n) * outlets.channel.map(CHANNEL_SIZE) * outlets.region.map(REGION_SIZE)
    outlets["first_week"] = np.where(rng.random(n) < 0.8, 0, rng.integers(1, 30, n))
    lapse = rng.random(n) < 0.12
    outlets["lapse_week"] = np.where(lapse, rng.integers(45, 80, n), WEEKS + 1)
    return outlets, reps


def generate(seed: int = 42, n_outlets: int = 300) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    products = build_products(rng)
    outlets, reps = build_outlets(rng, n_outlets)
    weeks = pd.date_range(START, periods=WEEKS, freq="W-MON")
    month_no = (weeks.year - 2025) * 12 + weeks.month - 1

    # cost and price indices (planted: cost_pass_through)
    cost_idx = np.cumprod(np.r_[1.0, 1 + rng.normal(0.014, 0.004, month_no.max())])
    price_idx = np.r_[1.0, 1.0, 1.0 + 0.65 * (cost_idx[1:-1] - 1) / 1.0][: len(cost_idx)]
    seasonal = np.where(weeks.month == 12, 1.25, np.where(weeks.month == 1, 0.92, 1.0))

    n_sku = len(products)
    top4 = products.sort_values("popularity", ascending=False).sku_id.head(5).tolist()
    stockout_days = {r: np.zeros((WEEKS, n_sku), dtype=int) for r in REGIONS}
    # background stock-outs: rare 1-2 day gaps anywhere; planted Kano gap on top-4 SKUs, weeks 55-63
    for r in REGIONS:
        mask = rng.random((WEEKS, n_sku)) < 0.015
        stockout_days[r][mask] = rng.integers(1, 3, mask.sum())
    for s in top4:
        j = products.index[products.sku_id == s][0]
        stockout_days["Kano"][55:67, j] = rng.integers(4, 8, 12)

    reg_of = outlets.region.to_numpy()
    sales_rows, truth_rows = [], []
    base = outlets.size_factor.to_numpy()[:, None] * products.popularity.to_numpy()[None, :] * 0.9
    for w, wk in enumerate(weeks):
        active = ((outlets.first_week.to_numpy() <= w) & (outlets.lapse_week.to_numpy() > w))[:, None]
        demand = rng.poisson(base * seasonal[w] * active)
        served = demand.copy()
        for r in REGIONS:
            rows = np.where(reg_of == r)[0]
            frac = 1 - stockout_days[r][w] / 7.0
            served[np.ix_(rows, range(n_sku))] = rng.binomial(demand[rows], np.clip(frac, 0, 1)[None, :])
        lost = demand - served
        for r in REGIONS:
            rows = np.where(reg_of == r)[0]
            lu = lost[rows].sum(axis=0)
            for j in np.nonzero(lu)[0]:
                truth_rows.append((wk, r, products.sku_id[j], int(lu[j])))
        oi, sj = np.nonzero(served)
        month_idx = month_no[w]
        list_price = products.base_price.to_numpy()[sj] * price_idx[month_idx]
        promo = (rng.random(len(oi)) < 0.06) * rng.choice([0.05, 0.10], len(oi))
        disc = outlets.channel.map(CHANNEL_DISC).to_numpy()[oi] + promo
        sales_rows.append(pd.DataFrame({
            "week_start": wk.strftime("%Y-%m-%d"), "outlet_id": outlets.outlet_id.to_numpy()[oi],
            "sku_id": products.sku_id.to_numpy()[sj], "units": served[oi, sj],
            "list_price": list_price.round(2), "discount_pct": disc.round(3),
            "unit_cost": (products.base_cost.to_numpy()[sj] * cost_idx[month_idx]).round(2)}))
    sales = pd.concat(sales_rows, ignore_index=True)
    sales["net_price"] = (sales.list_price * (1 - sales.discount_pct)).round(2)

    # regional warehouse stock position, derived from what actually sold
    sold = sales.merge(outlets[["outlet_id", "region"]], on="outlet_id").groupby(
        ["week_start", "region", "sku_id"]).units.sum().rename("units_sold").reset_index()
    grid = pd.MultiIndex.from_product([weeks.strftime("%Y-%m-%d"), REGIONS, products.sku_id],
                                      names=["week_start", "region", "sku_id"]).to_frame(index=False)
    stock = grid.merge(sold, how="left", on=["week_start", "region", "sku_id"]).fillna({"units_sold": 0})
    wk_ix = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(weeks)}
    sk_ix = {s: j for j, s in enumerate(products.sku_id)}
    stock["stockout_days"] = [stockout_days[r][wk_ix[d], sk_ix[s]] for d, r, s in
                              zip(stock.week_start, stock.region, stock.sku_id)]
    cover = rng.uniform(1.5, 4.0, len(stock))
    stock["closing_stock"] = np.where(stock.stockout_days > 0, 0, (stock.units_sold * cover).round()).astype(int)
    stock["units_sold"] = stock.units_sold.astype(int)

    # monthly revenue targets per rep (prior-quarter run-rate x 1.05, set by sales ops)
    s2 = sales.merge(outlets[["outlet_id", "rep_id"]], on="outlet_id")
    s2["month"] = s2.week_start.str[:7]
    rev = s2.assign(rev=s2.units * s2.net_price).groupby(["rep_id", "month"]).rev.sum().unstack(fill_value=0)
    months = sorted(rev.columns)
    trg = []
    for i, m in enumerate(months):
        if i >= 3:
            prev = rev[months[i - 3:i]].mean(axis=1) * 1.05
            trg += [(rid, m, round(float(v), 0)) for rid, v in prev.items()]
    targets = pd.DataFrame(trg, columns=["rep_id", "month", "target_revenue"])

    truth = pd.DataFrame(truth_rows, columns=["week_start", "region", "sku_id", "lost_units"])
    truth["week_start"] = pd.to_datetime(truth.week_start).dt.strftime("%Y-%m-%d")
    return {"products": products.drop(columns=["popularity"]), "outlets": outlets.drop(columns=["size_factor", "first_week", "lapse_week"]),
            "reps": reps, "_truth_lapse": outlets[["outlet_id", "lapse_week"]], "sales": sales, "stock": stock, "targets": targets, "_truth_lost_units": truth}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--outlets", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in generate(a.seed, a.outlets).items():
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"{name:20s} {len(df):>9,} rows")


if __name__ == "__main__":
    main()
