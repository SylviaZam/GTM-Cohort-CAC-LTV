#!/usr/bin/env python3
"""
GTM Cohort CAC/LTV (CLI)

Builds a lightweight SQL data mart (DuckDB in-memory) from orders + marketing spend CSVs,
computes acquisition cohorts, cumulative LTV, CAC, and LTV:CAC, and exports:

- reports/cac_ltv_cohorts.xlsx  (LTV_cumulative, Summary)
- assets/ltv_vs_cac.png         (quick preview for README)

Usage:
  python -m src.cohort_cli --orders data/orders_sample.csv --spend data/marketing_spend.csv \
      --out reports/cac_ltv_cohorts.xlsx
"""
from __future__ import annotations
import argparse
from pathlib import Path
import duckdb
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


def load_inputs(orders_path: Path, spend_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    orders = pd.read_csv(orders_path, parse_dates=["order_date"])
    spend = pd.read_csv(spend_path)
    orders["order_month"] = orders["order_date"].dt.to_period("M").astype(str)
    # normalize types
    spend["month"] = spend["month"].astype(str)
    return orders, spend


def build_mart_and_cohorts(orders: pd.DataFrame, spend: pd.DataFrame):
    con = duckdb.connect(database=":memory:")
    con.register("orders", orders)
    con.register("spend", spend)

    # Clean fact table
    con.execute("""
        CREATE OR REPLACE TABLE fact_orders AS
        SELECT order_id, order_date, order_month, customer_id, channel, revenue
        FROM orders
    """)

    # First order (acquisition) per customer
    con.execute("""
        CREATE OR REPLACE TABLE first_orders AS
        SELECT
          customer_id,
          MIN(order_date) AS first_order_date,
          strftime(MIN(order_date), '%Y-%m') AS cohort_month,
          ANY_VALUE(channel) AS acquisition_channel
        FROM fact_orders
        GROUP BY customer_id
    """)

    # Join back to compute months since cohort
    con.execute("""
        CREATE OR REPLACE TABLE fct AS
        SELECT
          f.*,
          fo.cohort_month,
          fo.acquisition_channel,
          date_diff('month',
                    date_trunc('month', fo.first_order_date),
                    date_trunc('month', f.order_date)) AS months_since_cohort
        FROM fact_orders f
        JOIN first_orders fo USING (customer_id)
    """)

    cohort_rev = con.execute("""
        SELECT cohort_month, months_since_cohort, SUM(revenue) AS revenue
        FROM fct
        GROUP BY 1,2
        ORDER BY 1,2
    """).df()

    acq_counts = con.execute("""
        SELECT cohort_month, COUNT(DISTINCT customer_id) AS new_customers
        FROM first_orders
        GROUP BY 1
        ORDER BY 1
    """).df()

    # Simple CAC: total spend for the cohort month / total new customers that month
    # (Keeps the demo robust; avoids per-channel allocation ambiguity.)
    spend_by_month = spend.groupby("month", as_index=False)["spend"].sum().rename(columns={"month": "cohort_month",
                                                                                           "spend": "spend_total"})
    cac = acq_counts.merge(spend_by_month, on="cohort_month", how="left").fillna({"spend_total": 0})
    cac["CAC"] = cac["spend_total"] / cac["new_customers"].replace(0, np.nan)

    return cohort_rev, acq_counts, cac


def ltv_tables(cohort_rev: pd.DataFrame, acq_counts: pd.DataFrame):
    ltv = cohort_rev.merge(acq_counts, on="cohort_month", how="left")
    ltv["ltv"] = ltv["revenue"] / ltv["new_customers"].replace(0, np.nan)
    ltv_pivot = ltv.pivot(index="cohort_month", columns="months_since_cohort", values="ltv").fillna(0).sort_index()
    ltv_cum = ltv_pivot.cumsum(axis=1)
    return ltv_cum


def export_excel(ltv_cum: pd.DataFrame, cac_df: pd.DataFrame, out_xlsx: Path):
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)

    latest = ltv_cum.columns.max() if len(ltv_cum.columns) else 0
    summary = ltv_cum[[latest]].rename(columns={latest: "LTV_latest"}).reset_index()
    summary = summary.merge(cac_df[["cohort_month", "CAC"]], on="cohort_month", how="left")
    summary["LTV_to_CAC"] = summary["LTV_latest"] / summary["CAC"]

    with pd.ExcelWriter(out_xlsx, engine="xlsxwriter") as writer:
        ltv_cum.to_excel(writer, sheet_name="LTV_cumulative")
        summary.to_excel(writer, index=False, sheet_name="Summary")

        # autosize columns
        for name, df in [("LTV_cumulative", ltv_cum.reset_index()), ("Summary", summary)]:
            ws = writer.sheets[name]
            for j, col in enumerate(df.columns):
                width = max(12, min(40,
                                    len(str(col)) + 4,
                                    int(df[col].astype(str).str.len().quantile(0.9)) + 2))
                ws.set_column(j, j, width)
    return summary


def export_png(summary: pd.DataFrame, out_png: Path):
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 4))
    plt.bar(summary["cohort_month"], summary["LTV_latest"], label="LTV")
    plt.plot(summary["cohort_month"], summary["CAC"], marker="o", label="CAC")
    plt.title("Cohort LTV vs CAC")
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("USD")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_png, dpi=160)
    plt.close()


def main():
    ap = argparse.ArgumentParser(description="Build CAC/LTV cohorts from orders + spend CSVs.")
    ap.add_argument("--orders", required=True, help="Path to orders CSV (order_id, order_date, customer_id, channel, revenue).")
    ap.add_argument("--spend", required=True, help="Path to spend CSV (month YYYY-MM, channel, spend).")
    ap.add_argument("--out", default="reports/cac_ltv_cohorts.xlsx", help="Output Excel path.")
    args = ap.parse_args()

    orders, spend = load_inputs(Path(args.orders), Path(args.spend))
    cohort_rev, acq_counts, cac = build_mart_and_cohorts(orders, spend)
    ltv_cum = ltv_tables(cohort_rev, acq_counts)
    summary = export_excel(ltv_cum, cac, Path(args.out))
    export_png(summary, Path("assets/ltv_vs_cac.png"))

    print("[OK] Wrote:", args.out, "and assets/ltv_vs_cac.png")


if __name__ == "__main__":
    main()
