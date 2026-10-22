import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import analysis  # noqa: E402
import gen_data  # noqa: E402


@pytest.fixture(scope="module")
def raw(tmp_path_factory):
    d = tmp_path_factory.mktemp("raw")
    for name, df in gen_data.generate(seed=42).items():
        df.to_csv(d / f"{name}.csv", index=False)
    return d


@pytest.fixture(scope="module")
def con(raw):
    c = analysis.connect(raw)
    return c


@pytest.fixture(scope="module")
def results(con):
    return analysis.run_sql(con)


def test_generator_is_deterministic():
    a, b = gen_data.generate(seed=7, n_outlets=40), gen_data.generate(seed=7, n_outlets=40)
    pd.testing.assert_frame_equal(a["sales"], b["sales"])


def test_referential_integrity(raw):
    sales, outlets, products = (pd.read_csv(raw / f"{t}.csv") for t in ("sales", "outlets", "products"))
    assert sales.outlet_id.isin(outlets.outlet_id).all()
    assert sales.sku_id.isin(products.sku_id).all()
    assert (sales.units > 0).all() and (sales.net_price > 0).all()
    assert not sales.duplicated(["week_start", "outlet_id", "sku_id"]).any()


def test_revenue_reconciles_to_raw(raw, results):
    sales = pd.read_csv(raw / "sales.csv")
    expected = (sales.units * sales.net_price).sum()
    assert results["monthly"].revenue.sum() == pytest.approx(expected, rel=1e-9)
    assert results["region_channel"].revenue.sum() == pytest.approx(expected, rel=1e-9)
    assert results["pareto"].revenue.sum() == pytest.approx(expected, rel=1e-9)


def test_pareto_classes_are_ordered_and_a_is_about_80pct(results):
    p = results["pareto"]
    assert p.cum_share.is_monotonic_increasing
    a_share = p[p.abc_class == "A"].revenue.sum() / p.revenue.sum()
    assert 0.80 <= a_share < 0.92
    assert list(p.abc_class) == sorted(p.abc_class)


def test_margin_squeeze_is_detected(results):
    lfl = results["like_for_like"]
    assert (lfl.cost_change_pct > lfl.price_change_pct).mean() > 0.9


def test_lapsed_outlet_detection_matches_ground_truth(raw, results):
    truth = pd.read_csv(raw / "_truth_lapse.csv")
    true_lapsed = set(truth[truth.lapse_week <= 78].outlet_id)
    found = set(results["lapsed_outlets"].outlet_id)
    assert len(found & true_lapsed) / len(found) >= 0.9


def test_stockout_loss_estimate_is_close_to_truth(raw, results):
    truth = pd.read_csv(raw / "_truth_lost_units.csv").lost_units.sum()
    est = results["stockout_loss"].est_lost_units.sum()
    assert 0.75 <= est / truth <= 1.25


def test_attainment_only_uses_complete_months(results):
    ra, mo = results["rep_attainment"], results["monthly"]
    partial = set(mo[mo.weeks < 4].month)
    assert not (set(ra.month) & partial)
