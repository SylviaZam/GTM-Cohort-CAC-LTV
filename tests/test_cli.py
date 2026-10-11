"""Command line, end to end."""
from pathlib import Path

import pandas as pd

from src import cohort_cli

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


def test_real_data_run_writes_all_outputs(tmp_path, capsys):
    assert cohort_cli.main(["--orders", str(ROOT / "data/orders.csv.gz"), "--root", str(tmp_path)]) == 0
    for rel in ["reports/cohorts.xlsx", "assets/retention_heatmap.png", "assets/ltv_curves.png",
                "assets/ltv_by_country.png"]:
        assert (tmp_path / rel).stat().st_size > 0, rel
    sheets = pd.ExcelFile(tmp_path / "reports/cohorts.xlsx").sheet_names
    assert {"Readme", "Cohort_summary", "Retention", "LTV_cumulative", "By_country",
            "Holiday_vs_midyear", "Affordable_CAC", "Cleaning_log"} <= set(sheets)
    assert "2010-01 to 2011-11" in capsys.readouterr().out


def test_spend_file_adds_cac(tmp_path):
    code = cohort_cli.main(["--orders", str(FIX / "orders_sample.csv"), "--spend", str(FIX / "marketing_spend.csv"),
                            "--first-cohort", "2023-01", "--last-month", "2023-05", "--root", str(tmp_path)])
    assert code == 0
    cac = pd.read_excel(tmp_path / "reports/cohorts.xlsx", sheet_name="CAC")
    assert cac.loc[cac["cohort_month"] == "2023-01", "blended_cac"].iloc[0] == 800.0   # 1,600 spend / 2 customers
