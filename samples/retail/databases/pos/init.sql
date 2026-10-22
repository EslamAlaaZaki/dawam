-- POS: the retail sample's PostgreSQL source system (database `pos`, Database Schema
-- `retail`). Runs once, when the container's volume is empty.
--
-- Every value is computed from a row number, so the data is the same on every machine:
-- no random(), no now(). The data covers calendar year 2025 and is "as of" 2025-12-31.
--
-- Relationships for the demo:
--   declared FKs  products.category_id, receipt_lines.receipt_id
--   undeclared    sales_receipts.store_id -> stores, sales_receipts.customer_id ->
--                 loyalty_customers, receipt_lines.product_id -> products (by name), and
--                 loyalty_customers.home_store_id -> stores (only the view
--                 v_loyalty_overview joins it)
--
-- Shared with the E-commerce sample: loyalty customers (loyalty number, name, email,
-- mobile number: person n is loyalty number LY000000n) and products (SKU).

CREATE SCHEMA retail;
SET search_path = retail;

-- Seed helpers, in pg_temp so they disappear with this session and never reach DAWAM.

CREATE FUNCTION pg_temp.family_name(n int) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT (ARRAY['Al-Otaibi', 'Al-Qahtani', 'Al-Ghamdi', 'Al-Zahrani', 'Al-Dosari',
                  'Al-Harbi', 'Al-Shehri', 'Al-Mutairi', 'Al-Shammari', 'Al-Anazi',
                  'Al-Subaie', 'Al-Malki', 'Al-Rashidi', 'Al-Juhani', 'Al-Omari',
                  'Al-Faifi', 'Al-Yami', 'Al-Hajri', 'Al-Balawi', 'Al-Khaldi'])[1 + (n * 11 + 4) % 20]
$$;

-- Person n's full name: first name, father's name, family name. The E-commerce sample uses
-- the same formula, so a shared customer has the same name in both systems.
CREATE FUNCTION pg_temp.person_name(n int) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT (ARRAY['Mohammed', 'Abdullah', 'Fahad', 'Khalid', 'Saud', 'Faisal', 'Omar',
                  'Turki', 'Nasser', 'Sultan', 'Majed', 'Yousef', 'Noura', 'Sara', 'Reem',
                  'Lama', 'Hessa', 'Maha', 'Abeer', 'Dana', 'Haifa', 'Mashael', 'Ghada',
                  'Amal'])[1 + (n * 7) % 24]
        || ' ' || (ARRAY['Mohammed', 'Abdullah', 'Fahad', 'Khalid', 'Saud', 'Faisal',
                         'Omar', 'Turki', 'Nasser', 'Sultan', 'Majed', 'Yousef'])[1 + (n * 5 + 3) % 12]
        || ' ' || pg_temp.family_name(n)
$$;

-- Reference data -------------------------------------------------------------------------

