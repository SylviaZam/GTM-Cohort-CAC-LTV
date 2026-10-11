-- Optional: acquisition cost, when marketing spend is available.
-- Input: spend(month DATE, spend DOUBLE), summed across channels.
-- Blended CAC = all spend in the cohort month / customers acquired that month.

CREATE OR REPLACE VIEW cac AS
SELECT s.cohort_month, s.cohort_size, COALESCE(sp.spend, 0) AS spend,
       COALESCE(sp.spend, 0) / s.cohort_size AS blended_cac
FROM cohort_size s
LEFT JOIN (SELECT month, SUM(spend) AS spend FROM spend GROUP BY month) sp
       ON sp.month = s.cohort_month;

-- LTV:CAC at each observed month since acquisition, and the first month LTV covers CAC
CREATE OR REPLACE VIEW ltv_to_cac AS
SELECT l.cohort_month, l.month_index, l.ltv_cumulative, c.blended_cac,
       l.ltv_cumulative / NULLIF(c.blended_cac, 0) AS ltv_to_cac
FROM cohort_ltv l JOIN cac c USING (cohort_month);

CREATE OR REPLACE VIEW payback AS
SELECT c.cohort_month, c.blended_cac,
       MIN(l.month_index) FILTER (WHERE l.ltv_cumulative >= c.blended_cac) AS payback_month
FROM cac c JOIN cohort_ltv l USING (cohort_month)
GROUP BY ALL;
