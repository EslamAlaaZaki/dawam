-- E-commerce: the retail sample's MySQL source system (database `shop`, which is also its
-- only Database Schema). Runs once, when the container's volume is empty.
--
-- Every value is computed from a row number, so the data is the same on every machine:
-- no RAND(), no NOW(). The data covers calendar year 2025 and is "as of" 2025-12-31.
-- A customer who is also a loyalty member carries the POS loyalty number and has the same
-- name, mobile number and email as in POS (person p is loyalty number LY000000p). Products
-- are not a table here: order items carry the SKU and name of a product of the POS range.
--
-- Relationships for the demo:
--   declared FKs  orders.customer_id, order_items.order_id
--   undeclared    payments.order_id -> orders, shipments.order_id -> orders (by name, and
--                 the view v_orders_to_ship joins payments), shipments.carrier_code ->
--                 carriers (only the procedure sp_carrier_performance joins it)

USE shop;
SET SESSION cte_max_recursion_depth = 10000;

-- Seed helpers, dropped at the end so they never reach DAWAM.

-- 1 .. 3000, to generate rows from.
CREATE TEMPORARY TABLE seq (n INT PRIMARY KEY);
INSERT INTO seq
WITH RECURSIVE s (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 3000)
SELECT n FROM s;

-- The POS products sold online (active ones, three of every four), by SKU.
CREATE TEMPORARY TABLE seed_catalog (
    sku   VARCHAR(12) NOT NULL,
    name  VARCHAR(100) NOT NULL,
    price DECIMAL(10, 2) NOT NULL
);
INSERT INTO seed_catalog VALUES
    ('SKU-GRC-0001', 'Standard Basmati Rice 5kg', 38.00),
    ('SKU-GRC-0002', 'Standard Arabic Coffee 500g', 45.00),
    ('SKU-BEV-0003', 'Standard Mineral Water 24 Pack', 14.00),
    ('SKU-HOU-0005', 'Standard Laundry Detergent 3L', 32.00),
    ('SKU-HOU-0006', 'Standard Dish Soap 1L', 11.00),
    ('SKU-PRC-0007', 'Standard Shampoo 400ml', 21.00),
    ('SKU-ELC-0009', 'Standard Wireless Earbuds', 129.00),
    ('SKU-ELC-0010', 'Standard Phone Charger 20W', 49.00),
    ('SKU-FSH-0011', 'Standard Cotton Thobe', 165.00),
    ('SKU-HOM-0014', 'Standard Dinner Set 24 Pieces', 219.00),
    ('SKU-TOY-0015', 'Standard Building Blocks 500 Pieces', 99.00),
    ('SKU-SPT-0017', 'Standard Yoga Mat', 59.00),
    ('SKU-SPT-0018', 'Standard Football Size 5', 45.00),
    ('SKU-STA-0019', 'Standard School Notebook Pack', 19.00),
    ('SKU-GRC-0021', 'Premium Basmati Rice 5kg', 57.00),
    ('SKU-GRC-0022', 'Premium Arabic Coffee 500g', 67.50),
    ('SKU-BEV-0023', 'Premium Mineral Water 24 Pack', 21.00),
    ('SKU-HOU-0025', 'Premium Laundry Detergent 3L', 48.00),
    ('SKU-PRC-0027', 'Premium Shampoo 400ml', 31.50),
    ('SKU-ELC-0029', 'Premium Wireless Earbuds', 193.50),
    ('SKU-ELC-0030', 'Premium Phone Charger 20W', 73.50),
    ('SKU-FSH-0031', 'Premium Cotton Thobe', 247.50),
    ('SKU-HOM-0033', 'Premium Bed Sheet Set', 223.50),
    ('SKU-HOM-0034', 'Premium Dinner Set 24 Pieces', 328.50),
    ('SKU-TOY-0035', 'Premium Building Blocks 500 Pieces', 148.50),
    ('SKU-SPT-0037', 'Premium Yoga Mat', 88.50),
    ('SKU-SPT-0038', 'Premium Football Size 5', 67.50),
    ('SKU-GRC-0041', 'Economy Basmati Rice 5kg', 30.50),
    ('SKU-GRC-0042', 'Economy Arabic Coffee 500g', 36.00),
    ('SKU-BEV-0043', 'Economy Mineral Water 24 Pack', 11.00),
    ('SKU-HOU-0045', 'Economy Laundry Detergent 3L', 25.50),
    ('SKU-HOU-0046', 'Economy Dish Soap 1L', 9.00),
    ('SKU-PRC-0047', 'Economy Shampoo 400ml', 17.00),
    ('SKU-ELC-0049', 'Economy Wireless Earbuds', 103.00),
    ('SKU-ELC-0050', 'Economy Phone Charger 20W', 39.00),
    ('SKU-FSH-0051', 'Economy Cotton Thobe', 132.00),
    ('SKU-HOM-0053', 'Economy Bed Sheet Set', 119.00),
    ('SKU-HOM-0054', 'Economy Dinner Set 24 Pieces', 175.00),
    ('SKU-TOY-0055', 'Economy Building Blocks 500 Pieces', 79.00),
    ('SKU-SPT-0057', 'Economy Yoga Mat', 47.00),
    ('SKU-SPT-0058', 'Economy Football Size 5', 36.00),
    ('SKU-STA-0059', 'Economy School Notebook Pack', 15.00),
    ('SKU-GRC-0061', 'Deluxe Basmati Rice 5kg', 76.00),
    ('SKU-GRC-0062', 'Deluxe Arabic Coffee 500g', 90.00),
    ('SKU-BEV-0063', 'Deluxe Mineral Water 24 Pack', 28.00),
    ('SKU-HOU-0066', 'Deluxe Dish Soap 1L', 22.00),
    ('SKU-PRC-0067', 'Deluxe Shampoo 400ml', 42.00),
    ('SKU-ELC-0069', 'Deluxe Wireless Earbuds', 258.00),
    ('SKU-ELC-0070', 'Deluxe Phone Charger 20W', 98.00),
    ('SKU-FSH-0071', 'Deluxe Cotton Thobe', 330.00),
    ('SKU-HOM-0073', 'Deluxe Bed Sheet Set', 298.00),
    ('SKU-HOM-0074', 'Deluxe Dinner Set 24 Pieces', 438.00),
    ('SKU-TOY-0075', 'Deluxe Building Blocks 500 Pieces', 198.00),
    ('SKU-SPT-0077', 'Deluxe Yoga Mat', 118.00),
    ('SKU-STA-0079', 'Deluxe School Notebook Pack', 38.00);

