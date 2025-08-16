-- =========================================================
-- GTM Cohort CAC/LTV — DuckDB SQL snapshot
-- These are the core queries executed by src/cohort_cli.py :))
-- =========================================================

-- 1) Clean fact table from registered 'orders' (pandas DataFrame)
--    Note: in the CLI, 'orders' already has an 'order_month' column.
CREATE OR REPLACE TABLE fact_orders AS
SELECT
  order_id,
  order_date,
  order_month,          -- e.g., '2023-04'
  customer_id,
  channel,
  revenue
FROM orders;

-- 2) First order per customer (defines acquisition/cohort month)
CREATE OR REPLACE TABLE first_orders AS
SELECT
  customer_id,
  MIN(order_date)                                        AS first_order_date,
  strftime(MIN(order_date), '%Y-%m')                     AS cohort_month,
  ANY_VALUE(channel)                                     AS acquisition_channel
FROM fact_orders
GROUP BY customer_id;

-- 3) Join back to compute months since cohort for each order
CREATE OR REPLACE TABLE fct AS
SELECT
  f.*,
  fo.cohort_month,
  fo.acquisition_channel,
  date_diff('month',
            date_trunc('month', fo.first_order_date),
            date_trunc('month', f.order_date))          AS months_since_cohort
FROM fact_orders f
JOIN first_orders fo USING (customer_id);

-- 4) Cohort revenue by months_since_cohort  (cohort_rev in Python)
--    Used to build LTV per cohort.
CREATE OR REPLACE VIEW cohort_revenue AS
SELECT
  cohort_month,
  months_since_cohort,
  SUM(revenue) AS revenue
FROM fct
GROUP BY 1, 2
ORDER BY 1, 2;

-- 5) New customer counts per cohort  (acq_counts in Python)
CREATE OR REPLACE VIEW acquisition_counts AS
SELECT
  cohort_month,
  COUNT(DISTINCT customer_id) AS new_customers
FROM first_orders
GROUP BY 1
ORDER BY 1;

-- =========================================================
-- OPTIONAL: All-SQL helpers that mirror the Python post-processing
-- (In the CLI, CAC aggregation and LTV division/cumulation are done
--  in pandas; these views let reviewers see the same logic in SQL.)
-- =========================================================

-- A) CAC by cohort month using total spend that month
--    (the CLI merges spend sums in pandas; this is the pure SQL form)
--    Requires 'spend' to be registered (month, channel, spend).
CREATE OR REPLACE VIEW cac_by_cohort AS
WITH spend_by_month AS (
  SELECT
    month::VARCHAR AS cohort_month,
    SUM(spend)     AS spend_total
  FROM spend
  GROUP BY 1
)
SELECT
  a.cohort_month,
  COALESCE(s.spend_total, 0)                            AS spend_total,
  a.new_customers,
  CASE WHEN a.new_customers = 0 THEN NULL
       ELSE s.spend_total / a.new_customers END         AS CAC
FROM acquisition_counts a
LEFT JOIN spend_by_month s USING (cohort_month)
ORDER BY 1;

-- B) Per-period LTV (revenue per new customer) before cumulation
CREATE OR REPLACE VIEW ltv_by_cohort AS
SELECT
  r.cohort_month,
  r.months_since_cohort,
  CASE WHEN a.new_customers = 0 THEN NULL
       ELSE r.revenue * 1.0 / a.new_customers END       AS ltv
FROM cohort_revenue r
JOIN acquisition_counts a USING (cohort_month)
ORDER BY 1, 2;

-- C) Latest LTV vs CAC summary (non-cumulative display of most recent LTV)
--    Note: your Excel uses *cumulative* LTV across months; here we show how to
--    surface the latest available (non-cumulative) LTV in pure SQL for a quick view.
WITH last_period AS (
  SELECT cohort_month, MAX(months_since_cohort) AS m
  FROM ltv_by_cohort
  GROUP BY 1
)
CREATE OR REPLACE VIEW ltv_latest_vs_cac AS
SELECT
  l.cohort_month,
  l.ltv                                              AS ltv_latest_non_cumulative,
  c.CAC,
  CASE WHEN c.CAC IS NULL OR c.CAC = 0 THEN NULL
       ELSE l.ltv / c.CAC END                        AS LTV_to_CAC_non_cum
FROM ltv_by_cohort l
JOIN last_period p USING (cohort_month, months_since_cohort)
LEFT JOIN cac_by_cohort c USING (cohort_month)
ORDER BY 1;

-- Tip: your Python pipeline computes *cumulative* LTV via a pivot + cumsum.
-- If you want a cumulative view in SQL, you can roll it up with a window:
-- (this yields cumulative LTV per cohort and month, analogous to your Excel)
CREATE OR REPLACE VIEW ltv_cumulative_sql AS
SELECT
  cohort_month,
  months_since_cohort,
  SUM(ltv) OVER (
    PARTITION BY cohort_month
    ORDER BY months_since_cohort
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  ) AS ltv_cumulative
FROM ltv_by_cohort
ORDER BY 1, 2;

-- From here, you could reproduce the Python 'Summary' in SQL by selecting the
-- latest ltv_cumulative per cohort and joining to CAC:
WITH last_cum AS (
  SELECT cohort_month, MAX(months_since_cohort) AS m
  FROM ltv_cumulative_sql
  GROUP BY 1
)
CREATE OR REPLACE VIEW summary_cumulative_sql AS
SELECT
  l.cohort_month,
  l.ltv_cumulative                               AS LTV_latest_cumulative,
  c.CAC,
  CASE WHEN c.CAC IS NULL OR c.CAC = 0 THEN NULL
       ELSE l.ltv_cumulative / c.CAC END         AS LTV_to_CAC
FROM ltv_cumulative_sql l
JOIN last_cum p USING (cohort_month, months_since_cohort)
LEFT JOIN cac_by_cohort c USING (cohort_month)
ORDER BY 1;
