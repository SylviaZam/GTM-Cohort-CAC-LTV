-- Clean the raw UCI Online Retail II lines into one row per invoice.
-- Input view: raw_lines (Invoice, StockCode, Description, Quantity, InvoiceDate, Price, "Customer ID", Country).
-- Each step is a view so scripts/prepare_data.py can log how many rows it removes.

-- 1) Exact duplicate lines (the two yearly sheets overlap 1-9 Dec 2010, and some lines repeat)
CREATE OR REPLACE VIEW step1_dedup AS
SELECT DISTINCT * FROM raw_lines;

-- 2) Guest checkouts: no customer ID, so they can't belong to a cohort
CREATE OR REPLACE VIEW step2_known_customer AS
SELECT * FROM step1_dedup WHERE "Customer ID" IS NOT NULL;

-- 3) Non-merchandise lines: fees, manual adjustments, tests, samples, gift vouchers, charity.
--    Postage (POST, DOT, C2) and discounts (D) stay: customers pay or receive them.
CREATE OR REPLACE VIEW step3_merchandise AS
SELECT * FROM step2_known_customer
WHERE upper(StockCode) NOT IN ('AMAZONFEE', 'BANK CHARGES', 'ADJUST', 'ADJUST2', 'B', 'M', 'S', 'CRUK',
                               'TEST001', 'TEST002')
  AND upper(StockCode) NOT LIKE 'GIFT\_%' ESCAPE '\';

-- 4) Zero or negative prices on normal invoices are data errors; cancellations ("C" invoices) stay as returns
CREATE OR REPLACE VIEW step4_valid_price AS
SELECT * FROM step3_merchandise
WHERE Price > 0 OR Invoice LIKE 'C%';

-- 5) One row per invoice; cancellations carry negative revenue and net against the customer's value
CREATE OR REPLACE VIEW orders_clean AS
SELECT
    Invoice                                   AS order_id,
    CAST(MIN(InvoiceDate) AS DATE)            AS order_date,
    CAST("Customer ID" AS INTEGER)            AS customer_id,
    Country                                   AS country,
    ROUND(SUM(Quantity * Price), 2)           AS revenue,
    Invoice LIKE 'C%'                         AS is_return
FROM step4_valid_price
GROUP BY Invoice, "Customer ID", Country;
