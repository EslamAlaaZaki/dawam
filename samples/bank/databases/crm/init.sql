-- CRM: the bank sample's MySQL source system (database `crm`, which is also its only
-- Database Schema). Runs once, when the container's volume is empty.
--
-- Every value is computed from a row number, so the data is the same on every machine:
-- no RAND(), no NOW(). The data is "as of" 2025-12-31. A contact that is a bank customer
-- carries the Core Banking customer number and has the same name, ID, mobile number and
-- email as in Core Banking (person n is customer CB000000n).
--
-- Relationships for the demo:
--   declared FKs  campaign_responses.campaign_id, complaints.contact_id
--   undeclared    contacts.rm_id -> relationship_managers, campaign_responses.contact_id
--                 -> contacts, complaints.category_code -> complaint_categories (by name,
--                 and in the view v_open_complaints and the procedure sp_campaign_results)

USE crm;
SET SESSION cte_max_recursion_depth = 10000;

-- Seed helpers, dropped at the end so they never reach DAWAM.

-- 1 .. 3000, to generate rows from.
CREATE TEMPORARY TABLE seq (n INT PRIMARY KEY);
INSERT INTO seq
WITH RECURSIVE s (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 3000)
SELECT n FROM s;

DELIMITER $$

-- A digit string with its Luhn check digit appended (Saudi national IDs use Luhn).
CREATE FUNCTION seed_luhn_complete(base VARCHAR(18)) RETURNS VARCHAR(19)
DETERMINISTIC NO SQL
BEGIN
    DECLARE i INT DEFAULT 1;
    DECLARE total INT DEFAULT 0;
    DECLARE d INT;
    DECLARE r VARCHAR(18) DEFAULT REVERSE(base);
    WHILE i <= CHAR_LENGTH(r) DO
        SET d = CAST(SUBSTRING(r, i, 1) AS UNSIGNED);
        IF i % 2 = 1 THEN
            SET d = d * 2;
            IF d > 9 THEN SET d = d - 9; END IF;
        END IF;
        SET total = total + d;
        SET i = i + 1;
    END WHILE;
    RETURN CONCAT(base, (10 - total % 10) % 10);
END$$

-- Person n's first name, father's name and family name: the formula Core Banking uses.
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

CREATE TABLE relationship_managers (
    rm_id         INT PRIMARY KEY,
    employee_no   VARCHAR(10) NOT NULL UNIQUE,
    full_name     VARCHAR(100) NOT NULL,
    email         VARCHAR(150) NOT NULL,
    mobile_number VARCHAR(10) NOT NULL,
    branch_code   VARCHAR(6) NOT NULL,
    hired_on      DATE NOT NULL,
    is_active     TINYINT(1) NOT NULL DEFAULT 1
) COMMENT = 'Relationship managers who look after premium and private customers.';

INSERT INTO relationship_managers VALUES
    (1, 'EMP1001', 'Abdulrahman Saleh Al-Harbi', 'abdulrahman.alharbi@najdbank.example', '0551002001', 'RUH001', '2012-02-01', 1),
    (2, 'EMP1002', 'Nouf Ibrahim Al-Qahtani', 'nouf.alqahtani@najdbank.example', '0551002002', 'RUH001', '2015-09-13', 1),
    (3, 'EMP1003', 'Bandar Ali Al-Otaibi', 'bandar.alotaibi@najdbank.example', '0551002003', 'RUH002', '2011-05-22', 1),
    (4, 'EMP1004', 'Rawan Khalid Al-Shehri', 'rawan.alshehri@najdbank.example', '0551002004', 'RUH003', '2018-01-07', 1),
    (5, 'EMP1005', 'Hamad Nasser Al-Dosari', 'hamad.aldosari@najdbank.example', '0551002005', 'RUH004', '2016-03-20', 1),
    (6, 'EMP1006', 'Lulwa Fahad Al-Subaie', 'lulwa.alsubaie@najdbank.example', '0551002006', 'RUH005', '2020-10-04', 1),
    (7, 'EMP1007', 'Waleed Omar Al-Ghamdi', 'waleed.alghamdi@najdbank.example', '0551002007', 'JED001', '2010-06-15', 1),
    (8, 'EMP1008', 'Shahad Majed Al-Zahrani', 'shahad.alzahrani@najdbank.example', '0551002008', 'JED001', '2019-04-28', 1),
    (9, 'EMP1009', 'Ziyad Turki Al-Malki', 'ziyad.almalki@najdbank.example', '0551002009', 'JED002', '2014-08-10', 1),
    (10, 'EMP1010', 'Jawaher Saud Al-Juhani', 'jawaher.aljuhani@najdbank.example', '0551002010', 'JED003', '2021-02-14', 1),
    (11, 'EMP1011', 'Ayman Sultan Al-Omari', 'ayman.alomari@najdbank.example', '0551002011', 'DMM001', '2013-11-03', 1),
    (12, 'EMP1012', 'Asma Yousef Al-Hajri', 'asma.alhajri@najdbank.example', '0551002012', 'KHB001', '2017-07-19', 1),
    (13, 'EMP1013', 'Mansour Faisal Al-Yami', 'mansour.alyami@najdbank.example', '0551002013', 'MKK001', '2015-12-06', 1),
    (14, 'EMP1014', 'Hind Abdullah Al-Rashidi', 'hind.alrashidi@najdbank.example', '0551002014', 'RUH002', '2022-05-01', 1),
    (15, 'EMP1015', 'Rakan Mohammed Al-Anazi', 'rakan.alanazi@najdbank.example', '0551002015', 'JED002', '2009-09-09', 0);

