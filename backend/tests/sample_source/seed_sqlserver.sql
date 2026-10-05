-- The sample source database for the SQL Server connector tests: the same content as
-- seed.sql (spec §10). Batches are separated by lines holding only GO.

CREATE SCHEMA core;
GO
CREATE SCHEMA crm;
GO
CREATE SCHEMA restricted;
GO

CREATE TABLE core.customers (
    cust_no     int NOT NULL CONSTRAINT customers_pkey PRIMARY KEY,
    full_name   nvarchar(200) NOT NULL,
    national_id nvarchar(30) NULL,
    email       nvarchar(200) NULL,
    phone       nvarchar(40) NULL,
    branch_code nvarchar(10) NULL
);
EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'One row per bank customer',
    @level0type = N'SCHEMA', @level0name = N'core', @level1type = N'TABLE', @level1name = N'customers';
EXEC sys.sp_addextendedproperty @name = N'MS_Description', @value = N'National ID number',
    @level0type = N'SCHEMA', @level0name = N'core', @level1type = N'TABLE', @level1name = N'customers',
    @level2type = N'COLUMN', @level2name = N'national_id';

CREATE TABLE core.branches (
    branch_code nvarchar(10) NOT NULL CONSTRAINT branches_pkey PRIMARY KEY,
    branch_name nvarchar(100) NOT NULL CONSTRAINT branches_name_uq UNIQUE
);

-- core.customers.branch_code -> core.branches is the one declared relationship.
ALTER TABLE core.customers
    ADD CONSTRAINT customers_branch_fk FOREIGN KEY (branch_code) REFERENCES core.branches (branch_code);

CREATE INDEX customers_email_idx ON core.customers (email);

-- accounts.cust_no -> customers.cust_no is NOT declared: inference must find it.
CREATE TABLE core.accounts (
    acct_no   int NOT NULL CONSTRAINT accounts_pkey PRIMARY KEY,
    cust_no   int NOT NULL,
    iban      nvarchar(40) NULL,
    balance   decimal(14, 2) NOT NULL CONSTRAINT accounts_balance_df DEFAULT 0,
    opened_on date NULL
);
CREATE INDEX accounts_cust_no_idx ON core.accounts (cust_no);

CREATE TABLE core.transactions (
    txn_id  bigint NOT NULL CONSTRAINT transactions_pkey PRIMARY KEY,
    acct_no int NOT NULL,
    amount  decimal(14, 2) NOT NULL,
    txn_at  datetimeoffset NOT NULL
);

-- Arabic table and column names.
CREATE TABLE core.[عملاء_محليون] (
    [رقم] int NOT NULL PRIMARY KEY,
    [الاسم] nvarchar(100) NOT NULL,
    [المدينة] nvarchar(100) NULL
);
GO

CREATE VIEW core.customer_balances AS
    SELECT c.cust_no, c.full_name, sum(a.balance) AS total_balance
    FROM core.customers c JOIN core.accounts a ON a.cust_no = c.cust_no
    GROUP BY c.cust_no, c.full_name;
GO

CREATE FUNCTION core.account_turnover(@p_acct int) RETURNS decimal(18, 2)
AS
BEGIN
    RETURN (
        SELECT coalesce(sum(t.amount), 0)
        FROM core.transactions t JOIN core.accounts a ON a.acct_no = t.acct_no
        WHERE a.acct_no = @p_acct
    );
END;
GO

CREATE PROCEDURE core.close_account @p_acct int
AS
BEGIN
    UPDATE a SET balance = 0
    FROM core.accounts a JOIN core.customers c ON c.cust_no = a.cust_no
    WHERE a.acct_no = @p_acct;
END;
GO

CREATE TABLE crm.contacts (
    contact_id int IDENTITY(1, 1) NOT NULL PRIMARY KEY,
    cust_no    int NULL,
    notes      nvarchar(max) NULL
);

CREATE TABLE restricted.salaries (
    emp_id int NOT NULL PRIMARY KEY,
    salary decimal(12, 2) NOT NULL
);

INSERT INTO core.branches VALUES (N'CAI', N'Cairo Downtown'), (N'ALX', N'الإسكندرية');
INSERT INTO core.customers VALUES
    (1, N'Amira Hassan', N'29001011234567', N'amira.hassan@example.com', N'+20 100 123 4567', N'CAI'),
    (2, N'محمد علي', N'28505051234568', N'mohamed.ali@example.com', N'+20 111 765 4321', N'ALX'),
    (3, N'Sara Ibrahim', NULL, NULL, NULL, N'CAI');
INSERT INTO core.accounts VALUES
    (100, 1, N'EG380019000500000000263180002', 1500.50, '2020-01-15'),
    (101, 2, N'EG800002000156789012345180002', 320.00, '2021-06-01'),
    (102, 1, NULL, 0, '2022-03-10');
INSERT INTO core.transactions VALUES
    (1, 100, 200.00, '2024-01-01 10:00:00 +00:00'),
    (2, 100, -50.25, '2024-01-02 11:30:00 +00:00'),
    (3, 101, 75.00, '2024-01-03 09:15:00 +00:00');
INSERT INTO core.[عملاء_محليون] VALUES (1, N'خالد', N'القاهرة'), (2, N'ليلى', NULL);
INSERT INTO crm.contacts (cust_no, notes) VALUES (1, N'prefers email'), (2, NULL);
INSERT INTO restricted.salaries VALUES (1, 9000), (2, 12000);
GO

-- Users: a read-only one DAWAM should be given, and one that can write (the warning).
-- The logins are server-wide: the fixture creates them (CHECK_POLICY = OFF, so the simple
-- test passwords are accepted); this file maps them into the database.
CREATE USER dawam_reader FOR LOGIN dawam_reader WITH DEFAULT_SCHEMA = core;
CREATE USER dawam_writer FOR LOGIN dawam_writer WITH DEFAULT_SCHEMA = core;
GRANT SELECT, EXECUTE, VIEW DEFINITION ON SCHEMA::core TO dawam_reader, dawam_writer;
GRANT SELECT, EXECUTE, VIEW DEFINITION ON SCHEMA::crm TO dawam_reader, dawam_writer;
GRANT SELECT, VIEW DEFINITION ON SCHEMA::restricted TO dawam_reader, dawam_writer;
GRANT INSERT, UPDATE ON core.customers TO dawam_writer;
GO
