-- Run only in the new, dedicated demo database selected by the MySQL client.
CREATE TABLE customers (
  id INT PRIMARY KEY, name VARCHAR(40), region VARCHAR(20)
);
CREATE TABLE orders (
  id INT PRIMARY KEY, customer_id INT, status VARCHAR(20), amount DECIMAL(10,2),
  KEY idx_customer(customer_id), KEY idx_status_amount(status,amount)
);
INSERT INTO customers VALUES (1,'Alice','East'),(2,'Bob','West'),(3,'Carol','North');
INSERT INTO orders VALUES
(1,1,'paid',50),(2,2,'paid',120),(3,3,'pending',80),
(4,1,'paid',200),(5,2,'pending',30),(6,3,'paid',150),
(7,1,'pending',60),(8,2,'paid',300),(9,3,'paid',90),(10,1,'paid',180);
INSERT INTO orders SELECT id+10,customer_id,status,amount FROM orders;
INSERT INTO orders SELECT id+20,customer_id,status,amount FROM orders;
INSERT INTO orders SELECT id+40,customer_id,status,amount FROM orders;
INSERT INTO orders SELECT id+80,customer_id,status,amount FROM orders;
INSERT INTO orders SELECT id+160,customer_id,status,amount FROM orders;
ANALYZE TABLE customers;
ANALYZE TABLE orders;

SELECT * FROM customers WHERE id=1;
SELECT id,amount FROM orders WHERE customer_id=2 ORDER BY id LIMIT 10;
SELECT id,amount FROM orders WHERE status='paid' AND amount>100 ORDER BY amount LIMIT 10;
SELECT status,COUNT(*),SUM(amount) FROM orders GROUP BY status;
SELECT c.name,COUNT(o.id),SUM(o.amount) FROM customers c JOIN orders o ON o.customer_id=c.id GROUP BY c.name;
SELECT /*+ USE_INDEX(o,idx_customer) */ o.id,o.amount FROM orders o WHERE o.customer_id=1 LIMIT 5;
