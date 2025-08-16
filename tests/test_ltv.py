import pandas as pd
from src.cohort_cli import ltv_tables

def test_ltv_simple():
    # one cohort, two months
    cohort_rev = pd.DataFrame({
        "cohort_month": ["2023-01","2023-01"],
        "months_since_cohort": [0,1],
        "revenue": [100.0, 50.0]
    })
    acq = pd.DataFrame({"cohort_month":["2023-01"], "new_customers":[10]})
    ltv_cum = ltv_tables(cohort_rev, acq)
    # month 0 LTV = 10.0, month 1 cumulative LTV = 15.0
    assert ltv_cum.loc["2023-01",0] == 10.0
    assert ltv_cum.loc["2023-01",1] == 15.0
