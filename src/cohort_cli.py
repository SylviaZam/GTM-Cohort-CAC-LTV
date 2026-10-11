#!/usr/bin/env python3
"""Cohort retention and customer value (LTV), with CAC when spend data exists.

    python -m src.cohort_cli                                   # UCI Online Retail II (included)
    python -m src.cohort_cli --orders my_orders.csv --spend my_spend.csv

orders CSV: order_id, order_date, customer_id, revenue [, country]   (returns as negative revenue)
spend CSV:  month (YYYY-MM), spend [, channel]

Writes reports/cohorts.xlsx and assets/retention_heatmap.png, ltv_curves.png, ltv_by_country.png.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from . import cohorts, report

VERSION = "1.0.0"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cohort retention, LTV, and CAC from order data.")
    ap.add_argument("--orders", default="data/orders.csv.gz")
    ap.add_argument("--spend", help="Optional marketing spend CSV (month, spend).")
    ap.add_argument("--first-cohort", help="YYYY-MM; default skips the first month of data.")
    ap.add_argument("--last-month", help="YYYY-MM; default is the last complete month.")
    ap.add_argument("--horizon", type=int, default=12, help="Months of value used for the country comparison.")
    ap.add_argument("--min-customers", type=int, default=50, help="Smaller countries are pooled.")
    ap.add_argument("--root", default=".", help="Folder to write reports/ and assets/ into.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = ap.parse_args(argv)

    orders = pd.read_csv(args.orders)
    spend = pd.read_csv(args.spend) if args.spend else None
    window = cohorts.default_window(orders)
    if args.first_cohort:
        window.first_cohort = pd.Timestamp(args.first_cohort + "-01")
    if args.last_month:
        window.last_full_month = pd.Timestamp(args.last_month + "-01")
    horizons = sorted({3, 6, 12, args.horizon})

    con = cohorts.run(orders, spend, window, horizons)
    retention = cohorts.matrix(con, "retention", "retention_rate")
    ltv = cohorts.matrix(con, "cohort_ltv", "ltv_cumulative")
    summary = cohorts.cohort_summary(con, horizons)
    country = cohorts.by_country(con, args.horizon, args.min_customers)
    affordable = cohorts.affordable_cac(summary, horizons=horizons)
    cac = cohorts.cac_tables(con)
    season = cohorts.season_comparison(con, args.horizon)

    root = Path(args.root)
    log_path = Path(args.orders).parent / "cleaning_log.csv"
    report.export_excel(root / "reports" / "cohorts.xlsx", {
        "Cohort_summary": summary, "Retention": retention, "LTV_cumulative": ltv, "By_country": country,
        "Holiday_vs_midyear": season, "Affordable_CAC": affordable, "CAC": cac,
        "Cleaning_log": pd.read_csv(log_path) if log_path.exists() else None,
    })
    (root / "assets").mkdir(parents=True, exist_ok=True)
    report.plot_retention(retention, root / "assets" / "retention_heatmap.png")
    report.plot_ltv_curves(ltv, root / "assets" / "ltv_curves.png")
    if not country.empty and country["country"].nunique() > 1:
        report.plot_country(country, args.horizon, root / "assets" / "ltv_by_country.png")

    print(f"[OK] {window.first_cohort:%Y-%m} to {window.last_full_month:%Y-%m}: "
          f"{int(summary['cohort_size'].sum()):,} customers in {len(summary)} cohorts")
    print(summary.round(3).to_string(index=False))
    if not country.empty:
        print("\n" + country.round(2).to_string(index=False))
    if not season.empty:
        print("\n" + season.round(1).to_string(index=False))
    print("\n" + affordable.round(0).to_string(index=False))
    if cac is not None:
        print("\n" + cac.round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
