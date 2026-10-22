-- Core Banking: the bank sample's PostgreSQL source system (database `corebank`,
-- Database Schema `banking`). Runs once, when the container's volume is empty.
--
-- Every value is computed from a row number, so the data is the same on every machine:
-- no random(), no now(). The data is "as of" 2025-12-31.
--
-- Relationships for the demo:
--   declared FKs  accounts.customer_id, accounts.account_type_code, transactions.account_id
--   undeclared    accounts.branch_id -> branches, transactions.txn_type_code ->
--                 transaction_types (by name), customers.home_branch_id -> branches (only
--                 the view v_customer_portfolio joins it)

CREATE SCHEMA banking;
SET search_path = banking;

-- Seed helpers, in pg_temp so they disappear with this session and never reach DAWAM.

-- A digit string with its Luhn check digit appended (Saudi national IDs use Luhn).
CREATE FUNCTION pg_temp.luhn_complete(base text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT base || ((10 - sum(d) % 10) % 10)::text
    FROM (
        SELECT CASE
                   WHEN i % 2 = 1 THEN
                       2 * substr(reverse(base), i, 1)::int
                       - CASE WHEN substr(reverse(base), i, 1)::int > 4 THEN 9 ELSE 0 END
                   ELSE substr(reverse(base), i, 1)::int
               END AS d
        FROM generate_series(1, length(base)) AS i
    ) AS digits
$$;

-- A Saudi IBAN for a 20-character BBAN (bank code + account number), with its mod-97
-- check digits ('SA' is 28 10).
CREATE FUNCTION pg_temp.sa_iban(bban text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT 'SA' || lpad((98 - mod((bban || '281000')::numeric, 97))::text, 2, '0') || bban
$$;

CREATE FUNCTION pg_temp.family_name(n int) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT (ARRAY['Al-Otaibi', 'Al-Qahtani', 'Al-Ghamdi', 'Al-Zahrani', 'Al-Dosari',
                  'Al-Harbi', 'Al-Shehri', 'Al-Mutairi', 'Al-Shammari', 'Al-Anazi',
                  'Al-Subaie', 'Al-Malki', 'Al-Rashidi', 'Al-Juhani', 'Al-Omari',
                  'Al-Faifi', 'Al-Yami', 'Al-Hajri', 'Al-Balawi', 'Al-Khaldi'])[1 + (n * 11 + 4) % 20]
$$;

-- Person n's full name: first name, father's name, family name. The CRM sample uses the
-- same formula, so a customer has the same name in both systems.
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

CREATE TABLE branches (
    branch_id   integer PRIMARY KEY,
    branch_code varchar(6) NOT NULL UNIQUE,
    branch_name varchar(100) NOT NULL,
    city        varchar(50) NOT NULL,
    region      varchar(20) NOT NULL,
    opened_on   date NOT NULL,
    is_active   boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE branches IS 'Physical branches of the bank.';

INSERT INTO branches VALUES
    (1, 'RUH001', 'Olaya Main Branch', 'Riyadh', 'Central', '2004-03-01', true),
    (2, 'RUH002', 'King Fahd Road', 'Riyadh', 'Central', '2006-09-15', true),
    (3, 'RUH003', 'Al Malqa', 'Riyadh', 'Central', '2012-01-10', true),
    (4, 'RUH004', 'Al Naseem', 'Riyadh', 'Central', '2014-05-20', true),
    (5, 'RUH005', 'Diriyah', 'Riyadh', 'Central', '2019-11-03', true),
    (6, 'JED001', 'Tahlia Street', 'Jeddah', 'Western', '2005-02-12', true),
    (7, 'JED002', 'Al Rawdah', 'Jeddah', 'Western', '2009-07-01', true),
    (8, 'JED003', 'Obhur', 'Jeddah', 'Western', '2016-04-18', true),
    (9, 'JED004', 'Al Balad', 'Jeddah', 'Western', '2011-10-09', false),
    (10, 'DMM001', 'Dammam Corniche', 'Dammam', 'Eastern', '2007-06-25', true),
    (11, 'KHB001', 'Al Khobar', 'Al Khobar', 'Eastern', '2010-03-14', true),
    (12, 'MKK001', 'Al Aziziyah', 'Makkah', 'Western', '2013-08-27', true);

CREATE TABLE account_types (
    account_type_code   varchar(10) PRIMARY KEY,
    type_name           varchar(60) NOT NULL,
    product_category    varchar(20) NOT NULL,
    minimum_balance_sar numeric(18, 2) NOT NULL,
    dormancy_months     integer,
    profit_rate_pct     numeric(5, 2),
    is_active           boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE account_types IS 'Deposit products an account can be opened as.';

INSERT INTO account_types VALUES
    ('CUR', 'Current Account', 'current', 0, 12, NULL, true),
    ('PAY', 'Payroll Account', 'current', 0, 12, NULL, true),
    ('SAV', 'Savings Account', 'savings', 500, 12, 2.25, true),
    ('YTH', 'Youth Savings Account', 'savings', 0, 24, 2.50, true),
    ('TD3', 'Term Deposit 3 Months', 'term_deposit', 10000, NULL, 4.10, true),
    ('TD12', 'Term Deposit 12 Months', 'term_deposit', 10000, NULL, 4.75, true);

CREATE TABLE transaction_types (
    txn_type_code         varchar(10) PRIMARY KEY,
    type_name             varchar(60) NOT NULL,
    direction             varchar(6) NOT NULL CHECK (direction IN ('credit', 'debit')),
    is_fee                boolean NOT NULL,
    is_customer_initiated boolean NOT NULL
);

INSERT INTO transaction_types VALUES
    ('DEP', 'Cash Deposit', 'credit', false, true),
    ('WDL', 'Cash Withdrawal', 'debit', false, true),
    ('TRF_IN', 'Incoming Transfer', 'credit', false, false),
    ('TRF_OUT', 'Outgoing Transfer', 'debit', false, true),
    ('POS', 'Card Purchase', 'debit', false, true),
    ('SAL', 'Salary Credit', 'credit', false, false),
    ('FEE', 'Service Fee', 'debit', true, false),
    ('PRF', 'Profit Payment', 'credit', false, false);

-- Master data ----------------------------------------------------------------------------

CREATE TABLE customers (
    customer_id    integer PRIMARY KEY,
    customer_no    varchar(12) NOT NULL UNIQUE,
    full_name      varchar(150) NOT NULL,
    national_id    varchar(10) NOT NULL UNIQUE,
    date_of_birth  date NOT NULL,
    gender         char(1) NOT NULL,
    mobile_number  varchar(13) NOT NULL,
    email          varchar(150),
    city           varchar(50) NOT NULL,
    home_branch_id integer NOT NULL,
    segment        varchar(10) NOT NULL,
    kyc_status     varchar(10) NOT NULL,
    status         varchar(10) NOT NULL,
    onboarded_on   date NOT NULL
);
COMMENT ON TABLE customers IS 'Individual customers of the bank (one row per person).';

-- Customers 1-1200. Half are in Riyadh; the rest in Jeddah, the Eastern Province and Makkah.
INSERT INTO customers
SELECT n,
       'CB' || lpad(n::text, 7, '0'),
       pg_temp.person_name(n),
       pg_temp.luhn_complete('1' || lpad(((n * 104729 + 31415926) % 100000000)::text, 8, '0')),
       date '1960-01-01' + (n * 3571) % 15000,
       CASE WHEN (n * 7) % 24 < 12 THEN 'M' ELSE 'F' END,
       '+9665' || lpad(((n * 7919 + 1234567) % 100000000)::text, 8, '0'),
       CASE WHEN n % 9 = 0 THEN NULL
            ELSE lower(split_part(pg_temp.person_name(n), ' ', 1)) || '.'
                 || lower(replace(pg_temp.family_name(n), 'Al-', '')) || n || '@example.com'
       END,
       b.city,
       b.branch_id,
       CASE WHEN n % 20 = 0 THEN 'private' WHEN n % 5 = 0 THEN 'premium' ELSE 'retail' END,
       CASE WHEN n % 53 = 0 THEN 'expired' WHEN n % 37 = 0 THEN 'pending' ELSE 'verified' END,
       CASE WHEN n % 41 = 0 THEN 'inactive' ELSE 'active' END,
       date '2015-01-01' + (n * 97) % 3650
FROM generate_series(1, 1200) AS n
JOIN branches b ON b.branch_id = CASE
    WHEN n % 10 < 5 THEN 1 + n % 5
    WHEN n % 10 < 8 THEN 6 + (n / 10) % 4
    WHEN n % 10 = 8 THEN 10 + (n / 10) % 2
    ELSE 12
END;

CREATE TABLE accounts (
    account_id         integer PRIMARY KEY,
    account_number     varchar(18) NOT NULL UNIQUE,
    iban               varchar(24) NOT NULL UNIQUE,
    customer_id        integer NOT NULL REFERENCES customers (customer_id),
    account_type_code  varchar(10) NOT NULL REFERENCES account_types (account_type_code),
    branch_id          integer NOT NULL,
    currency_code      char(3) NOT NULL DEFAULT 'SAR',
    opened_on          date NOT NULL,
    closed_on          date,
    status             varchar(10) NOT NULL,
    current_balance    numeric(18, 2) NOT NULL,
    last_activity_date date
);
COMMENT ON TABLE accounts IS 'Deposit accounts. One customer can hold several.';
CREATE INDEX accounts_customer_idx ON accounts (customer_id);

-- Accounts 1-1800: every customer has one, and 600 customers have a second one, sometimes
-- at another branch.
INSERT INTO accounts
SELECT m,
       lpad((1000000000 + m * 37)::text, 18, '0'),
       pg_temp.sa_iban('77' || lpad((1000000000 + m * 37)::text, 18, '0')),
       c.customer_id,
       CASE m % 10 WHEN 0 THEN 'SAV' WHEN 1 THEN 'SAV' WHEN 2 THEN 'PAY' WHEN 3 THEN 'PAY'
                   WHEN 4 THEN 'YTH' WHEN 5 THEN CASE WHEN m % 20 = 5 THEN 'TD3' ELSE 'TD12' END
                   ELSE 'CUR' END,
       CASE WHEN m > 1200 AND m % 3 = 0 THEN 1 + m % 12 ELSE c.home_branch_id END,
       'SAR',
       least(c.onboarded_on + (m * 13) % 400, date '2025-06-30'),
       NULL,
       'active',
       0,
       NULL
FROM generate_series(1, 1800) AS m
JOIN customers c ON c.customer_id = CASE WHEN m <= 1200 THEN m ELSE 1 + (m * 7) % 1200 END;

UPDATE accounts SET status = 'closed', closed_on = least(opened_on + 300, date '2025-12-01')
WHERE account_id % 29 = 0;
UPDATE accounts SET status = 'dormant'
WHERE account_id % 11 = 0 AND status = 'active'
  AND account_type_code IN ('CUR', 'SAV', 'PAY', 'YTH');
UPDATE accounts SET current_balance = CASE
    WHEN status = 'closed' THEN 0
    WHEN account_type_code IN ('TD3', 'TD12') THEN 10000 + (account_id * 7919) % 490000
    ELSE (account_id * 7919) % 85000 + (account_id % 100) / 100.0
END;

-- Transactions -----------------------------------------------------------------------------

CREATE TABLE transactions (
    transaction_id    bigint PRIMARY KEY,
    reference_no      varchar(12) NOT NULL UNIQUE,
    account_id        integer NOT NULL REFERENCES accounts (account_id),
    txn_type_code     varchar(10) NOT NULL,
    channel           varchar(10) NOT NULL,
    transaction_date  date NOT NULL,
    posted_at         timestamp NOT NULL,
    amount_sar        numeric(18, 2) NOT NULL,
    merchant_category varchar(30)
);
COMMENT ON TABLE transactions IS 'Posted money movements on accounts during 2025.';
CREATE INDEX transactions_account_date_idx ON transactions (account_id, transaction_date);

-- 3000 transactions in 2025, on active non-term accounts only.
INSERT INTO transactions
SELECT t,
       'TXN' || lpad(t::text, 9, '0'),
       a.account_id,
       k.code,
       CASE k.code
           WHEN 'POS' THEN 'card'
           WHEN 'SAL' THEN 'system' WHEN 'FEE' THEN 'system' WHEN 'PRF' THEN 'system'
           WHEN 'DEP' THEN CASE WHEN t % 3 = 0 THEN 'branch' ELSE 'atm' END
           WHEN 'WDL' THEN CASE WHEN t % 3 = 0 THEN 'branch' ELSE 'atm' END
           ELSE CASE WHEN t % 4 = 0 THEN 'online' ELSE 'mobile' END
       END,
       date '2025-01-01' + (t * 7) % 365,
       (date '2025-01-01' + (t * 7) % 365) + make_interval(secs => (t * 7919) % 86400),
       CASE k.code
           WHEN 'FEE' THEN 10 + (t % 5) * 10
           WHEN 'SAL' THEN 4000 + (t * 7919) % 26000
           WHEN 'POS' THEN 15 + (t * 7919) % 1500 + (t % 100) / 100.0
           WHEN 'PRF' THEN 5 + (t * 31) % 400 + (t % 100) / 100.0
           ELSE 100 + (t * 7919) % 9900
       END,
       CASE WHEN k.code = 'POS'
            THEN (ARRAY['Grocery', 'Restaurant', 'Fuel station', 'Pharmacy', 'Electronics',
                        'Clothing', 'Travel'])[1 + (t * 3) % 7]
       END
FROM generate_series(1, 3000) AS t
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN t % 20 < 4 OR t % 20 = 19 THEN 'POS'
        WHEN t % 20 < 7 THEN 'TRF_OUT'
        WHEN t % 20 < 10 THEN 'TRF_IN'
        WHEN t % 20 < 12 THEN 'DEP'
        WHEN t % 20 < 15 THEN 'WDL'
        WHEN t % 20 < 17 THEN 'SAL'
        WHEN t % 20 = 17 THEN 'FEE'
        ELSE 'PRF'
    END AS code
) AS k
JOIN (
    SELECT account_id, row_number() OVER (ORDER BY account_id) AS rn, count(*) OVER () AS total
    FROM accounts
    WHERE status = 'active' AND account_type_code IN ('CUR', 'SAV', 'PAY', 'YTH')
) AS a ON a.rn = 1 + (t * 7919) % a.total;

UPDATE accounts a SET last_activity_date = x.last_date
FROM (SELECT account_id, max(transaction_date) AS last_date FROM transactions GROUP BY account_id) AS x
WHERE x.account_id = a.account_id;
UPDATE accounts SET last_activity_date = date '2024-09-30' - (account_id * 7) % 600
WHERE status = 'dormant';
UPDATE accounts SET last_activity_date = closed_on WHERE status = 'closed';

-- A view and a function whose JOINs show relationships no FK declares ---------------------

CREATE VIEW v_customer_portfolio AS
SELECT c.customer_no,
       c.segment,
       b.branch_code AS home_branch_code,
       b.city,
       count(a.account_id) AS open_accounts,
       coalesce(sum(a.current_balance), 0) AS total_balance_sar
FROM banking.customers c
JOIN banking.branches b ON b.branch_id = c.home_branch_id
LEFT JOIN banking.accounts a ON a.customer_id = c.customer_id AND a.status <> 'closed'
GROUP BY c.customer_no, c.segment, b.branch_code, b.city;

CREATE FUNCTION fn_branch_deposits(p_branch_code varchar, p_from date, p_to date)
RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(t.amount_sar), 0)
    FROM banking.transactions t
    JOIN banking.accounts a ON a.account_id = t.account_id
    JOIN banking.branches b ON b.branch_id = a.branch_id
    JOIN banking.transaction_types tt ON tt.txn_type_code = t.txn_type_code
    WHERE b.branch_code = p_branch_code
      AND tt.direction = 'credit'
      AND t.transaction_date BETWEEN p_from AND p_to;
$$;
COMMENT ON FUNCTION fn_branch_deposits IS 'Credits posted to a branch''s accounts in a date range.';

ANALYZE;

-- The read-only user DAWAM connects as -----------------------------------------------------

CREATE ROLE dawam_reader LOGIN PASSWORD 'dawam_reader';
ALTER ROLE dawam_reader SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE corebank TO dawam_reader;
GRANT USAGE ON SCHEMA banking TO dawam_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA banking TO dawam_reader;
-- Function definitions are readable through pg_proc without further grants.
