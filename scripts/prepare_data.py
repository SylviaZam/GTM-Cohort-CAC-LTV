#!/usr/bin/env python3
"""Build data/orders.csv.gz from the raw UCI Online Retail II workbook.

The raw file (~45 MB) is not committed. To rebuild from scratch:

    curl -L -o online_retail_ii.zip "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
    unzip online_retail_ii.zip
    python scripts/prepare_data.py online_retail_II.xlsx

Writes data/orders.csv.gz (one row per invoice) and data/cleaning_log.csv
(rows kept after each cleaning step in sql/00_clean_lines.sql).
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main(xlsx: str) -> None:
    sheets = pd.read_excel(xlsx, sheet_name=None, dtype={"Invoice": str, "StockCode": str, "Description": str})
    raw = pd.concat(sheets.values(), ignore_index=True)
    con = duckdb.connect()
    con.register("raw_lines", raw)
    con.execute((ROOT / "sql" / "00_clean_lines.sql").read_text())

    steps = [("raw lines", "raw_lines"), ("remove exact duplicates", "step1_dedup"),
             ("remove guest checkouts (no customer ID)", "step2_known_customer"),
             ("remove fees, adjustments, tests, vouchers", "step3_merchandise"),
             ("remove zero/negative prices on sales", "step4_valid_price")]
    log = pd.DataFrame([(name, con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]) for name, view in steps],
                       columns=["step", "lines_kept"])
    orders = con.execute("SELECT * FROM orders_clean ORDER BY order_date, order_id").df()
    log.loc[len(log)] = ["invoices after grouping lines", len(orders)]
    print(log.to_string(index=False))

    (ROOT / "data").mkdir(exist_ok=True)
    log.to_csv(ROOT / "data" / "cleaning_log.csv", index=False)
    orders.to_csv(ROOT / "data" / "orders.csv.gz", index=False, compression="gzip")
    print(f"[OK] {len(orders):,} invoices, {orders['customer_id'].nunique():,} customers -> data/orders.csv.gz")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