CREATE TABLE complaint_categories (
    category_code        VARCHAR(10) PRIMARY KEY,
    category_name        VARCHAR(60) NOT NULL,
    sla_days             INT NOT NULL,
    regulator_reportable TINYINT(1) NOT NULL
) COMMENT = 'Complaint categories, each with its resolution SLA in calendar days.';

INSERT INTO complaint_categories VALUES
    ('CARD', 'Cards and card payments', 5, 1),
    ('FEES', 'Fees and charges', 10, 1),
    ('TRANSFER', 'Transfers and remittances', 3, 1),
    ('ATM', 'ATM cash and deposits', 2, 1),
    ('APP', 'Mobile and online banking', 7, 0),
    ('ACCOUNT', 'Account opening and closing', 10, 1),
    ('SERVICE', 'Staff and branch service', 15, 0),
    ('LOAN', 'Personal finance', 10, 1);

CREATE TABLE campaigns (
    campaign_id    INT PRIMARY KEY,
    campaign_code  VARCHAR(20) NOT NULL UNIQUE,
    campaign_name  VARCHAR(100) NOT NULL,
    product        VARCHAR(30) NOT NULL,
    channel        VARCHAR(10) NOT NULL,
    target_segment VARCHAR(10),
    start_date     DATE NOT NULL,
    end_date       DATE NOT NULL,
    budget_sar     DECIMAL(14, 2) NOT NULL,
    status         VARCHAR(10) NOT NULL
) COMMENT = 'Marketing campaigns and the product each one promotes.';

INSERT INTO campaigns VALUES
    (1, 'CMP-2025-01', 'New Year Savings Boost', 'savings', 'sms', 'retail', '2025-01-05', '2025-02-05', 120000.00, 'completed'),
    (2, 'CMP-2025-02', 'Ramadan Cashback Card', 'credit_card', 'email', NULL, '2025-02-20', '2025-03-30', 250000.00, 'completed'),
    (3, 'CMP-2025-03', 'Payroll Switch Offer', 'payroll_account', 'sms', 'retail', '2025-03-10', '2025-04-20', 90000.00, 'completed'),
    (4, 'CMP-2025-04', 'Premium Term Deposit', 'term_deposit', 'phone', 'premium', '2025-04-15', '2025-05-31', 60000.00, 'completed'),
    (5, 'CMP-2025-05', 'Summer Travel Card', 'credit_card', 'app', NULL, '2025-06-01', '2025-07-15', 180000.00, 'completed'),
    (6, 'CMP-2025-06', 'Home Finance Week', 'personal_finance', 'email', 'premium', '2025-07-20', '2025-08-20', 150000.00, 'completed'),
    (7, 'CMP-2025-07', 'Back to School Youth Account', 'savings', 'app', 'retail', '2025-08-10', '2025-09-10', 70000.00, 'completed'),
    (8, 'CMP-2025-08', 'National Day Offer', 'personal_finance', 'sms', NULL, '2025-09-15', '2025-10-05', 200000.00, 'completed'),
    (9, 'CMP-2025-09', 'Private Banking Invitation', 'term_deposit', 'phone', 'private', '2025-10-01', '2025-11-30', 40000.00, 'completed'),
    (10, 'CMP-2025-10', 'Year End Card Upgrade', 'credit_card', 'email', NULL, '2025-11-15', '2025-12-31', 110000.00, 'active'),
    (11, 'CMP-2026-01', 'Digital Onboarding Push', 'savings', 'app', 'retail', '2026-01-10', '2026-02-28', 95000.00, 'planned'),
    (12, 'CMP-2026-02', 'Ramadan Giving', 'credit_card', 'sms', NULL, '2026-02-10', '2026-03-20', 220000.00, 'planned');

