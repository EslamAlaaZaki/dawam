-- DAWAM DDL for sqlserver; layers: staging, core, mart

-- Schemas

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'stg')
    EXEC('CREATE SCHEMA stg');

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'core')
    EXEC('CREATE SCHEMA core');

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'mart')
    EXEC('CREATE SCHEMA mart');

-- Tables

CREATE TABLE stg.stg_orders (
    id BIGINT NOT NULL,
    note NVARCHAR(MAX)
);

CREATE TABLE core.dim_customer (
    customer_key BIGINT NOT NULL,
    customer_id NVARCHAR(20) NOT NULL,
    full_name NVARCHAR(200) NOT NULL,
    segment NCHAR(1),
    birth_date DATE,
    is_vip BIT NOT NULL,
    credit_limit DECIMAL(12,2),
    profile NVARCHAR(MAX),
    scd_valid_from DATETIME2 NOT NULL,
    scd_valid_to DATETIME2,
    scd_current_flag BIT NOT NULL,
    row_hash NVARCHAR(64) NOT NULL,
    PRIMARY KEY (customer_key)
);

CREATE TABLE core.dim_product (
    product_key BIGINT NOT NULL,
    product_code NVARCHAR(30) NOT NULL,
    weight FLOAT(53) NOT NULL,
    launched_at DATETIMEOFFSET NOT NULL,
    opens_at TIME NOT NULL,
    guid UNIQUEIDENTIFIER NOT NULL,
    blob VARBINARY(16) NOT NULL,
    [order] INT NOT NULL,
    doc NVARCHAR(MAX) NOT NULL,
    PRIMARY KEY (product_key)
);

CREATE TABLE core.fact_sales (
    customer_key BIGINT NOT NULL,
    product_key BIGINT NOT NULL,
    invoice_no NVARCHAR(20) NOT NULL,
    quantity INT NOT NULL,
    amount DECIMAL(18,2) NOT NULL
);

CREATE TABLE mart.fact_sales_monthly (
    customer_key BIGINT NOT NULL,
    month DATE NOT NULL,
    total DECIMAL(18,2) NOT NULL
);

-- Foreign keys

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_customer_key FOREIGN KEY (customer_key) REFERENCES core.dim_customer (customer_key);

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_product_key FOREIGN KEY (product_key) REFERENCES core.dim_product (product_key);

ALTER TABLE mart.fact_sales_monthly ADD CONSTRAINT fk_fact_sales_monthly_customer_key FOREIGN KEY (customer_key) REFERENCES core.dim_customer (customer_key);

-- Unknown members

INSERT INTO core.dim_customer (customer_key, customer_id, full_name, segment, birth_date, is_vip, credit_limit, profile, scd_valid_from, scd_valid_to, scd_current_flag, row_hash)
VALUES (-1, N'Unknown', N'Unknown customer''s name', N'?', NULL, 0, NULL, NULL, '1900-01-01 00:00:00', '9999-12-31 00:00:00', 1, N'unknown');

INSERT INTO core.dim_product (product_key, product_code, weight, launched_at, opens_at, guid, blob, [order], doc)
VALUES (-1, N'Unknown', 1.5, '1900-01-01 00:00:00+00:00', '00:00:00', CAST('00000000-0000-0000-0000-000000000000' AS UNIQUEIDENTIFIER), 0x00, 0, '{}');
