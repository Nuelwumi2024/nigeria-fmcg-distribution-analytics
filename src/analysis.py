"""Run the SQL analysis on DuckDB, validate estimators against ground truth, and summarise."""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "processed"
TABLES = ["products", "outlets", "reps", "sales", "stock", "targets"]


def connect(raw: Path = RAW) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for t in TABLES:
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_csv_auto('{(raw / (t + '.csv')).as_posix()}')")
    return con


def run_sql(con: duckdb.DuckDBPyConnection, sql_path: Path = ROOT / "sql" / "analysis.sql") -> dict[str, pd.DataFrame]:
    blocks = re.split(r"^-- name: (\w+)\s*$", sql_path.read_text(), flags=re.M)[1:]
    results = {}
    for name, body in zip(blocks[::2], blocks[1::2]):
        stmt = "\n".join(l for l in body.splitlines() if not l.strip().startswith("--")).strip().rstrip(";")
        cur = con.execute(stmt)
        if stmt.upper().startswith("SELECT") or stmt.upper().startswith("WITH"):
            results[name] = cur.df()
    return results


def forecast_backtest(con: duckdb.DuckDBPyConnection, holdout: int = 13) -> pd.DataFrame:
    """One-step-ahead weekly units by category over the last `holdout` weeks."""
    w = con.execute("SELECT week_no, category, sum(units) AS units FROM sales_enriched GROUP BY 1, 2").df()
    piv = w.pivot(index="week_no", columns="category", values="units").sort_index()
    rows = []
    for t in range(len(piv) - holdout, len(piv)):
        hist, actual = piv.iloc[:t], piv.iloc[t]
        for name, f in [("naive", hist.iloc[-1]), ("avg4", hist.iloc[-4:].mean()), ("avg8", hist.iloc[-8:].mean())]:
            rows.append(pd.DataFrame({"method": name, "category": piv.columns, "forecast": f.values, "actual": actual.values}))
    bt = pd.concat(rows)
    out = bt.groupby("method").apply(lambda g: pd.Series({
        "wape_pct": (g.forecast - g.actual).abs().sum() / g.actual.sum() * 100,
        "bias_pct": (g.forecast - g.actual).sum() / g.actual.sum() * 100}), include_groups=False)
    return out.reset_index()


def main() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect()
    res = run_sql(con)
    for name, df in res.items():
        df.to_csv(OUT / f"{name}.csv", index=False)
    s: dict = {}

    mo = res["monthly"]
    full = mo[mo.weeks >= 4]            # the final month is partial; keep it out of month comparisons
    s.update(revenue_total=float(mo.revenue.sum()), gm_pct_first_q=float(full.gm_pct.head(3).mean()),
             gm_pct_last_q=float(full.gm_pct.tail(3).mean()), months=len(mo))
    s["gm_drop_pp"] = s["gm_pct_first_q"] - s["gm_pct_last_q"]
    lfl = res["like_for_like"]
    wt = lfl.start_units
    s["price_change_pct_wavg"] = float(np.average(lfl.price_change_pct, weights=wt))
    s["cost_change_pct_wavg"] = float(np.average(lfl.cost_change_pct, weights=wt))
    last26 = float(con.execute("SELECT sum(revenue) FROM sales_enriched WHERE week_no > (SELECT max(week_no) FROM sales_enriched) - 26").fetchone()[0])
    s["revenue_last_26w"] = last26
    s["margin_gap_value_26w"] = last26 * s["gm_drop_pp"] / 100

    p = res["pareto"]
    s.update(skus_total=len(p), skus_class_a=int((p.abc_class == "A").sum()),
             class_a_revenue_share=float(p[p.abc_class == "A"].revenue.sum() / p.revenue.sum() * 100),
             class_c_skus=int((p.abc_class == "C").sum()), top_sku=p.sku_name.iloc[0])

    oc = res["outlet_concentration"]
    s["top10pct_outlet_share"] = float(oc.share_pct.iloc[0])
    rc = res["region_channel"]
    ch = rc.groupby("channel").apply(lambda g: pd.Series({"revenue": g.revenue.sum(), "gm_pct": np.average(g.gm_pct, weights=g.revenue)}), include_groups=False)
    s["supermarket_gm_pct"], s["kiosk_gm_pct"] = float(ch.loc["Supermarket", "gm_pct"]), float(ch.loc["Kiosk", "gm_pct"])
    s["supermarket_revenue_share"] = float(ch.loc["Supermarket", "revenue"] / ch.revenue.sum() * 100)

    # lapsed outlets, validated against the generator's ground truth
    lap = res["lapsed_outlets"]
    truth = pd.read_csv(RAW / "_truth_lapse.csv")
    true_lapsed = set(truth[truth.lapse_week <= 78].outlet_id)  # at least 12 silent weeks by week 90
    found = set(lap.outlet_id)
    s.update(lapsed_outlets=len(lap), lapsed_revenue_at_risk=float(lap.annual_revenue_at_risk.sum()),
             lapsed_precision_pct=float(len(found & true_lapsed) / max(len(found), 1) * 100),
             lapsed_recall_pct=float(len(found & true_lapsed) / max(len(true_lapsed), 1) * 100))

    ra = res["rep_attainment"]
    last_m = ra.month.max()
    s["reps_at_or_above_target_last_month_pct"] = float((ra[ra.month == last_m].attainment_pct >= 100).mean() * 100)
    s["median_attainment_pct_all"] = float(ra.attainment_pct.median())
    s["rep_attainment_p10_p90"] = [float(ra.attainment_pct.quantile(0.1)), float(ra.attainment_pct.quantile(0.9))]

    # stock-out loss estimator vs truth
    so = res["stockout_loss"]
    tr = pd.read_csv(RAW / "_truth_lost_units.csv")
    est = so.groupby(["region", "sku_id"]).est_lost_units.sum()
    tru = tr.groupby(["region", "sku_id"]).lost_units.sum()
    both = pd.concat([est, tru], axis=1, keys=["est", "true"]).fillna(0)
    s.update(est_lost_units=float(est.sum()), true_lost_units=float(tru.sum()),
             estimator_ratio=float(est.sum() / tru.sum()), est_lost_revenue=float(so.est_lost_revenue.sum()))
    kano = so[so.region == "Kano"].copy()
    kano_win = kano[(kano.week_start >= pd.Timestamp("2025-01-06") + pd.Timedelta(weeks=55)) &
                    (kano.week_start <= pd.Timestamp("2025-01-06") + pd.Timedelta(weeks=66))]
    s["kano_gap_lost_revenue"] = float(kano_win.est_lost_revenue.sum())
    s["kano_gap_share_of_all_lost_pct"] = float(kano_win.est_lost_revenue.sum() / so.est_lost_revenue.sum() * 100)
    s["stockout_loss_pct_of_revenue"] = float(so.est_lost_revenue.sum() / mo.revenue.sum() * 100)
    s["lost_revenue_on_class_a_pct"] = float(
        so.merge(p[["sku_id", "abc_class"]], on="sku_id").query("abc_class == 'A'").est_lost_revenue.sum() / so.est_lost_revenue.sum() * 100)

    bt = forecast_backtest(con)
    bt.to_csv(OUT / "forecast_backtest.csv", index=False)
    s["forecast_wape"] = {r.method: float(r.wape_pct) for r in bt.itertuples()}
    (OUT / "summary.json").write_text(json.dumps(s, indent=2, default=float))
    return s


if __name__ == "__main__":
    for k, v in main().items():
        print(f"{k:40s} {v:,.2f}" if isinstance(v, float) else f"{k:40s} {v}")
