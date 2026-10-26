-- Optional live-test fixtures. Run only with explicit approval on the test cluster.
-- Reserved schema: pcc_validation_20261011. Never modifies existing application schemas.
-- The runner refuses to continue if this schema already exists.
CREATE DATABASE `pcc_validation_20261011` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE `pcc_validation_20261011`;

CREATE TABLE customers (
  id BIGINT PRIMARY KEY, region INT NOT NULL, name VARCHAR(64) NOT NULL,
  KEY idx_region (region)
);
CREATE TABLE orders (
  id BIGINT PRIMARY KEY, customer_id BIGINT NOT NULL, amount INT NOT NULL,
  status INT NOT NULL, note VARCHAR(64) NOT NULL,
  KEY idx_customer (customer_id), KEY idx_amount (amount),
  KEY idx_status_amount (status, amount)
);
CREATE TABLE items (
  id BIGINT PRIMARY KEY, order_id BIGINT NOT NULL, category INT NOT NULL,
  price INT NOT NULL, KEY idx_order (order_id), KEY idx_category (category)
);
CREATE TABLE partitioned_orders (
  id INT NOT NULL, amount INT NOT NULL, PRIMARY KEY(id), KEY idx_amount(amount)
) PARTITION BY RANGE(id) (
  PARTITION p0 VALUES LESS THAN (100),
  PARTITION p1 VALUES LESS THAN (200),
  PARTITION pmax VALUES LESS THAN MAXVALUE
);
CREATE TABLE pseudo_fixture (id INT PRIMARY KEY, value INT, KEY idx_value(value));
CREATE TABLE drift_fixture (id INT PRIMARY KEY, value INT);
CREATE VIEW order_view AS SELECT id, customer_id, amount, status FROM orders WHERE status = 1;
CREATE VIEW nested_view AS SELECT id, customer_id, amount FROM order_view WHERE amount > 100;

-- The runner inserts deterministic rows with bound parameters only into these tables:
-- customers: 100; orders: 1,000; items: 2,000; partitioned_orders: 240;
-- pseudo_fixture: 8; drift_fixture: 16. Total: 3,364 rows.
-- Analyze the first four tables and drift_fixture; pseudo_fixture intentionally has no stats.
-- Additional approved negative checks may add an index to drift_fixture, insert one new row,
-- and load an exported stats file back into the fixture table. No DROP or TRUNCATE is used.