DELIMITER $$

-- Person n's first name, father's name and family name: the formula POS uses.
CREATE FUNCTION seed_first_name(n INT) RETURNS VARCHAR(20)
DETERMINISTIC NO SQL
RETURN ELT(1 + (n * 7) % 24, 'Mohammed', 'Abdullah', 'Fahad', 'Khalid', 'Saud', 'Faisal',
           'Omar', 'Turki', 'Nasser', 'Sultan', 'Majed', 'Yousef', 'Noura', 'Sara', 'Reem',
           'Lama', 'Hessa', 'Maha', 'Abeer', 'Dana', 'Haifa', 'Mashael', 'Ghada', 'Amal')$$

CREATE FUNCTION seed_family_name(n INT) RETURNS VARCHAR(20)
DETERMINISTIC NO SQL
RETURN ELT(1 + (n * 11 + 4) % 20, 'Al-Otaibi', 'Al-Qahtani', 'Al-Ghamdi', 'Al-Zahrani',
           'Al-Dosari', 'Al-Harbi', 'Al-Shehri', 'Al-Mutairi', 'Al-Shammari', 'Al-Anazi',
           'Al-Subaie', 'Al-Malki', 'Al-Rashidi', 'Al-Juhani', 'Al-Omari', 'Al-Faifi',
           'Al-Yami', 'Al-Hajri', 'Al-Balawi', 'Al-Khaldi')$$

CREATE FUNCTION seed_person_name(n INT) RETURNS VARCHAR(80)
DETERMINISTIC NO SQL
RETURN CONCAT(seed_first_name(n), ' ',
              ELT(1 + (n * 5 + 3) % 12, 'Mohammed', 'Abdullah', 'Fahad', 'Khalid', 'Saud',
                  'Faisal', 'Omar', 'Turki', 'Nasser', 'Sultan', 'Majed', 'Yousef'),
              ' ', seed_family_name(n))$$

DELIMITER ;

-- Reference and master data ----------------------------------------------------------------

CREATE TABLE carriers (
    carrier_code VARCHAR(10) PRIMARY KEY,
    carrier_name VARCHAR(60) NOT NULL,
    sla_days     INT NOT NULL,
    is_active    TINYINT(1) NOT NULL DEFAULT 1
) COMMENT = 'Delivery companies, each with the days it has from shipping to deliver.';

INSERT INTO carriers VALUES
    ('SMSA', 'SMSA Express', 3, 1),
    ('ARAMEX', 'Aramex', 3, 1),
    ('NAQEL', 'Naqel Express', 4, 1),
    ('SPL', 'Saudi Post (SPL)', 5, 1),
    ('ZAJIL', 'Zajil Express', 4, 1),
    ('JT', 'J&T Express', 4, 1);

