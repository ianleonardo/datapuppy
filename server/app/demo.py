"""Demo target database (DuckDB) for trying the assistant without a production DB.

Contains customers/orders/payments with FKs plus "smelly" tables (CUSTOMERS_BCK, TMP_ORDERS, SALES_20251012)
so the hygiene check, ERD and size interception can be demonstrated.
"""
from __future__ import annotations

from pathlib import Path

import duckdb


def create_demo_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    con.execute("SELECT setseed(0.42)")
    con.execute("""
    CREATE TABLE countries (country_code VARCHAR PRIMARY KEY, country_name VARCHAR NOT NULL, region VARCHAR);
    INSERT INTO countries VALUES ('SG','Singapore','ASEAN'),('MY','Malaysia','ASEAN'),('ID','Indonesia','ASEAN'),
      ('TH','Thailand','ASEAN'),('VN','Vietnam','ASEAN'),('US','United States','Americas');

    CREATE TABLE customers (
      customer_id INTEGER PRIMARY KEY, customer_name VARCHAR, email VARCHAR, phone VARCHAR,
      country_code VARCHAR REFERENCES countries(country_code), segment VARCHAR, signup_date DATE);
    INSERT INTO customers
    SELECT i, 'Customer ' || i, 'customer' || i || '@example.com', '+65 8' || lpad((i * 7919 % 10000000)::VARCHAR, 7, '0'),
           (['SG','MY','ID','TH','VN','US'])[1 + (i % 6)], (['Retail','SME','Corporate'])[1 + (i % 3)],
           DATE '2022-01-01' + INTERVAL (i % 900) DAY
    FROM range(1, 801) t(i);

    CREATE TABLE products (product_id INTEGER PRIMARY KEY, product_name VARCHAR, category VARCHAR, unit_price DECIMAL(10,2));
    INSERT INTO products SELECT i, (['Coffee O','Kaya Toast','Laksa','Teh Tarik','Nasi Lemak','Roti Prata','Chendol',
      'Satay','Mee Goreng','Bak Kut Teh'])[1 + ((i - 1) % 10)] || CASE WHEN i > 10 THEN ' Deluxe' ELSE '' END,
      (['Beverage','Breakfast','Noodles','Beverage','Rice','Breakfast','Dessert','Grill','Noodles','Soup'])[1 + ((i - 1) % 10)],
      round(2 + random() * 18, 2) FROM range(1, 21) t(i);

    CREATE TABLE orders (
      order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id), order_date TIMESTAMP,
      status VARCHAR, channel VARCHAR, country_code VARCHAR);
    INSERT INTO orders
    SELECT 20000 + i, 1 + (i * 37 % 800), TIMESTAMP '2024-01-01' + INTERVAL (i * 53 % 1000) DAY + INTERVAL (i % 24) HOUR,
           (['Completed','Completed','Completed','Pending','Cancelled','Refunded'])[1 + (i % 6)],
           (['Web','Mobile','Store'])[1 + (i % 3)], NULL
    FROM range(1, 6001) t(i);
    UPDATE orders SET country_code = c.country_code FROM customers c WHERE c.customer_id = orders.customer_id;

    CREATE TABLE order_items (
      order_id INTEGER REFERENCES orders(order_id), line_no INTEGER, product_id INTEGER REFERENCES products(product_id),
      quantity INTEGER, amount DECIMAL(12,2), PRIMARY KEY (order_id, line_no));
    INSERT INTO order_items
    SELECT o.order_id, l, 1 + ((o.order_id * l) % 20), 1 + ((o.order_id + l) % 4), 0 FROM orders o, range(1, 3) t(l);
    UPDATE order_items SET amount = quantity * p.unit_price FROM products p WHERE p.product_id = order_items.product_id;

    CREATE TABLE payments (
      payment_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id), paid_at TIMESTAMP,
      method VARCHAR, amount DECIMAL(12,2), status VARCHAR);
    INSERT INTO payments
    SELECT 900000 + row_number() OVER (), o.order_id, o.order_date + INTERVAL 2 HOUR,
           (['PayNow','Card','GrabPay','Cash'])[1 + (o.order_id % 4)], s.total,
           CASE WHEN o.status = 'Refunded' THEN 'Refunded' WHEN o.status = 'Pending' THEN 'Pending' ELSE 'Settled' END
    FROM orders o JOIN (SELECT order_id, sum(amount) AS total FROM order_items GROUP BY 1) s USING (order_id)
    WHERE o.status <> 'Cancelled';

    CREATE VIEW monthly_revenue AS
    SELECT date_trunc('month', o.order_date) AS month, o.country_code, sum(p.amount) AS revenue, count(*) AS orders
    FROM orders o JOIN payments p ON p.order_id = o.order_id WHERE p.status = 'Settled' GROUP BY ALL;

    -- smelly tables (hygiene demo)
    CREATE TABLE customers_bck AS SELECT * FROM customers;
    CREATE TABLE tmp_orders AS SELECT * FROM orders LIMIT 100;
    CREATE TABLE sales_20251012 AS SELECT * FROM payments LIMIT 500;
    CREATE TABLE orders_copy2 AS SELECT * FROM orders LIMIT 50;
    """)
    con.execute("COMMENT ON TABLE orders IS 'One row per customer order'")
    con.execute("COMMENT ON TABLE payments IS 'Settlement records; amount in SGD'")
    con.close()
