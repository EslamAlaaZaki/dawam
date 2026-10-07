-- DAWAM DDL for snowflake; layers: staging, core, mart

-- Schemas

CREATE SCHEMA IF NOT EXISTS stg;

CREATE SCHEMA IF NOT EXISTS core;

CREATE SCHEMA IF NOT EXISTS mart;

-- Tables

CREATE TABLE stg.stg_orders (
    id BIGINT NOT NULL,
    note VARCHAR
);

CREATE TABLE core.dim_customer (
    customer_key BIGINT NOT NULL,
    customer_id VARCHAR(20) NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    segment CHAR(1),
    birth_date DATE,
    is_vip BOOLEAN NOT NULL,
    credit_limit NUMBER(12,2),
    profile VARIANT,
    scd_valid_from TIMESTAMP_NTZ NOT NULL,
    scd_valid_to TIMESTAMP_NTZ,
    scd_current_flag BOOLEAN NOT NULL,
    row_hash VARCHAR(64) NOT NULL,
    PRIMARY KEY (customer_key)
);

CREATE TABLE core.dim_product (
    product_key BIGINT NOT NULL,
    product_code VARCHAR(30) NOT NULL,
    weight DOUBLE NOT NULL,
    launched_at TIMESTAMP_TZ NOT NULL,
    opens_at TIME NOT NULL,
    guid VARCHAR(36) NOT NULL,
    blob BINARY(16) NOT NULL,
    "order" INTEGER NOT NULL,
    doc VARIANT NOT NULL,
    PRIMARY KEY (product_key)
);

CREATE TABLE core.fact_sales (
    customer_key BIGINT NOT NULL,
    product_key BIGINT NOT NULL,
    invoice_no VARCHAR(20) NOT NULL,
    quantity INTEGER NOT NULL,
    amount NUMBER(18,2) NOT NULL
);

CREATE TABLE mart.fact_sales_monthly (
    customer_key BIGINT NOT NULL,
    month DATE NOT NULL,
    total NUMBER(18,2) NOT NULL
);

-- Foreign keys

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_customer_key FOREIGN KEY (customer_key) REFERENCES core.dim_customer (customer_key);

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_product_key FOREIGN KEY (product_key) REFERENCES core.dim_product (product_key);

ALTER TABLE mart.fact_sales_monthly ADD CONSTRAINT fk_fact_sales_monthly_customer_key FOREIGN KEY (customer_key) REFERENCES core.dim_customer (customer_key);

-- Unknown members

INSERT INTO core.dim_customer (customer_key, customer_id, full_name, segment, birth_date, is_vip, credit_limit, profile, scd_valid_from, scd_valid_to, scd_current_flag, row_hash)
SELECT -1, 'Unknown', 'Unknown customer''s name', '?', NULL, FALSE, NULL, NULL, TIMESTAMP '1900-01-01 00:00:00', TIMESTAMP '9999-12-31 00:00:00', TRUE, 'unknown';

INSERT INTO core.dim_product (product_key, product_code, weight, launched_at, opens_at, guid, blob, "order", doc)
SELECT -1, 'Unknown', 1.5, TO_TIMESTAMP_TZ('1900-01-01 00:00:00+00:00'), TIME '00:00:00', '00000000-0000-0000-0000-000000000000', TO_BINARY('00'), 0, PARSE_JSON('{}');