CREATE TABLE contacts (
    contact_id        INT PRIMARY KEY,
    core_customer_no  VARCHAR(12),
    full_name         VARCHAR(150) NOT NULL,
    id_number         VARCHAR(10) NOT NULL,
    mobile_number     VARCHAR(10) NOT NULL,
    email             VARCHAR(150),
    city              VARCHAR(50) NOT NULL,
    segment           VARCHAR(10) NOT NULL,
    lifecycle_stage   VARCHAR(10) NOT NULL,
    preferred_channel VARCHAR(10) NOT NULL,
    marketing_consent TINYINT(1) NOT NULL,
    rm_id             INT,
    created_at        DATETIME NOT NULL,
    KEY contacts_customer_no_idx (core_customer_no)
) COMMENT = 'People the bank talks to: customers (with their Core Banking number) and prospects.';

-- Contacts 1-950 are Core Banking customers 1-950; 951-1000 are prospects (persons 5951-6000).
INSERT INTO contacts
SELECT q.contact_id,
       IF(q.contact_id <= 950, CONCAT('CB', LPAD(q.p, 7, '0')), NULL),
       seed_person_name(q.p),
       seed_luhn_complete(CONCAT('1', LPAD((q.p * 104729 + 31415926) % 100000000, 8, '0'))),
       CONCAT('05', LPAD((q.p * 7919 + 1234567) % 100000000, 8, '0')),
       IF(q.p % 9 = 0, NULL,
          CONCAT(LOWER(seed_first_name(q.p)), '.',
                 LOWER(REPLACE(seed_family_name(q.p), 'Al-', '')), q.p, '@example.com')),
       CASE WHEN q.p % 10 < 5 THEN 'Riyadh'
            WHEN q.p % 10 < 8 THEN 'Jeddah'
            WHEN q.p % 10 = 8 THEN IF((q.p DIV 10) % 2 = 0, 'Dammam', 'Al Khobar')
            ELSE 'Makkah' END,
       CASE WHEN q.contact_id > 950 THEN 'retail'
            WHEN q.p % 20 = 0 THEN 'private' WHEN q.p % 5 = 0 THEN 'premium'
            ELSE 'retail' END,
       IF(q.contact_id <= 950, 'customer', 'prospect'),
       ELT(1 + q.p % 4, 'sms', 'email', 'phone', 'app'),
       IF(q.p % 7 = 0, 0, 1),
       IF(q.contact_id <= 950 AND q.p % 5 = 0, 1 + q.p % 14, NULL),
       TIMESTAMP('2019-01-01') + INTERVAL ((q.p * 97) % 2400) DAY
                               + INTERVAL ((q.p * 7919) % 86400) SECOND
FROM (SELECT n AS contact_id, IF(n <= 950, n, n + 5000) AS p FROM seq WHERE n <= 1000) AS q;

-- Transactional data -------------------------------------------------------------------------

CREATE TABLE campaign_responses (
    response_id          INT PRIMARY KEY,
    campaign_id          INT NOT NULL,
    contact_id           INT NOT NULL,
    response_date        DATE NOT NULL,
    response_type        VARCHAR(10) NOT NULL,
    channel              VARCHAR(10) NOT NULL,
    converted_amount_sar DECIMAL(14, 2),
    CONSTRAINT campaign_responses_campaign_fk FOREIGN KEY (campaign_id) REFERENCES campaigns (campaign_id)
) COMMENT = 'One row per tracked reaction of a contact to a campaign.';

-- 2500 responses to the ten campaigns that have run, from contacts with marketing consent.
INSERT INTO campaign_responses
SELECT s.n,
       c.campaign_id,
       k.contact_id,
       c.start_date + INTERVAL (s.n % (DATEDIFF(c.end_date, c.start_date) + 1)) DAY,
       ELT(1 + s.n % 20, 'opened', 'opened', 'opened', 'opened', 'opened', 'opened', 'opened',
           'opened', 'opened', 'opened', 'clicked', 'clicked', 'clicked', 'clicked', 'clicked',
           'applied', 'applied', 'converted', 'converted', 'opted_out'),
       c.channel,
       IF(s.n % 20 IN (17, 18),
          CASE c.product WHEN 'term_deposit' THEN 50000 + (s.n * 7919) % 450000
                         WHEN 'personal_finance' THEN 20000 + (s.n * 7919) % 180000
                         ELSE 1000 + (s.n * 7919) % 24000 END,
          NULL)
FROM seq s
JOIN campaigns c ON c.campaign_id = 1 + (s.n * 13) % 10
JOIN (
    SELECT contact_id, ROW_NUMBER() OVER (ORDER BY contact_id) AS rn, COUNT(*) OVER () AS total
    FROM contacts
    WHERE marketing_consent = 1
) AS k ON k.rn = 1 + (s.n * 7919) % k.total
WHERE s.n <= 2500;

