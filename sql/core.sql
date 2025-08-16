-- =========================================================
-- GTM Cohort CAC/LTV — DuckDB SQL snapshot (explicit joins)
-- =========================================================

-- 1) Clean fact table (derive order_month here)
CREATE OR REPLACE TABLE fact_orders AS
SELECT
  order_id,
  order_date,
  strftime(order_date, '%Y-%m') AS order_month,
  customer_id,
  channel,
  revenue
FROM orders;

-- 2) First order per customer (defines cohort month)
CREATE OR REPLACE TABLE first_orders AS
SELECT
  customer_id,
  MIN(order_date)                             AS first_order_date,
  strftime(MIN(order_date), '%Y-%m')          AS cohort_month,
  any_value(channel)                          AS acquisition_channel
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
            date_trunc('month', f.order_date)) AS months_since_cohort
FROM fact_orders f
JOIN first_orders fo
  ON f.customer_id = fo.customer_id;

-- 4) Cohort revenue by months_since_cohort
CREATE OR REPLACE VIEW cohort_revenue AS
SELECT
  cohort_month,
  months_since_cohort,
  SUM(revenue) AS revenue
FROM fct
GROUP BY 1, 2
ORDER BY 1, 2;

-- 5) New customer counts per cohort
CREATE OR REPLACE VIEW acquisition_counts AS
SELECT
  cohort_month,
  COUNT(DISTINCT customer_id) AS new_customers
FROM first_orders
GROUP BY 1
ORDER BY 1;

-- A) CAC by cohort month using total spend that month
CREATE OR REPLACE VIEW cac_by_cohort AS
WITH spend_by_month AS (
  SELECT
    CAST(month AS VARCHAR) AS cohort_month,
    SUM(spend)             AS spend_total
  FROM spend
  GROUP BY 1
)
SELECT
  a.cohort_month AS cohort_month,
  COALESCE(s.spend_total, 0)                      AS spend_total,
  a.new_customers,
  CASE WHEN a.new_customers = 0 THEN NULL
       ELSE s.spend_total / a.new_customers END   AS CAC
FROM acquisition_counts a
LEFT JOIN spend_by_month s
  ON s.cohort_month = a.cohort_month
ORDER BY cohort_month;

-- B) Per-period LTV (before cumulation)
CREATE OR REPLACE VIEW ltv_by_cohort AS
SELECT
  r.cohort_month AS cohort_month,
  r.months_since_cohort,
  CASE WHEN a.new_customers = 0 THEN NULL
       ELSE r.revenue * 1.0 / a.new_customers END AS ltv
FROM cohort_revenue r
JOIN acquisition_counts a
  ON a.cohort_month = r.cohort_month
ORDER BY cohort_month, months_since_cohort;

-- C) Latest *non-cumulative* LTV vs CAC (for inspection)
CREATE OR REPLACE VIEW ltv_latest_vs_cac AS
WITH last_period AS (
  SELECT cohort_month, MAX(months_since_cohort) AS m
  FROM ltv_by_cohort
  GROUP BY cohort_month
)
SELECT
  l.cohort_month AS cohort_month,
  l.ltv          AS ltv_latest_non_cumulative,
  c.CAC,
  CASE WHEN c.CAC IS NULL OR c.CAC = 0 THEN NULL
       ELSE l.ltv / c.CAC END                   AS LTV_to_CAC_non_cum
FROM ltv_by_cohort l
JOIN last_period p
  ON l.cohort_month = p.cohort_month
 AND l.months_since_cohort = p.m
LEFT JOIN cac_by_cohort c
  ON c.cohort_month = l.cohort_month
ORDER BY cohort_month;

-- D) Cumulative LTV view (matches Excel logic)
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
ORDER BY cohort_month, months_since_cohort;

-- E) Summary using latest cumulative LTV vs CAC
CREATE OR REPLACE VIEW summary_cumulative_sql AS
WITH last_cum AS (
  SELECT cohort_month, MAX(months_since_cohort) AS m
  FROM ltv_cumulative_sql
  GROUP BY cohort_month
)
SELECT
  l.cohort_month                AS cohort_month,
  l.ltv_cumulative              AS LTV_latest_cumulative,
  c.CAC,
  CASE WHEN c.CAC IS NULL OR c.CAC = 0 THEN NULL
       ELSE l.ltv_cumulative / c.CAC END        AS LTV_to_CAC
FROM ltv_cumulative_sql l
JOIN last_cum p
  ON l.cohort_month = p.cohort_month
 AND l.months_since_cohort = p.m
LEFT JOIN cac_by_cohort c
  ON c.cohort_month = l.cohort_month
ORDER BY cohort_month;
