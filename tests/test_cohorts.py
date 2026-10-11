"""Cohort SQL checked on tiny hand-built order sets with known answers."""
import duckdb
import numpy as np
import pandas as pd
import pytest

from src import cohorts as C


def orders(rows):
    return pd.DataFrame(rows, columns=["order_id", "order_date", "customer_id", "country", "revenue"])


def window(first, last):
    return C.Window(pd.Timestamp(first), pd.Timestamp(last))


BASE = orders([
    ("r0", "2023-01-10", 1, "UK", -20.0),   # return before customer 1's first purchase
    ("o1", "2023-02-05", 1, "UK", 100.0),   # customer 1 acquired in Feb
    ("o2", "2023-03-09", 1, "UK", 50.0),
    ("r1", "2023-04-02", 1, "UK", -30.0),   # return only: net value drops, not an active month
    ("o3", "2023-02-20", 2, "DE", 200.0),   # customer 2 acquired in Feb, never returns
    ("o4", "2023-03-15", 3, "UK", 80.0),    # customer 3 acquired in Mar
    ("o5", "2023-06-01", 3, "UK", 999.0),   # after the window: ignored
])


@pytest.fixture
def con():
    return C.run(BASE, window=window("2023-02-01", "2023-04-01"), horizons=(1, 2, 3))


def test_cohort_is_the_first_purchase_not_a_return(con):
    c = con.execute("SELECT customer_id, strftime(cohort_month, '%Y-%m') AS m FROM cohort_customers ORDER BY 1").df()
    assert c["m"].tolist() == ["2023-02", "2023-02", "2023-03"]


def test_retention_counts_purchases_only(con):
    r = C.matrix(con, "retention", "retention_rate")
    assert r.loc["2023-02", 0] == 1.0
    assert r.loc["2023-02", 1] == 0.5        # customer 1 bought again in March
    assert r.loc["2023-02", 2] == 0.0        # April is a return only


def test_ltv_nets_returns_and_leaves_unobserved_months_blank(con):
    ltv = C.matrix(con, "cohort_ltv", "ltv_cumulative")
    assert ltv.loc["2023-02", 0] == pytest.approx(150.0)   # (100 + 200) / 2
    assert ltv.loc["2023-02", 2] == pytest.approx(160.0)   # (100 + 50 - 30 + 200) / 2
    assert ltv.loc["2023-03", 1] == pytest.approx(80.0)
    assert np.isnan(ltv.loc["2023-03", 2])                 # May not observed yet


def test_customer_ltv_only_for_cohorts_old_enough(con):
    d = con.execute("SELECT customer_id, ltv FROM customer_ltv WHERE horizon = 3 ORDER BY 1").df()
    assert d["customer_id"].tolist() == [1, 2]             # March cohort has only 2 observed months
    assert d["ltv"].tolist() == [120.0, 200.0]


def test_default_window_skips_first_month_and_partial_last_month():
    w = C.default_window(orders([("a", "2009-12-01", 1, "UK", 1), ("b", "2011-12-09", 1, "UK", 1)]))
    assert (w.first_cohort, w.last_full_month) == (pd.Timestamp("2010-01-01"), pd.Timestamp("2011-11-01"))
    w = C.default_window(orders([("a", "2023-01-03", 1, "UK", 1), ("b", "2023-03-31", 1, "UK", 1)]))
    assert w.last_full_month == pd.Timestamp("2023-03-01")


def test_cac_and_payback_with_spend():
    spend = pd.DataFrame({"month": ["2023-02", "2023-02", "2023-03"], "channel": ["a", "b", "a"],
                          "spend": [100.0, 50.0, 500.0]})
    con = C.run(BASE, spend, window("2023-02-01", "2023-04-01"), horizons=(1,))
    cac = C.cac_tables(con).set_index("cohort_month")
    assert cac.loc["2023-02", "blended_cac"] == pytest.approx(75.0)   # 150 / 2 customers
    assert cac.loc["2023-02", "payback_month"] == 0                   # 150 of value in month 0 >= 75
    assert pd.isna(cac.loc["2023-03", "payback_month"])               # 80 never covers 500


def test_no_spend_means_no_cac_table(con):
    assert C.cac_tables(con) is None


def test_country_table_pools_small_countries_and_is_reproducible():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(300):
        country = "UK" if i < 240 else ("DE" if i < 290 else "FR")
        rows.append((f"a{i}", "2023-01-05", i, country, float(rng.integers(50, 150))))
        rows.append((f"b{i}", "2023-05-05", i, country, float(rng.integers(0, 100))))
    con = C.run(orders(rows), window=window("2023-01-01", "2023-12-01"))
    t1, t2 = C.by_country(con, 12, min_customers=40), C.by_country(con, 12, min_customers=40)
    assert set(t1["country"]) == {"UK", "DE", "Other international"}
    pd.testing.assert_frame_equal(t1, t2)
    assert (t1["ci_low"] <= t1["mean_ltv_12m"]).all() and (t1["mean_ltv_12m"] <= t1["ci_high"]).all()


def test_season_comparison_detects_lower_holiday_value():
    rows = []
    for i in range(200):
        holiday = i % 2 == 0
        first = "2022-11-10" if holiday else "2022-05-10"
        rows.append((f"x{i}", first, i, "UK", 100.0))
        rows.append((f"y{i}", "2023-04-10" if holiday else "2022-12-10", i, "UK", 20.0 if holiday else 200.0))
    con = C.run(orders(rows), window=window("2022-01-01", "2023-12-01"))
    s = C.season_comparison(con).iloc[0]
    assert s["difference"] == pytest.approx(-180.0) and s["ci_high"] < 0


def test_cleaning_sql_on_raw_lines():
    from pathlib import Path
    raw = pd.DataFrame({
        "Invoice":     ["1", "1", "1", "2", "3", "C4", "5", "6"],
        "StockCode":   ["A", "A", "POST", "BANK CHARGES", "A", "A", "A", "GIFT_0001_10"],
        "Description": ["x"] * 8,
        "Quantity":    [2, 2, 1, 1, 1, -1, 3, 1],
        "InvoiceDate": pd.to_datetime(["2010-01-05"] * 3 + ["2010-01-06", "2010-01-07", "2010-02-01",
                                                            "2010-02-02", "2010-02-03"]),
        "Price":       [5.0, 5.0, 15.0, 10.0, 0.0, 5.0, 4.0, 10.0],
        "Customer ID": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, np.nan, 10.0],
        "Country":     ["United Kingdom"] * 8,
    })
    con = duckdb.connect()
    con.register("raw_lines", raw)
    con.execute((Path(C.SQL_DIR) / "00_clean_lines.sql").read_text())
    out = con.execute("SELECT order_id, revenue, is_return FROM orders_clean ORDER BY order_id").df()
    # duplicate line dropped, postage kept: 2*5 + 15 = 25; fee, zero price, guest, voucher removed; return negative
    assert out.to_dict("records") == [{"order_id": "1", "revenue": 25.0, "is_return": False},
                                      {"order_id": "C4", "revenue": -5.0, "is_return": True}]