CREATE TABLE complaints (
    complaint_id  INT PRIMARY KEY,
    contact_id    INT NOT NULL,
    category_code VARCHAR(10) NOT NULL,
    channel       VARCHAR(12) NOT NULL,
    priority      VARCHAR(6) NOT NULL,
    opened_on     DATE NOT NULL,
    resolved_on   DATE,
    status        VARCHAR(12) NOT NULL,
    CONSTRAINT complaints_contact_fk FOREIGN KEY (contact_id) REFERENCES contacts (contact_id)
) COMMENT = 'Customer complaints and their resolution.';

-- 600 complaints opened in 2025, by customers only.
INSERT INTO complaints
SELECT x.n, x.contact_id, x.category_code, x.channel, x.priority, x.opened_on,
       IF(x.status IN ('resolved', 'closed'), x.opened_on + INTERVAL ((x.n * 7) % 9) DAY, NULL),
       x.status
FROM (
    SELECT s.n,
           1 + (s.n * 7919) % 950 AS contact_id,
           CASE WHEN s.n % 20 < 5 THEN 'CARD' WHEN s.n % 20 < 8 THEN 'FEES'
                WHEN s.n % 20 < 11 THEN 'TRANSFER' WHEN s.n % 20 < 13 THEN 'ATM'
                WHEN s.n % 20 < 16 THEN 'APP' WHEN s.n % 20 = 16 THEN 'ACCOUNT'
                WHEN s.n % 20 < 19 THEN 'SERVICE' ELSE 'LOAN' END AS category_code,
           ELT(1 + s.n % 4, 'branch', 'call_center', 'mobile_app', 'email') AS channel,
           CASE WHEN s.n % 10 = 0 THEN 'high' WHEN s.n % 3 = 0 THEN 'low' ELSE 'normal' END AS priority,
           DATE('2025-01-01') + INTERVAL ((s.n * 11) % 365) DAY AS opened_on,
           CASE WHEN (s.n * 11) % 365 >= 340 AND s.n % 2 = 0 THEN 'open'
                WHEN s.n % 23 = 0 THEN 'in_progress'
                WHEN s.n % 4 = 0 THEN 'resolved'
                ELSE 'closed' END AS status
    FROM seq s
    WHERE s.n <= 600
) AS x;

-- A view and a procedure whose JOINs show relationships no FK declares ---------------------

CREATE VIEW v_open_complaints AS
SELECT cp.complaint_id,
       cp.opened_on,
       cp.priority,
       cc.category_name,
       cc.sla_days,
       ct.core_customer_no,
       rm.employee_no AS rm_employee_no,
       DATEDIFF('2025-12-31', cp.opened_on) AS age_days
FROM complaints cp
JOIN complaint_categories cc ON cc.category_code = cp.category_code
JOIN contacts ct ON ct.contact_id = cp.contact_id
LEFT JOIN relationship_managers rm ON rm.rm_id = ct.rm_id
WHERE cp.status IN ('open', 'in_progress');

DELIMITER $$

CREATE PROCEDURE sp_campaign_results(IN p_campaign_code VARCHAR(20))
COMMENT 'Responses, conversions and converted amount of one campaign, by contact segment.'
READS SQL DATA
BEGIN
    SELECT c.campaign_code,
           ct.segment,
           COUNT(*) AS responses,
           SUM(r.response_type = 'converted') AS conversions,
           SUM(r.converted_amount_sar) AS converted_amount_sar
    FROM campaign_responses r
    JOIN campaigns c ON c.campaign_id = r.campaign_id
    JOIN contacts ct ON ct.contact_id = r.contact_id
    WHERE c.campaign_code = p_campaign_code
    GROUP BY c.campaign_code, ct.segment;
END$$

DELIMITER ;

DROP FUNCTION seed_person_name;
DROP FUNCTION seed_family_name;
DROP FUNCTION seed_first_name;
DROP FUNCTION seed_luhn_complete;
DROP TEMPORARY TABLE seq;

ANALYZE TABLE relationship_managers, complaint_categories, campaigns, contacts,
              campaign_responses, complaints;

-- The read-only user DAWAM connects as -----------------------------------------------------

CREATE USER 'dawam_reader'@'%' IDENTIFIED BY 'dawam_reader';
-- SELECT reads the data; SHOW VIEW fills in view definitions; SHOW_ROUTINE lets the
-- user read procedure and function bodies it does not own (otherwise they are NULL).
GRANT SELECT, SHOW VIEW ON crm.* TO 'dawam_reader'@'%';
GRANT SHOW_ROUTINE ON *.* TO 'dawam_reader'@'%';
