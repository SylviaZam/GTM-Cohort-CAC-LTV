"""Excel workbook and charts for the cohort model."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

README_ROWS = [
    ("Cohort", "Customers grouped by the month of their first purchase."),
    ("Retention", "Share of a cohort that bought again in each month since acquisition (month 0 = 100%)."),
    ("LTV_cumulative", "Net revenue per acquired customer, accumulated month by month. Blank = not observed yet."),
    ("Cohort_summary", "Size, month-1 and month-3 retention, and value after 3, 6, 12 months per cohort."),
    ("By_country", "12-month value per customer by country of first purchase, with a bootstrap 95% interval."),
    ("Holiday_vs_midyear", "12-month value of customers acquired Oct-Dec vs Apr-Sep, with a bootstrap 95% interval."),
    ("Affordable_CAC", "Most you could pay per new customer and break even within 3/6/12 months, at 30-50% gross margin."),
    ("CAC", "Only when a spend file is given: blended CAC and the month LTV first covers it."),
    ("Revenue", "Net of returns, before costs. Multiply by your gross margin for contribution value."),
]


def _autosize(ws, df: pd.DataFrame) -> None:
    for j, col in enumerate(df.columns):
        width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)])
        ws.set_column(j, j, min(max(width + 2, 9), 90))


def export_excel(path: Path, sheets: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheets = {"Readme": pd.DataFrame(README_ROWS, columns=["term", "meaning"]), **sheets}
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            df = df.reset_index() if df.index.name else df
            df.round(4).to_excel(writer, index=False, sheet_name=name)
            _autosize(writer.sheets[name], df)


def plot_retention(ret: pd.DataFrame, path: Path) -> None:
    data = (ret.drop(columns=[0], errors="ignore") * 100).dropna(how="all")
    fig, ax = plt.subplots(figsize=(12, 0.32 * len(data) + 1.6))
    im = ax.imshow(np.ma.masked_invalid(data.to_numpy(dtype=float)), aspect="auto", cmap="Blues", vmin=0, vmax=40)
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            v = data.iat[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=6.5,
                        color="white" if v > 25 else "#1a1a1a")
    ax.set_xticks(range(data.shape[1]), [str(c) for c in data.columns], fontsize=8)
    ax.set_yticks(range(data.shape[0]), data.index, fontsize=8)
    ax.set_xlabel("Months since first purchase")
    ax.set_title("Repeat purchase rate (%) by acquisition cohort")
    fig.colorbar(im, ax=ax, fraction=0.025, label="% of cohort buying that month")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def plot_ltv_curves(ltv: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))
    quarters = pd.PeriodIndex(ltv.index, freq="M").quarter
    years = pd.PeriodIndex(ltv.index, freq="M").year
    for (cohort, row), q, y in zip(ltv.iterrows(), quarters, years):
        holiday = q == 4
        ax.plot(row.index, row.values, color="#c0392b" if holiday else "#2b6cb0",
                alpha=0.9 if holiday else 0.35, lw=2 if holiday else 1.2)
    ax.plot([], [], color="#c0392b", lw=2, label="Acquired Oct-Dec")
    ax.plot([], [], color="#2b6cb0", lw=1.2, alpha=0.6, label="Acquired Jan-Sep")
    ax.set_xlabel("Months since first purchase")
    ax.set_ylabel("Cumulative net revenue per customer (£)")
    ax.set_title("Customer value builds slower for holiday-season cohorts")
    ax.legend(frameon=False)
    ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)


def plot_country(tbl: pd.DataFrame, horizon: int, path: Path) -> None:
    t = tbl.iloc[::-1]
    mean, med = t[f"mean_ltv_{horizon}m"], t[f"median_ltv_{horizon}m"]
    fig, ax = plt.subplots(figsize=(8, 1.4 + 0.7 * len(t)))
    y = np.arange(len(t))
    ax.barh(y, mean, color="#2b6cb0", alpha=.85, label="Mean")
    ax.errorbar(mean, y, xerr=[mean - t["ci_low"], t["ci_high"] - mean], fmt="none", ecolor="#1a1a1a", capsize=5)
    ax.scatter(med, y, marker="D", color="#f39c12", zorder=3, label="Median")
    ax.set_yticks(y, [f"{c}  (n={n:,})" for c, n in zip(t["country"], t["customers"])])
    ax.set_xlabel(f"Net revenue per customer, first {horizon} months (£), 95% bootstrap CI")
    ax.set_title("Customer value by country of first purchase")
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout(); fig.savefig(path, dpi=160); plt.close(fig)
