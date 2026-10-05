-- The sample source database for connector, extraction, inference and PII tests (spec §10).
-- It has: relationships declared by no foreign key (cust_no, acct_no), one declared FK,
-- views, procedures and functions whose bodies JOIN tables, Arabic names, PII-like values,
-- and a schema ("restricted") that tests leave outside the allowed list.

CREATE SCHEMA core;
CREATE SCHEMA crm;
CREATE SCHEMA restricted;

CREATE TABLE core.customers (
    cust_no     integer PRIMARY KEY,
    full_name   text NOT NULL,
    national_id text,
    email       text,
    phone       text,
    branch_code text
);
COMMENT ON TABLE core.customers IS 'One row per bank customer';
COMMENT ON COLUMN core.customers.national_id IS 'National ID number';

CREATE TABLE core.branches (
    branch_code text PRIMARY KEY,
    branch_name text NOT NULL
);

-- core.customers.branch_code -> core.branches is the one declared relationship.
ALTER TABLE core.customers
    ADD CONSTRAINT customers_branch_fk FOREIGN KEY (branch_code) REFERENCES core.branches (branch_code);

ALTER TABLE core.branches ADD CONSTRAINT branches_name_uq UNIQUE (branch_name);
CREATE INDEX customers_lower_email_idx ON core.customers (lower(email));

-- accounts.cust_no -> customers.cust_no is NOT declared: inference must find it.
CREATE TABLE core.accounts (
    acct_no   integer PRIMARY KEY,
    cust_no   integer NOT NULL,
    iban      text,
    balance   numeric(14, 2) NOT NULL DEFAULT 0,
    opened_on date
);
CREATE INDEX accounts_cust_no_idx ON core.accounts (cust_no);

CREATE TABLE core.transactions (
    txn_id  bigint PRIMARY KEY,
    acct_no integer NOT NULL,
    amount  numeric(14, 2) NOT NULL,
    txn_at  timestamptz NOT NULL
);

-- Arabic table and column names.
CREATE TABLE core."عملاء_محليون" (
    "رقم" integer PRIMARY KEY,
    "الاسم" text NOT NULL,
    "المدينة" text
);

CREATE VIEW core.customer_balances AS
    SELECT c.cust_no, c.full_name, sum(a.balance) AS total_balance
    FROM core.customers c JOIN core.accounts a ON a.cust_no = c.cust_no
    GROUP BY c.cust_no, c.full_name;

CREATE FUNCTION core.account_turnover(p_acct integer) RETURNS numeric
LANGUAGE sql STABLE AS $$
    SELECT coalesce(sum(t.amount), 0)
    FROM core.transactions t JOIN core.accounts a ON a.acct_no = t.acct_no
    WHERE a.acct_no = p_acct
$$;

CREATE PROCEDURE core.close_account(p_acct integer)
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE core.accounts a SET balance = 0
    FROM core.customers c
    WHERE c.cust_no = a.cust_no AND a.acct_no = p_acct;
END
$$;

CREATE TABLE crm.contacts (
    contact_id serial PRIMARY KEY,
    cust_no    integer,
    notes      text
);

CREATE TABLE restricted.salaries (
    emp_id integer PRIMARY KEY,
    salary numeric(12, 2) NOT NULL
);

INSERT INTO core.branches VALUES ('CAI', 'Cairo Downtown'), ('ALX', 'الإسكندرية');
INSERT INTO core.customers VALUES
    (1, 'Amira Hassan', '29001011234567', 'amira.hassan@example.com', '+20 100 123 4567', 'CAI'),
    (2, 'محمد علي', '28505051234568', 'mohamed.ali@example.com', '+20 111 765 4321', 'ALX'),
    (3, 'Sara Ibrahim', NULL, NULL, NULL, 'CAI');
INSERT INTO core.accounts VALUES
    (100, 1, 'EG380019000500000000263180002', 1500.50, '2020-01-15'),
    (101, 2, 'EG800002000156789012345180002', 320.00, '2021-06-01'),
    (102, 1, NULL, 0, '2022-03-10');
INSERT INTO core.transactions VALUES
    (1, 100, 200.00, '2024-01-01 10:00+00'),
    (2, 100, -50.25, '2024-01-02 11:30+00'),
    (3, 101, 75.00, '2024-01-03 09:15+00');
INSERT INTO core."عملاء_محليون" VALUES (1, 'خالد', 'القاهرة'), (2, 'ليلى', NULL);
INSERT INTO crm.contacts (cust_no, notes) VALUES (1, 'prefers email'), (2, NULL);
INSERT INTO restricted.salaries VALUES (1, 9000), (2, 12000);

-- Roles: a read-only user DAWAM should be given, and one that can write (the warning).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dawam_reader') THEN
        CREATE ROLE dawam_reader LOGIN PASSWORD 'reader-secret';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dawam_writer') THEN
        CREATE ROLE dawam_writer LOGIN PASSWORD 'writer-secret';
    END IF;
END
$$;
GRANT USAGE ON SCHEMA core, crm, restricted TO dawam_reader, dawam_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA core, crm, restricted TO dawam_reader, dawam_writer;
GRANT EXECUTE ON ALL ROUTINES IN SCHEMA core TO dawam_reader, dawam_writer;
GRANT INSERT, UPDATE ON core.customers TO dawam_writer;