CREATE TABLE customers (
    customer_id   INT PRIMARY KEY,
    loyalty_no    VARCHAR(9),
    full_name     VARCHAR(150) NOT NULL,
    email         VARCHAR(150) NOT NULL,
    mobile_number VARCHAR(10) NOT NULL,
    city          VARCHAR(50) NOT NULL,
    address_line  VARCHAR(150) NOT NULL,
    postal_code   VARCHAR(5) NOT NULL,
    registered_on DATE NOT NULL,
    marketing_opt_in TINYINT(1) NOT NULL,
    KEY customers_loyalty_no_idx (loyalty_no)
) COMMENT = 'Registered online shoppers. Those who are loyalty members carry their POS loyalty number.';

-- Customers 1-800, registered between 2023-01-01 and 2025-12-30. Customers 1-500 are the
-- loyalty members with an even POS customer number (customer m is person 2m, loyalty
-- number LY + 7 digits); 501-800 are online-only (persons 5501-5800).
INSERT INTO customers
SELECT q.customer_id,
       IF(q.customer_id <= 500, CONCAT('LY', LPAD(q.p, 7, '0')), NULL),
       seed_person_name(q.p),
       CONCAT(LOWER(seed_first_name(q.p)), '.',
              LOWER(REPLACE(seed_family_name(q.p), 'Al-', '')), q.p, '@example.com'),
       CONCAT('05', LPAD((q.p * 7919 + 1234567) % 100000000, 8, '0')),
       ELT(1 + q.p % 10, 'Riyadh', 'Riyadh', 'Riyadh', 'Riyadh', 'Riyadh', 'Jeddah', 'Jeddah',
           'Jeddah', 'Dammam', 'Al Khobar'),
       CONCAT('Building ', 10 + q.p % 90, ', ',
              ELT(1 + q.p % 6, 'King Fahd Road', 'Olaya Street', 'Tahlia Street',
                  'Prince Sultan Road', 'Al Corniche Road', 'King Abdullah Road'), ', ',
              ELT(1 + (q.p DIV 7) % 6, 'Al Olaya', 'Al Malqa', 'Al Rawdah', 'Al Nakheel',
                  'Hittin', 'Al Faisaliyah')),
       CONCAT('1', LPAD(q.p % 10000, 4, '0')),
       DATE('2023-01-01') + INTERVAL ((q.p * 13) % 1095) DAY,
       IF(q.p % 7 = 0, 0, 1)
FROM (SELECT n AS customer_id, IF(n <= 500, 2 * n, n + 5000) AS p FROM seq WHERE n <= 800) AS q;

-- Transactional data -------------------------------------------------------------------------

CREATE TABLE orders (
    order_id         INT PRIMARY KEY,
    order_no         VARCHAR(12) NOT NULL UNIQUE,
    customer_id      INT NOT NULL,
    order_date       DATE NOT NULL,
    order_status     VARCHAR(12) NOT NULL,
    order_source     VARCHAR(10) NOT NULL,
    delivery_option  VARCHAR(10) NOT NULL,
    coupon_code      VARCHAR(12),
    ship_city        VARCHAR(50) NOT NULL,
    subtotal_sar     DECIMAL(12, 2) NOT NULL DEFAULT 0,
    shipping_fee_sar DECIMAL(10, 2) NOT NULL DEFAULT 0,
    vat_sar          DECIMAL(12, 2) NOT NULL DEFAULT 0,
    total_sar        DECIMAL(12, 2) NOT NULL DEFAULT 0,
    KEY orders_order_date_idx (order_date),
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
) COMMENT = 'Online orders of 2025.';

-- 2000 orders in 2025, never before the customer registered. Every 25th order is cancelled;
-- of the others, those from 25 December on are still open (processing, or shipped but not
-- yet delivered), every 33rd is returned and the rest are delivered.
INSERT INTO orders (order_id, order_no, customer_id, order_date, order_status, order_source,
                    delivery_option, coupon_code, ship_city)
SELECT x.n,
       CONCAT('WEB', LPAD(x.n, 8, '0')),
       x.customer_id,
       x.order_date,
       CASE WHEN x.n % 25 = 0 THEN 'cancelled'
            WHEN x.order_date >= '2025-12-25'
                THEN IF(x.order_date < '2025-12-31' AND x.n % 2 = 0, 'shipped', 'processing')
            WHEN x.n % 33 = 0 THEN 'returned'
            ELSE 'delivered' END,
       ELT(1 + x.n % 5, 'web', 'web', 'ios', 'android', 'web'),
       IF(x.n % 5 = 0, 'express', 'standard'),
       IF(x.n % 8 = 0, 'WELCOME10', NULL),
       x.city
