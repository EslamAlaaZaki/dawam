-- DAWAM DDL for bigquery; layers: core

-- Schemas

CREATE SCHEMA IF NOT EXISTS core;

-- Tables

CREATE TABLE core.dim_customer (
    customer_key INT64 NOT NULL,
    customer_id STRING(20) NOT NULL,
    full_name STRING(200) NOT NULL,
    segment STRING(1),
    birth_date DATE,
    is_vip BOOL NOT NULL,
    credit_limit NUMERIC(12,2),
    profile JSON,
    scd_valid_from DATETIME NOT NULL,
    scd_valid_to DATETIME,
    scd_current_flag BOOL NOT NULL,
    row_hash STRING(64) NOT NULL,
    PRIMARY KEY (customer_key) NOT ENFORCED
);

CREATE TABLE core.dim_product (
    product_key INT64 NOT NULL,
    product_code STRING(30) NOT NULL,
    weight FLOAT64 NOT NULL,
    launched_at TIMESTAMP NOT NULL,
    opens_at TIME NOT NULL,
    guid STRING NOT NULL,
    blob BYTES(16) NOT NULL,
    `order` INT64 NOT NULL,
    doc JSON NOT NULL,
    PRIMARY KEY (product_key) NOT ENFORCED
);

CREATE TABLE core.fact_sales (
    customer_key INT64 NOT NULL,
    product_key INT64 NOT NULL,
    invoice_no STRING(20) NOT NULL,
    quantity INT64 NOT NULL,
    amount NUMERIC(18,2) NOT NULL
);

-- Foreign keys

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_customer_key FOREIGN KEY (customer_key) REFERENCES core.dim_customer (customer_key) NOT ENFORCED;

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_product_key FOREIGN KEY (product_key) REFERENCES core.dim_product (product_key) NOT ENFORCED;

-- Unknown members

INSERT INTO core.dim_customer (customer_key, customer_id, full_name, segment, birth_date, is_vip, credit_limit, profile, scd_valid_from, scd_valid_to, scd_current_flag, row_hash)
VALUES (-1, 'Unknown', 'Unknown customer\'s name', '?', NULL, FALSE, NULL, NULL, DATETIME '1900-01-01 00:00:00', DATETIME '9999-12-31 00:00:00', TRUE, 'unknown');

INSERT INTO core.dim_product (product_key, product_code, weight, launched_at, opens_at, guid, blob, `order`, doc)
VALUES (-1, 'Unknown', 1.5, TIMESTAMP '1900-01-01 00:00:00+00:00', TIME '00:00:00', '00000000-0000-0000-0000-000000000000', b'\x00', 0, JSON '{}');
