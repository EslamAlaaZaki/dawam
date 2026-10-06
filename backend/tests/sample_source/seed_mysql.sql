-- The sample source for MySQL / MariaDB: the same shape as seed.sql (spec §10).
-- In MySQL a schema is a database. Statements are separated by lines holding only "-- go"
-- (routine bodies contain semicolons).

CREATE DATABASE core CHARACTER SET utf8mb4;
-- go
CREATE DATABASE crm CHARACTER SET utf8mb4;
-- go
CREATE DATABASE restricted CHARACTER SET utf8mb4;
-- go

CREATE TABLE core.customers (
    cust_no     integer PRIMARY KEY,
    full_name   varchar(200) NOT NULL,
    national_id varchar(32) COMMENT 'National ID number',
    email       varchar(200),
    phone       varchar(64),
    branch_code varchar(8)
) COMMENT 'One row per bank customer';
-- go

CREATE TABLE core.branches (
    branch_code varchar(8) PRIMARY KEY,
    branch_name varchar(200) NOT NULL,
    CONSTRAINT branches_name_uq UNIQUE (branch_name)
);
-- go

-- core.customers.branch_code -> core.branches is the one declared relationship.
ALTER TABLE core.customers
    ADD CONSTRAINT customers_branch_fk FOREIGN KEY (branch_code) REFERENCES core.branches (branch_code);
-- go
CREATE INDEX customers_email_idx ON core.customers (email);
-- go

-- accounts.cust_no -> customers.cust_no is NOT declared: inference must find it.
CREATE TABLE core.accounts (
    acct_no   integer PRIMARY KEY,
    cust_no   integer NOT NULL,
    iban      varchar(34),
    balance   decimal(14, 2) NOT NULL DEFAULT 0,
    opened_on date
);
-- go
CREATE INDEX accounts_cust_no_idx ON core.accounts (cust_no);
-- go

CREATE TABLE core.transactions (
    txn_id  bigint PRIMARY KEY,
    acct_no integer NOT NULL,
    amount  decimal(14, 2) NOT NULL,
    txn_at  datetime NOT NULL
);
-- go

-- Arabic table and column names.
CREATE TABLE core.`عملاء_محليون` (
    `رقم` integer PRIMARY KEY,
    `الاسم` varchar(200) NOT NULL,
    `المدينة` varchar(200)
);
-- go

CREATE VIEW core.customer_balances AS
    SELECT c.cust_no, c.full_name, sum(a.balance) AS total_balance
    FROM core.customers c JOIN core.accounts a ON a.cust_no = c.cust_no
    GROUP BY c.cust_no, c.full_name;
-- go

CREATE FUNCTION core.account_turnover(p_acct integer) RETURNS decimal(14, 2)
READS SQL DATA
BEGIN
    RETURN (SELECT coalesce(sum(t.amount), 0)
            FROM core.transactions t JOIN core.accounts a ON a.acct_no = t.acct_no
            WHERE a.acct_no = p_acct);
END;
-- go

CREATE PROCEDURE core.close_account(p_acct integer)
BEGIN
    UPDATE core.accounts a JOIN core.customers c ON c.cust_no = a.cust_no
    SET a.balance = 0
    WHERE a.acct_no = p_acct;
END;
-- go

CREATE TABLE crm.contacts (
    contact_id integer AUTO_INCREMENT PRIMARY KEY,
    cust_no    integer,
    notes      text
);
-- go

CREATE TABLE restricted.salaries (
    emp_id integer PRIMARY KEY,
    salary decimal(12, 2) NOT NULL
);
-- go

INSERT INTO core.branches VALUES ('CAI', 'Cairo Downtown'), ('ALX', 'الإسكندرية');
-- go
INSERT INTO core.customers VALUES
    (1, 'Amira Hassan', '29001011234567', 'amira.hassan@example.com', '+20 100 123 4567', 'CAI'),
    (2, 'محمد علي', '28505051234568', 'mohamed.ali@example.com', '+20 111 765 4321', 'ALX'),
    (3, 'Sara Ibrahim', NULL, NULL, NULL, 'CAI');
-- go
INSERT INTO core.accounts VALUES
    (100, 1, 'EG380019000500000000263180002', 1500.50, '2020-01-15'),
    (101, 2, 'EG800002000156789012345180002', 320.00, '2021-06-01'),
    (102, 1, NULL, 0, '2022-03-10');
-- go
INSERT INTO core.transactions VALUES
    (1, 100, 200.00, '2024-01-01 10:00:00'),
    (2, 100, -50.25, '2024-01-02 11:30:00'),
    (3, 101, 75.00, '2024-01-03 09:15:00');
-- go
INSERT INTO core.`عملاء_محليون` VALUES (1, 'خالد', 'القاهرة'), (2, 'ليلى', NULL);
-- go
INSERT INTO crm.contacts (cust_no, notes) VALUES (1, 'prefers email'), (2, NULL);
-- go
INSERT INTO restricted.salaries VALUES (1, 9000), (2, 12000);
-- go

-- Users: a read-only one DAWAM should be given, and one that can write (the warning).
CREATE USER 'dawam_reader'@'%' IDENTIFIED BY 'reader-secret';
-- go
CREATE USER 'dawam_writer'@'%' IDENTIFIED BY 'writer-secret';
-- go
GRANT SELECT, EXECUTE, SHOW VIEW ON core.* TO 'dawam_reader'@'%', 'dawam_writer'@'%';
-- go
GRANT SELECT, SHOW VIEW ON crm.* TO 'dawam_reader'@'%', 'dawam_writer'@'%';
-- go
GRANT SELECT ON restricted.* TO 'dawam_reader'@'%', 'dawam_writer'@'%';
-- go
GRANT INSERT, UPDATE ON core.customers TO 'dawam_writer'@'%';
-- go
