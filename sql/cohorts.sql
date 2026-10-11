-- Acquisition cohorts, retention, and customer value (LTV) from one-row-per-order data.
-- Inputs (registered from Python):
--   orders(order_id, order_date DATE, customer_id, country, revenue)   returns = negative revenue
--   params(first_cohort DATE, last_full_month DATE)
--   horizons(h INTEGER)                                                e.g. 3, 6, 12 months
-- Months after last_full_month are ignored, so a half-finished month never looks like churn.

-- Orders inside the analysis window, with their calendar month
CREATE OR REPLACE VIEW orders_in_window AS
SELECT o.*, date_trunc('month', o.order_date) AS order_month
FROM orders o CROSS JOIN params p
WHERE date_trunc('month', o.order_date) <= p.last_full_month;

-- Each customer's first purchase (returns don't count) defines their cohort and country.
-- ROW_NUMBER with order_id as tie-breaker keeps the choice deterministic.
CREATE OR REPLACE TABLE customers AS
SELECT customer_id, date_trunc('month', order_date) AS cohort_month, country
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date, order_id) AS rn
    FROM orders_in_window
    WHERE revenue > 0
)
WHERE rn = 1;

-- Drop cohorts before first_cohort: in the first month of data, "new" customers
-- include people who had already been buying before the data starts.
CREATE OR REPLACE TABLE cohort_customers AS
SELECT c.* FROM customers c CROSS JOIN params p WHERE c.cohort_month >= p.first_cohort;

CREATE OR REPLACE VIEW cohort_size AS
SELECT cohort_month, COUNT(*) AS cohort_size FROM cohort_customers GROUP BY cohort_month;

-- Net revenue and purchases per customer per month since acquisition (month 0 = acquisition month)
CREATE OR REPLACE TABLE customer_months AS
SELECT
    o.customer_id, c.cohort_month, c.country,
    date_diff('month', c.cohort_month, o.order_month)  AS month_index,
    SUM(o.revenue)                                      AS net_revenue,
    COUNT(*) FILTER (WHERE o.revenue > 0)               AS purchases
FROM orders_in_window o
JOIN cohort_customers c USING (customer_id)
WHERE o.order_month >= c.cohort_month   -- returns of orders placed before acquisition are out of scope
GROUP BY ALL;

-- Every (cohort, month) pair that has actually been observed. Later months don't exist yet
-- for young cohorts; leaving them out (instead of filling 0) avoids penalizing new cohorts.
CREATE OR REPLACE VIEW cohort_grid AS
SELECT s.cohort_month, s.cohort_size, t.month_index
FROM cohort_size s CROSS JOIN params p CROSS JOIN range(0, 60) t(month_index)
WHERE s.cohort_month + to_months(CAST(t.month_index AS INTEGER)) <= p.last_full_month;

-- Share of the cohort that bought in each month since acquisition
CREATE OR REPLACE VIEW retention AS
SELECT g.cohort_month, g.month_index, g.cohort_size,
       COALESCE(a.active, 0)                      AS active_customers,
       COALESCE(a.active, 0) * 1.0 / g.cohort_size AS retention_rate
FROM cohort_grid g
LEFT JOIN (
    SELECT cohort_month, month_index, COUNT(*) AS active
    FROM customer_months WHERE purchases > 0
    GROUP BY ALL
) a USING (cohort_month, month_index);

-- Cumulative net revenue per acquired customer (cohort LTV curve)
CREATE OR REPLACE VIEW cohort_ltv AS
SELECT g.cohort_month, g.month_index, g.cohort_size,
       SUM(COALESCE(r.revenue, 0)) OVER (PARTITION BY g.cohort_month ORDER BY g.month_index)
           / g.cohort_size AS ltv_cumulative
FROM cohort_grid g
LEFT JOIN (
    SELECT cohort_month, month_index, SUM(net_revenue) AS revenue FROM customer_months GROUP BY ALL
) r USING (cohort_month, month_index);

-- Customer-level value over the first h months, only for customers whose cohort is old enough
-- to have all h months observed. Used for country comparisons and bootstrap intervals.
CREATE OR REPLACE VIEW customer_ltv AS
SELECT c.customer_id, c.cohort_month, c.country, h.h AS horizon,
       COALESCE(SUM(cm.net_revenue) FILTER (WHERE cm.month_index < h.h), 0) AS ltv
FROM cohort_customers c
CROSS JOIN horizons h
CROSS JOIN params p
LEFT JOIN customer_months cm ON cm.customer_id = c.customer_id
WHERE c.cohort_month + to_months(h.h - 1) <= p.last_full_month
GROUP BY ALL;
