"""Run the cohort SQL in DuckDB and shape the results.

All cohort logic lives in sql/cohorts.sql (and sql/cac.sql when spend data is
given); Python handles inputs, pivots, and the bootstrap for country intervals.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import duckdb
import numpy as np
import pandas as pd

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


@dataclass
class Window:
    first_cohort: pd.Timestamp
    last_full_month: pd.Timestamp


def default_window(orders: pd.DataFrame) -> Window:
    """Skip the first month of data (left-censored) and the last month if it is incomplete."""
    dates = pd.to_datetime(orders["order_date"])
    first = dates.min().to_period("M")
    last = dates.max()
    last_full = last.to_period("M") if last.is_month_end else last.to_period("M") - 1
    return Window((first + 1).to_timestamp(), last_full.to_timestamp())


def prepare_orders(orders: pd.DataFrame) -> pd.DataFrame:
    required = {"order_id", "order_date", "customer_id", "revenue"}
    missing = required - set(orders.columns)
    if missing:
        raise ValueError(f"orders file is missing columns: {', '.join(sorted(missing))}")
    o = orders.copy()
    o["order_date"] = pd.to_datetime(o["order_date"]).dt.date
    o["order_id"] = o["order_id"].astype(str)
    if "country" not in o.columns:
        o["country"] = "All"
    return o[["order_id", "order_date", "customer_id", "country", "revenue"]]


def prepare_spend(spend: pd.DataFrame) -> pd.DataFrame:
    s = spend.copy()
    s["month"] = pd.to_datetime(s["month"].astype(str).str[:7] + "-01").dt.date
    return s.groupby("month", as_index=False)["spend"].sum()


def run(orders: pd.DataFrame, spend: Optional[pd.DataFrame] = None, window: Optional[Window] = None,
        horizons: Sequence[int] = (3, 6, 12)) -> duckdb.DuckDBPyConnection:
    orders = prepare_orders(orders)
    window = window or default_window(orders)
    con = duckdb.connect()
    con.register("orders", orders)
    con.register("params", pd.DataFrame({"first_cohort": [window.first_cohort.date()],
                                         "last_full_month": [window.last_full_month.date()]}))
    con.register("horizons", pd.DataFrame({"h": list(horizons)}))
    con.execute((SQL_DIR / "cohorts.sql").read_text())
    if spend is not None:
        con.register("spend", prepare_spend(spend))
        con.execute((SQL_DIR / "cac.sql").read_text())
    return con


def matrix(con, view: str, value: str) -> pd.DataFrame:
    """Cohort x month-since-acquisition table; unobserved cells stay blank (NaN)."""
    df = con.execute(f"SELECT cohort_month, month_index, {value} FROM {view}").df()
    df["cohort_month"] = pd.to_datetime(df["cohort_month"]).dt.strftime("%Y-%m")
    return df.pivot(index="cohort_month", columns="month_index", values=value).sort_index()


def cohort_summary(con, horizons: Sequence[int] = (3, 6, 12)) -> pd.DataFrame:
    size = con.execute("SELECT cohort_month, cohort_size FROM cohort_size").df()
    size["cohort_month"] = pd.to_datetime(size["cohort_month"]).dt.strftime("%Y-%m")
    out = size.set_index("cohort_month").sort_index()
    ret, ltv = matrix(con, "retention", "retention_rate"), matrix(con, "cohort_ltv", "ltv_cumulative")
    for k in (1, 3):
        if k in ret.columns:
            out[f"retention_m{k}"] = ret[k]
    for h in horizons:
        if h - 1 in ltv.columns:
            out[f"ltv_{h}m"] = ltv[h - 1]
    return out.reset_index()


def bootstrap_mean(values: np.ndarray, n_boot: int = 5000, seed: int = 42) -> tuple:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n_boot, len(values)))
    means = values[idx].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def by_country(con, horizon: int = 12, min_customers: int = 50, seed: int = 42) -> pd.DataFrame:
    """Value per customer over the first `horizon` months, by country of first purchase.

    Countries with fewer than `min_customers` eligible customers are pooled as
    'Other international'. Means are skewed by a few very large buyers, so the
    table shows the median too and a bootstrap 95% interval for the mean.
    """
    # ORDER BY: SQL returns rows in no fixed order, and the bootstrap must see the same order every run.
    df = con.execute("SELECT customer_id, country, ltv FROM customer_ltv WHERE horizon = ? ORDER BY customer_id",
                     [horizon]).df()
    if df.empty:
        return pd.DataFrame()
    counts = df["country"].value_counts()
    big = counts[counts >= min_customers].index
    df["group"] = np.where(df["country"].isin(big), df["country"], "Other international")
    ret = con.execute("""
        SELECT c.country, c.customer_id,
               MAX(CASE WHEN cm.month_index BETWEEN 1 AND 3 AND cm.purchases > 0 THEN 1 ELSE 0 END) AS repeat_3m
        FROM cohort_customers c CROSS JOIN params p
        LEFT JOIN customer_months cm USING (customer_id)
        WHERE c.cohort_month + to_months(3) <= p.last_full_month
        GROUP BY ALL""").df()
    ret["group"] = np.where(ret["country"].isin(big), ret["country"], "Other international")
    rows = []
    for group, g in df.groupby("group"):
        lo, hi = bootstrap_mean(g["ltv"].to_numpy(), seed=seed)
        r = ret[ret["group"] == group]
        rows.append({"country": group, "customers": len(g), f"mean_ltv_{horizon}m": g["ltv"].mean(),
                     "ci_low": lo, "ci_high": hi, f"median_ltv_{horizon}m": g["ltv"].median(),
                     "repeat_within_3m": r["repeat_3m"].mean() if len(r) else np.nan})
    return pd.DataFrame(rows).sort_values("customers", ascending=False).reset_index(drop=True)


def affordable_cac(summary: pd.DataFrame, margins=(0.3, 0.4, 0.5), horizons=(3, 6, 12)) -> pd.DataFrame:
    """Most you could pay to acquire a customer and break even within h months, at a given gross margin.

    Uses the customer-weighted average LTV across cohorts old enough for each horizon.
    """
    rows = []
    for h in horizons:
        col = f"ltv_{h}m"
        if col not in summary:
            continue
        s = summary.dropna(subset=[col])
        ltv = np.average(s[col], weights=s["cohort_size"])
        rows.append({"payback_within": f"{h} months", "cohorts_used": len(s), "avg_ltv_revenue": ltv,
                     **{f"max_cac_at_{int(m * 100)}pct_margin": ltv * m for m in margins}})
    return pd.DataFrame(rows)


def cac_tables(con) -> Optional[pd.DataFrame]:
    try:
        df = con.execute("""
            SELECT c.cohort_month, c.cohort_size, c.spend, c.blended_cac, p.payback_month
            FROM cac c JOIN payback p USING (cohort_month) ORDER BY c.cohort_month""").df()
    except duckdb.CatalogException:
        return None
    df["cohort_month"] = pd.to_datetime(df["cohort_month"]).dt.strftime("%Y-%m")
    return df


def season_comparison(con, horizon: int = 12, n_boot: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Value of customers acquired Oct-Dec (holiday) vs Apr-Sep (mid-year).

    A 12-month window covers every calendar month once, so this comparison is not
    distorted by when each cohort's later months fall. Jan-Mar cohorts are left out:
    right after the data starts they still include returning customers.
    """
    d = con.execute("SELECT month(cohort_month) AS m, ltv FROM customer_ltv WHERE horizon = ? ORDER BY customer_id",
                    [horizon]).df()
    a = d.loc[d["m"].between(10, 12), "ltv"].to_numpy()
    b = d.loc[d["m"].between(4, 9), "ltv"].to_numpy()
    if len(a) == 0 or len(b) == 0:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    diffs = (a[rng.integers(0, len(a), (n_boot, len(a)))].mean(axis=1)
             - b[rng.integers(0, len(b), (n_boot, len(b)))].mean(axis=1))
    return pd.DataFrame([{
        "horizon_months": horizon, "holiday_customers": len(a), "midyear_customers": len(b),
        "holiday_mean": a.mean(), "midyear_mean": b.mean(), "difference": a.mean() - b.mean(),
        "ci_low": np.quantile(diffs, 0.025), "ci_high": np.quantile(diffs, 0.975),
        "holiday_median": np.median(a), "midyear_median": np.median(b),
    }])