CREATE TABLE stores (
    store_id   integer PRIMARY KEY,
    store_code varchar(6) NOT NULL UNIQUE,
    store_name varchar(100) NOT NULL,
    city       varchar(50) NOT NULL,
    region     varchar(20) NOT NULL,
    format     varchar(12) NOT NULL,
    opened_on  date NOT NULL,
    closed_on  date,
    is_active  boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE stores IS 'Physical stores of the chain.';

INSERT INTO stores VALUES
    (1, 'RUH01', 'Olaya Mall', 'Riyadh', 'Central', 'hypermarket', '2008-04-01', NULL, true),
    (2, 'RUH02', 'Granada Center', 'Riyadh', 'Central', 'supermarket', '2011-09-15', NULL, true),
    (3, 'RUH03', 'Al Nakheel', 'Riyadh', 'Central', 'supermarket', '2014-02-20', NULL, true),
    (4, 'RUH04', 'Al Malqa Express', 'Riyadh', 'Central', 'express', '2018-06-10', NULL, true),
    (5, 'RUH05', 'Hittin', 'Riyadh', 'Central', 'supermarket', '2020-10-01', NULL, true),
    (6, 'JED01', 'Red Sea Mall', 'Jeddah', 'Western', 'hypermarket', '2009-03-12', NULL, true),
    (7, 'JED02', 'Al Rawdah', 'Jeddah', 'Western', 'supermarket', '2013-11-05', NULL, true),
    (8, 'JED03', 'Obhur Express', 'Jeddah', 'Western', 'express', '2017-05-22', '2025-08-31', false),
    (9, 'DMM01', 'Dammam Corniche', 'Dammam', 'Eastern', 'supermarket', '2012-01-18', NULL, true),
    (10, 'KHB01', 'Al Khobar Road', 'Al Khobar', 'Eastern', 'supermarket', '2025-04-10', NULL, true),
    (11, 'DMM02', 'Al Faisaliyah', 'Dammam', 'Eastern', 'express', '2019-08-14', NULL, true),
    (12, 'JED04', 'Tahlia Street', 'Jeddah', 'Western', 'supermarket', '2015-12-02', NULL, true);

CREATE TABLE categories (
    category_id   integer PRIMARY KEY,
    category_code varchar(3) NOT NULL UNIQUE,
    category_name varchar(50) NOT NULL
);
COMMENT ON TABLE categories IS 'Product categories; the SKU carries the category code.';

INSERT INTO categories VALUES
    (1, 'GRC', 'Grocery'),
    (2, 'BEV', 'Beverages'),
    (3, 'HOU', 'Household'),
    (4, 'PRC', 'Personal care'),
    (5, 'ELC', 'Electronics'),
    (6, 'FSH', 'Fashion'),
    (7, 'HOM', 'Home and kitchen'),
    (8, 'TOY', 'Toys'),
    (9, 'SPT', 'Sports'),
    (10, 'STA', 'Stationery');

-- Master data ----------------------------------------------------------------------------

CREATE TABLE products (
    product_id     integer PRIMARY KEY,
    sku            varchar(12) NOT NULL UNIQUE,
    product_name   varchar(100) NOT NULL,
    category_id    integer NOT NULL REFERENCES categories (category_id),
    list_price_sar numeric(10, 2) NOT NULL,
    unit_cost_sar  numeric(10, 2) NOT NULL,
    is_active      boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE products IS 'The product range, one row per SKU. Prices exclude VAT.';

INSERT INTO products VALUES
    (1, 'SKU-GRC-0001', 'Standard Basmati Rice 5kg', 1, 38.00, 22.80, true),
    (2, 'SKU-GRC-0002', 'Standard Arabic Coffee 500g', 1, 45.00, 29.25, true),
    (3, 'SKU-BEV-0003', 'Standard Mineral Water 24 Pack', 2, 14.00, 9.80, true),
    (4, 'SKU-BEV-0004', 'Standard Orange Juice 1L', 2, 9.50, 4.75, true),
    (5, 'SKU-HOU-0005', 'Standard Laundry Detergent 3L', 3, 32.00, 24.00, true),
    (6, 'SKU-HOU-0006', 'Standard Dish Soap 1L', 3, 11.00, 8.80, true),
    (7, 'SKU-PRC-0007', 'Standard Shampoo 400ml', 4, 21.00, 11.55, true),
    (8, 'SKU-PRC-0008', 'Standard Oud Perfume 50ml', 4, 185.00, 111.00, true),
    (9, 'SKU-ELC-0009', 'Standard Wireless Earbuds', 5, 129.00, 83.85, true),
    (10, 'SKU-ELC-0010', 'Standard Phone Charger 20W', 5, 49.00, 34.30, true),
    (11, 'SKU-FSH-0011', 'Standard Cotton Thobe', 6, 165.00, 82.50, true),
    (12, 'SKU-FSH-0012', 'Standard Classic Abaya', 6, 289.00, 216.75, true),
    (13, 'SKU-HOM-0013', 'Standard Bed Sheet Set', 7, 149.00, 119.20, false),
    (14, 'SKU-HOM-0014', 'Standard Dinner Set 24 Pieces', 7, 219.00, 120.45, true),
    (15, 'SKU-TOY-0015', 'Standard Building Blocks 500 Pieces', 8, 99.00, 59.40, true),
    (16, 'SKU-TOY-0016', 'Standard Remote Control Car', 8, 139.00, 90.35, true),
    (17, 'SKU-SPT-0017', 'Standard Yoga Mat', 9, 59.00, 41.30, true),
    (18, 'SKU-SPT-0018', 'Standard Football Size 5', 9, 45.00, 22.50, true),
    (19, 'SKU-STA-0019', 'Standard School Notebook Pack', 10, 19.00, 14.25, true),
    (20, 'SKU-STA-0020', 'Standard Gel Pen Set', 10, 12.00, 9.60, true),
    (21, 'SKU-GRC-0021', 'Premium Basmati Rice 5kg', 1, 57.00, 31.35, true),
    (22, 'SKU-GRC-0022', 'Premium Arabic Coffee 500g', 1, 67.50, 40.50, true),
    (23, 'SKU-BEV-0023', 'Premium Mineral Water 24 Pack', 2, 21.00, 13.65, true),
    (24, 'SKU-BEV-0024', 'Premium Orange Juice 1L', 2, 14.00, 9.80, true),
    (25, 'SKU-HOU-0025', 'Premium Laundry Detergent 3L', 3, 48.00, 24.00, true),
    (26, 'SKU-HOU-0026', 'Premium Dish Soap 1L', 3, 16.50, 12.38, false),
    (27, 'SKU-PRC-0027', 'Premium Shampoo 400ml', 4, 31.50, 25.20, true),
    (28, 'SKU-PRC-0028', 'Premium Oud Perfume 50ml', 4, 277.50, 152.62, true),
    (29, 'SKU-ELC-0029', 'Premium Wireless Earbuds', 5, 193.50, 116.10, true),
    (30, 'SKU-ELC-0030', 'Premium Phone Charger 20W', 5, 73.50, 47.77, true),
    (31, 'SKU-FSH-0031', 'Premium Cotton Thobe', 6, 247.50, 173.25, true),
    (32, 'SKU-FSH-0032', 'Premium Classic Abaya', 6, 433.50, 216.75, true),
    (33, 'SKU-HOM-0033', 'Premium Bed Sheet Set', 7, 223.50, 167.62, true),
    (34, 'SKU-HOM-0034', 'Premium Dinner Set 24 Pieces', 7, 328.50, 262.80, true),
    (35, 'SKU-TOY-0035', 'Premium Building Blocks 500 Pieces', 8, 148.50, 81.68, true),
    (36, 'SKU-TOY-0036', 'Premium Remote Control Car', 8, 208.50, 125.10, true),
    (37, 'SKU-SPT-0037', 'Premium Yoga Mat', 9, 88.50, 57.52, true),
    (38, 'SKU-SPT-0038', 'Premium Football Size 5', 9, 67.50, 47.25, true),
    (39, 'SKU-STA-0039', 'Premium School Notebook Pack', 10, 28.50, 14.25, false),
    (40, 'SKU-STA-0040', 'Premium Gel Pen Set', 10, 18.00, 13.50, true),
    (41, 'SKU-GRC-0041', 'Economy Basmati Rice 5kg', 1, 30.50, 24.40, true),
    (42, 'SKU-GRC-0042', 'Economy Arabic Coffee 500g', 1, 36.00, 19.80, true),
    (43, 'SKU-BEV-0043', 'Economy Mineral Water 24 Pack', 2, 11.00, 6.60, true),
    (44, 'SKU-BEV-0044', 'Economy Orange Juice 1L', 2, 7.50, 4.88, true),
    (45, 'SKU-HOU-0045', 'Economy Laundry Detergent 3L', 3, 25.50, 17.85, true),
    (46, 'SKU-HOU-0046', 'Economy Dish Soap 1L', 3, 9.00, 4.50, true),
    (47, 'SKU-PRC-0047', 'Economy Shampoo 400ml', 4, 17.00, 12.75, true),
    (48, 'SKU-PRC-0048', 'Economy Oud Perfume 50ml', 4, 148.00, 118.40, true),
    (49, 'SKU-ELC-0049', 'Economy Wireless Earbuds', 5, 103.00, 56.65, true),
    (50, 'SKU-ELC-0050', 'Economy Phone Charger 20W', 5, 39.00, 23.40, true),
    (51, 'SKU-FSH-0051', 'Economy Cotton Thobe', 6, 132.00, 85.80, true),
    (52, 'SKU-FSH-0052', 'Economy Classic Abaya', 6, 231.00, 161.70, false),
    (53, 'SKU-HOM-0053', 'Economy Bed Sheet Set', 7, 119.00, 59.50, true),
    (54, 'SKU-HOM-0054', 'Economy Dinner Set 24 Pieces', 7, 175.00, 131.25, true),
    (55, 'SKU-TOY-0055', 'Economy Building Blocks 500 Pieces', 8, 79.00, 63.20, true),
    (56, 'SKU-TOY-0056', 'Economy Remote Control Car', 8, 111.00, 61.05, true),
    (57, 'SKU-SPT-0057', 'Economy Yoga Mat', 9, 47.00, 28.20, true),
    (58, 'SKU-SPT-0058', 'Economy Football Size 5', 9, 36.00, 23.40, true),
    (59, 'SKU-STA-0059', 'Economy School Notebook Pack', 10, 15.00, 10.50, true),
    (60, 'SKU-STA-0060', 'Economy Gel Pen Set', 10, 9.50, 4.75, true),
    (61, 'SKU-GRC-0061', 'Deluxe Basmati Rice 5kg', 1, 76.00, 57.00, true),
    (62, 'SKU-GRC-0062', 'Deluxe Arabic Coffee 500g', 1, 90.00, 72.00, true),
    (63, 'SKU-BEV-0063', 'Deluxe Mineral Water 24 Pack', 2, 28.00, 15.40, true),
    (64, 'SKU-BEV-0064', 'Deluxe Orange Juice 1L', 2, 19.00, 11.40, true),
    (65, 'SKU-HOU-0065', 'Deluxe Laundry Detergent 3L', 3, 64.00, 41.60, false),
    (66, 'SKU-HOU-0066', 'Deluxe Dish Soap 1L', 3, 22.00, 15.40, true),
    (67, 'SKU-PRC-0067', 'Deluxe Shampoo 400ml', 4, 42.00, 21.00, true),
    (68, 'SKU-PRC-0068', 'Deluxe Oud Perfume 50ml', 4, 370.00, 277.50, true),
    (69, 'SKU-ELC-0069', 'Deluxe Wireless Earbuds', 5, 258.00, 206.40, true),
    (70, 'SKU-ELC-0070', 'Deluxe Phone Charger 20W', 5, 98.00, 53.90, true),
    (71, 'SKU-FSH-0071', 'Deluxe Cotton Thobe', 6, 330.00, 198.00, true),
    (72, 'SKU-FSH-0072', 'Deluxe Classic Abaya', 6, 578.00, 375.70, true),
    (73, 'SKU-HOM-0073', 'Deluxe Bed Sheet Set', 7, 298.00, 208.60, true),
    (74, 'SKU-HOM-0074', 'Deluxe Dinner Set 24 Pieces', 7, 438.00, 219.00, true),
    (75, 'SKU-TOY-0075', 'Deluxe Building Blocks 500 Pieces', 8, 198.00, 148.50, true),
    (76, 'SKU-TOY-0076', 'Deluxe Remote Control Car', 8, 278.00, 222.40, true),
    (77, 'SKU-SPT-0077', 'Deluxe Yoga Mat', 9, 118.00, 64.90, true),
    (78, 'SKU-SPT-0078', 'Deluxe Football Size 5', 9, 90.00, 54.00, false),
    (79, 'SKU-STA-0079', 'Deluxe School Notebook Pack', 10, 38.00, 24.70, true),
    (80, 'SKU-STA-0080', 'Deluxe Gel Pen Set', 10, 24.00, 16.80, true);

CREATE TABLE loyalty_customers (
    customer_id       integer PRIMARY KEY,
    loyalty_no        varchar(9) NOT NULL UNIQUE,
    full_name         varchar(150) NOT NULL,
    mobile_number     varchar(13) NOT NULL,
    email             varchar(150),
    date_of_birth     date NOT NULL,
    gender            char(1) NOT NULL,
    city              varchar(50) NOT NULL,
    home_store_id     integer NOT NULL,
    tier              varchar(10) NOT NULL,
    marketing_consent boolean NOT NULL,
    enrolled_on       date NOT NULL
);
COMMENT ON TABLE loyalty_customers IS 'Members of the loyalty programme (one row per person).';

-- Customers 1-1000, enrolled between 2022-01-01 and 2025-06-30 at their home store (the
-- Al Khobar Road store opened in April 2025 and has no home customers). Even customers
-- also shop online; odd ones may have no email.
INSERT INTO loyalty_customers
SELECT n,
       'LY' || lpad(n::text, 7, '0'),
       pg_temp.person_name(n),
       '+9665' || lpad(((n * 7919 + 1234567) % 100000000)::text, 8, '0'),
       CASE WHEN n % 9 = 0 AND n % 2 = 1 THEN NULL
            ELSE lower(split_part(pg_temp.person_name(n), ' ', 1)) || '.'
                 || lower(replace(pg_temp.family_name(n), 'Al-', '')) || n || '@example.com'
       END,
       date '1955-01-01' + (n * 3571) % 17000,
       CASE WHEN (n * 7) % 24 < 12 THEN 'M' ELSE 'F' END,
       s.city,
       s.store_id,
       CASE WHEN n % 20 = 0 THEN 'gold' WHEN n % 5 = 0 THEN 'silver' ELSE 'bronze' END,
       n % 7 <> 0,
       date '2022-01-01' + (n * 97) % 1277
FROM generate_series(1, 1000) AS n
JOIN stores s ON s.store_id = (ARRAY[1, 1, 2, 2, 3, 4, 5, 6, 6, 7, 8, 9, 9, 11, 12])[1 + n % 15];

-- Transactional data ---------------------------------------------------------------------

CREATE TABLE sales_receipts (
    receipt_id     integer PRIMARY KEY,
    receipt_no     varchar(10) NOT NULL UNIQUE,
    store_id       integer NOT NULL,
    customer_id    integer,
    receipt_type   varchar(6) NOT NULL CHECK (receipt_type IN ('sale', 'return')),
    sold_at        timestamp NOT NULL,
    payment_method varchar(10) NOT NULL,
    cashier_code   varchar(4) NOT NULL,
    subtotal_sar   numeric(12, 2) NOT NULL DEFAULT 0,
    vat_sar        numeric(12, 2) NOT NULL DEFAULT 0,
    total_sar      numeric(12, 2) NOT NULL DEFAULT 0
);
COMMENT ON TABLE sales_receipts IS 'Till receipts of 2025: sales and returns. Amounts of a return are negative.';
CREATE INDEX sales_receipts_store_sold_idx ON sales_receipts (store_id, sold_at);

-- 1500 receipts in 2025. A receipt is never before its store opened or after it closed,
-- nor before its customer enrolled. 40% carry a loyalty customer; every 25th is a return.
INSERT INTO sales_receipts (receipt_id, receipt_no, store_id, customer_id, receipt_type,
                            sold_at, payment_method, cashier_code)
SELECT t,
       'RC' || lpad(t::text, 8, '0'),
       s.store_id,
       c.customer_id,
       CASE WHEN t % 25 = 0 THEN 'return' ELSE 'sale' END,
       d.day + make_interval(secs => 32400 + (t * 7919) % 50400),
       (ARRAY['cash', 'cash', 'mada', 'mada', 'mada', 'mada', 'visa', 'visa', 'apple_pay',
              'stc_pay'])[1 + (t * 3) % 10],
       'C' || lpad((1 + t % 40)::text, 3, '0')
FROM generate_series(1, 1500) AS t
JOIN stores s ON s.store_id = (ARRAY[1, 1, 1, 2, 2, 3, 3, 4, 5, 6, 6, 6, 7, 7, 8, 9, 9, 10, 10,
                                     11, 12, 12, 2, 6])[1 + t % 24]
LEFT JOIN loyalty_customers c ON t % 5 < 2 AND c.customer_id = 1 + (t * 7919) % 1000
CROSS JOIN LATERAL (
    SELECT greatest(
               greatest(s.opened_on, date '2025-01-01')
                   + (t * 7) % (least(coalesce(s.closed_on, date '2025-12-31'), date '2025-12-31')
                                - greatest(s.opened_on, date '2025-01-01') + 1),
               coalesce(c.enrolled_on, date '2025-01-01')) AS day
) AS d;

CREATE TABLE receipt_lines (
    line_id        integer PRIMARY KEY,
    receipt_id     integer NOT NULL REFERENCES sales_receipts (receipt_id),
    line_no        smallint NOT NULL,
    product_id     integer NOT NULL,
    quantity       integer NOT NULL,
    unit_price_sar numeric(10, 2) NOT NULL,
    discount_sar   numeric(10, 2) NOT NULL,
    line_net_sar   numeric(12, 2) NOT NULL,
    vat_sar        numeric(12, 2) NOT NULL
);
COMMENT ON TABLE receipt_lines IS 'The products on a receipt. Quantity and amounts are negative on a return.';
CREATE INDEX receipt_lines_receipt_idx ON receipt_lines (receipt_id);

-- 3000 lines: each receipt gets one, and the other 1500 go to the first 1100 receipts (so
-- some have three). Only products that are still listed are sold. Net = quantity x price -
-- discount; VAT is 15%.
INSERT INTO receipt_lines
SELECT x.t,
       x.receipt_id,
       row_number() OVER (PARTITION BY x.receipt_id ORDER BY x.t),
       x.product_id,
       x.qty,
       x.price,
       x.disc,
       x.qty * x.price - x.disc,
       round((x.qty * x.price - x.disc) * 0.15, 2)
FROM (
    SELECT t,
           r.receipt_id,
           p.product_id,
           p.list_price_sar AS price,
           (1 + t % 4) * CASE WHEN r.receipt_type = 'return' THEN -1 ELSE 1 END AS qty,
           CASE WHEN t % 7 = 0
                THEN round((1 + t % 4) * CASE WHEN r.receipt_type = 'return' THEN -1 ELSE 1 END
                           * p.list_price_sar * 0.10, 2)
                ELSE 0
           END AS disc
    FROM generate_series(1, 3000) AS t
    JOIN sales_receipts r ON r.receipt_id = CASE WHEN t <= 1500 THEN t ELSE 1 + (t * 7919) % 1100 END
    JOIN (
        SELECT product_id, list_price_sar,
               row_number() OVER (ORDER BY product_id) AS rn, count(*) OVER () AS total
        FROM products
        WHERE is_active
    ) AS p ON p.rn = 1 + (t * 7919) % p.total
) AS x;

UPDATE sales_receipts r
SET subtotal_sar = x.net, vat_sar = x.vat, total_sar = x.net + x.vat
FROM (SELECT receipt_id, sum(line_net_sar) AS net, sum(vat_sar) AS vat
      FROM receipt_lines GROUP BY receipt_id) AS x
WHERE x.receipt_id = r.receipt_id;

-- A view and a function whose JOINs show relationships no FK declares ---------------------

CREATE VIEW v_loyalty_overview AS
SELECT c.loyalty_no,
       c.tier,
       s.store_code AS home_store_code,
       s.city,
       count(r.receipt_id) AS purchases,
       coalesce(sum(r.subtotal_sar), 0) AS net_spend_sar
FROM retail.loyalty_customers c
JOIN retail.stores s ON s.store_id = c.home_store_id
LEFT JOIN retail.sales_receipts r ON r.customer_id = c.customer_id AND r.receipt_type = 'sale'
GROUP BY c.loyalty_no, c.tier, s.store_code, s.city;

CREATE FUNCTION fn_store_net_sales(p_store_code varchar, p_from date, p_to date)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(l.line_net_sar), 0)
    FROM retail.receipt_lines l
    JOIN retail.sales_receipts r ON r.receipt_id = l.receipt_id
    JOIN retail.stores s ON s.store_id = r.store_id
    WHERE s.store_code = p_store_code
      AND r.sold_at::date BETWEEN p_from AND p_to;
$$;
COMMENT ON FUNCTION fn_store_net_sales IS 'Net sales (sales minus returns, excluding VAT) of a store in a date range.';

ANALYZE;

-- The read-only user DAWAM connects as -----------------------------------------------------

CREATE ROLE dawam_reader LOGIN PASSWORD 'dawam_reader';
ALTER ROLE dawam_reader SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE pos TO dawam_reader;
GRANT USAGE ON SCHEMA retail TO dawam_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA retail TO dawam_reader;
-- Function definitions are readable through pg_proc without further grants.