FROM (
    SELECT s.n, c.customer_id, c.city,
           GREATEST(c.registered_on, DATE('2025-01-01') + INTERVAL ((s.n * 7) % 365) DAY) AS order_date
    FROM seq s
    JOIN customers c ON c.customer_id = 1 + (s.n * 7919) % 800
    WHERE s.n <= 2000
) AS x;

CREATE TABLE order_items (
    item_id        INT PRIMARY KEY,
    order_id       INT NOT NULL,
    sku            VARCHAR(12) NOT NULL,
    product_name   VARCHAR(100) NOT NULL,
    quantity       INT NOT NULL,
    unit_price_sar DECIMAL(10, 2) NOT NULL,
    discount_sar   DECIMAL(10, 2) NOT NULL,
    line_net_sar   DECIMAL(12, 2) NOT NULL,
    KEY order_items_order_idx (order_id),
    KEY order_items_sku_idx (sku),
    CONSTRAINT order_items_order_fk FOREIGN KEY (order_id) REFERENCES orders (order_id)
) COMMENT = 'The products on an order. Net = quantity x price - discount.';

-- 3000 items: each order gets one, and 1000 orders get another. A coupon gives 10% off
-- every line of its order.
INSERT INTO order_items
SELECT y.n, y.order_id, y.sku, y.name, y.qty, y.price, y.disc, y.qty * y.price - y.disc
FROM (
    SELECT s.n, o.order_id, p.sku, p.name, p.price, 1 + s.n % 3 AS qty,
           IF(o.coupon_code IS NULL, 0, ROUND((1 + s.n % 3) * p.price * 0.10, 2)) AS disc
    FROM seq s
    JOIN orders o ON o.order_id = IF(s.n <= 2000, s.n, 1 + (s.n * 7919) % 2000)
    JOIN (
        SELECT sku, name, price,
               ROW_NUMBER() OVER (ORDER BY sku) AS rn, COUNT(*) OVER () AS total
        FROM seed_catalog
    ) AS p ON p.rn = 1 + (s.n * 7919) % p.total
    WHERE s.n <= 3000
) AS y;

-- Delivery is free for a standard order of 200 SAR or more, otherwise 20 SAR; express is
-- 35 SAR. VAT (15%) is charged on goods and delivery.
UPDATE orders o
JOIN (SELECT order_id, SUM(line_net_sar) AS goods FROM order_items GROUP BY order_id) AS x
    ON x.order_id = o.order_id
SET o.subtotal_sar = x.goods,
    o.shipping_fee_sar = IF(o.delivery_option = 'express', 35, IF(x.goods >= 200, 0, 20));
UPDATE orders
SET vat_sar = ROUND((subtotal_sar + shipping_fee_sar) * 0.15, 2),
    total_sar = subtotal_sar + shipping_fee_sar + ROUND((subtotal_sar + shipping_fee_sar) * 0.15, 2);

CREATE TABLE shipments (
    shipment_id      INT PRIMARY KEY,
    order_id         INT NOT NULL,
    carrier_code     VARCHAR(10) NOT NULL,
    tracking_no      VARCHAR(12) NOT NULL,
    shipment_status  VARCHAR(10) NOT NULL,
    shipped_on       DATE NOT NULL,
    delivered_on     DATE,
    recipient_name   VARCHAR(150) NOT NULL,
    delivery_address VARCHAR(200) NOT NULL,
    delivery_city    VARCHAR(50) NOT NULL,
    KEY shipments_order_idx (order_id)
) COMMENT = 'One shipment per shipped order, with the carrier and the delivery address.';

-- Shipped orders ship 0-1 days after the order, delivered and returned ones 0-2 days after;
-- delivery then takes 1-2 days (express) or 2-5 days (standard). Cancelled and processing
-- orders have no shipment.
INSERT INTO shipments
SELECT ROW_NUMBER() OVER (ORDER BY z.order_id),
       z.order_id,
       ELT(1 + z.order_id % 6, 'SMSA', 'ARAMEX', 'NAQEL', 'SPL', 'ZAJIL', 'JT'),
       CONCAT('TRK', LPAD((z.order_id * 7919) % 1000000000, 9, '0')),
       IF(z.order_status = 'shipped', 'in_transit', IF(z.order_status = 'returned', 'returned', 'delivered')),
       z.shipped_on,
       IF(z.order_status = 'shipped', NULL,
          z.shipped_on + INTERVAL IF(z.delivery_option = 'express', 1 + z.order_id % 2, 2 + z.order_id % 4) DAY),
       c.full_name,
       c.address_line,
       c.city
