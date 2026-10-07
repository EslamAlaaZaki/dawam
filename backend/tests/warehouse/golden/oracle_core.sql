-- DAWAM DDL for oracle; layers: core

-- Schemas

-- Oracle: schema core is a user; create it first (CREATE USER core ...).

-- Tables

CREATE TABLE core.dim_customer (
    customer_key NUMBER(19) NOT NULL,
    customer_id VARCHAR2(20) NOT NULL,
    full_name VARCHAR2(200) NOT NULL,
    segment CHAR(1),
    birth_date DATE,
    is_vip NUMBER(1) NOT NULL,
    credit_limit NUMBER(12,2),
    profile CLOB,
    scd_valid_from TIMESTAMP NOT NULL,
    scd_valid_to TIMESTAMP,
    scd_current_flag NUMBER(1) NOT NULL,
    row_hash VARCHAR2(64) NOT NULL,
    PRIMARY KEY (customer_key)
);

CREATE TABLE core.dim_product (
    product_key NUMBER(19) NOT NULL,
    product_code VARCHAR2(30) NOT NULL,
    weight BINARY_DOUBLE NOT NULL,
    launched_at TIMESTAMP WITH TIME ZONE NOT NULL,
    opens_at INTERVAL DAY(0) TO SECOND(0) NOT NULL,
    guid RAW(16) NOT NULL,
    blob RAW(16) NOT NULL,
    "order" NUMBER(10) NOT NULL,
    doc CLOB NOT NULL,
    PRIMARY KEY (product_key)
);

CREATE TABLE core.fact_sales (
    customer_key NUMBER(19) NOT NULL,
    product_key NUMBER(19) NOT NULL,
    invoice_no VARCHAR2(20) NOT NULL,
    quantity NUMBER(10) NOT NULL,
    amount NUMBER(18,2) NOT NULL
);

-- Foreign keys

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_customer_key FOREIGN KEY (customer_key) REFERENCES core.dim_customer (customer_key);

ALTER TABLE core.fact_sales ADD CONSTRAINT fk_fact_sales_product_key FOREIGN KEY (product_key) REFERENCES core.dim_product (product_key);

-- Unknown members

INSERT INTO core.dim_customer (customer_key, customer_id, full_name, segment, birth_date, is_vip, credit_limit, profile, scd_valid_from, scd_valid_to, scd_current_flag, row_hash)
VALUES (-1, 'Unknown', 'Unknown customer''s name', '?', NULL, 0, NULL, NULL, TIMESTAMP '1900-01-01 00:00:00', TIMESTAMP '9999-12-31 00:00:00', 1, 'unknown');

INSERT INTO core.dim_product (product_key, product_code, weight, launched_at, opens_at, guid, blob, "order", doc)
VALUES (-1, 'Unknown', 1.5, TIMESTAMP '1900-01-01 00:00:00 +00:00', INTERVAL '0 00:00:00' DAY TO SECOND, HEXTORAW('00000000000000000000000000000000'), HEXTORAW('00'), 0, '{}');
