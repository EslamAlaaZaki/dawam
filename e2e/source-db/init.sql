-- The seeded PostgreSQL source of the end-to-end suite: a tiny shop in schema `sales`,
-- read through a read-only user (DAWAM never writes to a source).
CREATE SCHEMA sales;

CREATE TABLE sales.customers (
    customer_no varchar(20) PRIMARY KEY,
    name        varchar(200) NOT NULL,
    city        varchar(100)
);

CREATE TABLE sales.orders (
    order_no    varchar(20) PRIMARY KEY,
    customer_no varchar(20) NOT NULL REFERENCES sales.customers (customer_no),
    order_date  date NOT NULL,
    amount      numeric(18, 2) NOT NULL
);

INSERT INTO sales.customers VALUES
    ('C001', 'Amal Trading', 'Cairo'),
    ('C002', 'Nile Foods', 'Giza'),
    ('C003', 'Delta Supplies', 'Alexandria');

INSERT INTO sales.orders VALUES
    ('O-1001', 'C001', '2025-01-05', 1200.50),
    ('O-1002', 'C002', '2025-01-07', 310.00),
    ('O-1003', 'C001', '2025-02-11', 87.25);

CREATE ROLE reader LOGIN PASSWORD 'reader';
GRANT USAGE ON SCHEMA sales TO reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sales TO reader;