FROM (
    SELECT o.order_id, o.customer_id, o.order_status, o.delivery_option,
           o.order_date + INTERVAL IF(o.order_status = 'shipped', o.order_id % 2, o.order_id % 3) DAY AS shipped_on
    FROM orders o
    WHERE o.order_status IN ('shipped', 'delivered', 'returned')
) AS z
JOIN customers c ON c.customer_id = z.customer_id;

CREATE TABLE payments (
    payment_id     INT PRIMARY KEY,
    order_id       INT NOT NULL,
    payment_method VARCHAR(10) NOT NULL,
    payment_status VARCHAR(10) NOT NULL,
    amount_sar     DECIMAL(12, 2) NOT NULL,
    paid_at        DATETIME,
    KEY payments_order_idx (order_id)
) COMMENT = 'One payment per order: the amount, the method and when it was collected.';

-- Cancelled orders are voided, returned ones refunded, cash-on-delivery orders still open
-- are pending (collected at delivery); the rest are captured when ordered.
INSERT INTO payments
SELECT o.order_id,
       o.order_id,
       o.method,
       CASE WHEN o.order_status = 'cancelled' THEN 'voided'
            WHEN o.order_status = 'returned' THEN 'refunded'
            WHEN o.method = 'cod' AND o.order_status IN ('processing', 'shipped') THEN 'pending'
            ELSE 'captured' END,
       o.total_sar,
       CASE WHEN o.order_status = 'cancelled' THEN NULL
            WHEN o.method = 'cod'
                THEN IF(sh.delivered_on IS NULL, NULL,
                        TIMESTAMP(sh.delivered_on) + INTERVAL (21600 + (o.order_id * 7919) % 64800) SECOND)
            ELSE TIMESTAMP(o.order_date) + INTERVAL (21600 + (o.order_id * 7919) % 64800) SECOND END
FROM (
    SELECT orders.*,
           ELT(1 + order_id % 20, 'mada', 'mada', 'mada', 'mada', 'mada', 'mada', 'mada', 'mada',
               'card', 'card', 'card', 'card', 'card', 'apple_pay', 'apple_pay', 'apple_pay',
               'cod', 'cod', 'cod', 'stc_pay') AS method
    FROM orders
) AS o
LEFT JOIN shipments sh ON sh.order_id = o.order_id;

-- A view and a procedure whose JOINs show relationships no FK declares ---------------------

CREATE VIEW v_orders_to_ship AS
SELECT o.order_id,
       o.order_no,
       o.order_date,
       o.delivery_option,
       c.loyalty_no,
       c.city,
       p.payment_method,
       p.payment_status,
       DATEDIFF('2025-12-31', o.order_date) AS age_days
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN payments p ON p.order_id = o.order_id
WHERE o.order_status = 'processing';

DELIMITER $$

CREATE PROCEDURE sp_carrier_performance(IN p_carrier_code VARCHAR(10))
COMMENT 'Shipments, deliveries and on-time deliveries of one carrier, by delivery option.'
READS SQL DATA
BEGIN
    SELECT ca.carrier_code,
           o.delivery_option,
           COUNT(*) AS shipments,
           SUM(sh.delivered_on IS NOT NULL) AS delivered,
           SUM(sh.delivered_on IS NOT NULL
               AND DATEDIFF(sh.delivered_on, sh.shipped_on) <= ca.sla_days) AS on_time
    FROM shipments sh
    JOIN carriers ca ON ca.carrier_code = sh.carrier_code
    JOIN orders o ON o.order_id = sh.order_id
    WHERE ca.carrier_code = p_carrier_code
    GROUP BY ca.carrier_code, o.delivery_option;
END$$

DELIMITER ;

DROP FUNCTION seed_person_name;
DROP FUNCTION seed_family_name;
DROP FUNCTION seed_first_name;
DROP TEMPORARY TABLE seed_catalog;
DROP TEMPORARY TABLE seq;

ANALYZE TABLE carriers, customers, orders, order_items, shipments, payments;

-- The read-only user DAWAM connects as -----------------------------------------------------

CREATE USER 'dawam_reader'@'%' IDENTIFIED BY 'dawam_reader';
-- SELECT reads the data; SHOW VIEW fills in view definitions; SHOW_ROUTINE lets the
-- user read procedure and function bodies it does not own (otherwise they are NULL).
GRANT SELECT, SHOW VIEW ON shop.* TO 'dawam_reader'@'%';
GRANT SHOW_ROUTINE ON *.* TO 'dawam_reader'@'%';
