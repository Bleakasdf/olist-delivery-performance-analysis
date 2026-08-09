-- Order-level item aggregation. All downstream joins use one row per order.
DROP VIEW IF EXISTS vw_order_items_agg;
CREATE VIEW vw_order_items_agg AS
SELECT
    order_id,
    COUNT(*) AS item_rows,
    COUNT(DISTINCT product_id) AS product_count,
    COUNT(DISTINCT seller_id) AS seller_count,
    SUM(price) AS item_value,
    SUM(freight_value) AS freight_value,
    MIN(shipping_limit_date) AS min_shipping_limit_date,
    MAX(shipping_limit_date) AS max_shipping_limit_date
FROM raw_order_items
GROUP BY order_id;

DROP VIEW IF EXISTS vw_order_payments_agg;
CREATE VIEW vw_order_payments_agg AS
SELECT
    order_id,
    COUNT(*) AS payment_rows,
    COUNT(DISTINCT payment_type) AS payment_type_count,
    SUM(payment_value) AS payment_value,
    MAX(payment_installments) AS max_payment_installments
FROM raw_order_payments
GROUP BY order_id;

DROP VIEW IF EXISTS vw_order_reviews_agg;
CREATE VIEW vw_order_reviews_agg AS
SELECT
    order_id,
    COUNT(*) AS review_rows,
    AVG(review_score) AS review_score,
    MIN(review_score) AS min_review_score,
    MAX(review_score) AS max_review_score
FROM raw_order_reviews
GROUP BY order_id;

-- Primary category = category with the largest item value in the order.
DROP VIEW IF EXISTS vw_order_primary_category;
CREATE VIEW vw_order_primary_category AS
WITH category_value AS (
    SELECT
        oi.order_id,
        COALESCE(ct.product_category_name_english,
                 p.product_category_name,
                 'unknown') AS category_name,
        SUM(oi.price) AS category_item_value
    FROM raw_order_items oi
    LEFT JOIN raw_products p
        ON p.product_id = oi.product_id
    LEFT JOIN raw_category_translation ct
        ON ct.product_category_name = p.product_category_name
    GROUP BY oi.order_id,
             COALESCE(ct.product_category_name_english,
                      p.product_category_name,
                      'unknown')
), ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY category_item_value DESC, category_name
           ) AS rn
    FROM category_value
)
SELECT order_id, category_name AS primary_category
FROM ranked
WHERE rn = 1;

-- Primary seller = seller with the largest item value in the order.
DROP VIEW IF EXISTS vw_order_primary_seller;
CREATE VIEW vw_order_primary_seller AS
WITH seller_value AS (
    SELECT order_id, seller_id, SUM(price) AS seller_item_value
    FROM raw_order_items
    GROUP BY order_id, seller_id
), ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY seller_item_value DESC, seller_id
           ) AS rn
    FROM seller_value
)
SELECT order_id, seller_id AS primary_seller_id
FROM ranked
WHERE rn = 1;

DROP VIEW IF EXISTS vw_orders_enriched;
CREATE VIEW vw_orders_enriched AS
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_approved_at,
    o.order_delivered_carrier_date,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    c.customer_state,
    c.customer_zip_code_prefix,
    ps.primary_seller_id,
    s.seller_state,
    s.seller_zip_code_prefix,
    pc.primary_category,
    ia.item_rows,
    ia.product_count,
    ia.seller_count,
    ia.item_value,
    ia.freight_value,
    COALESCE(ia.item_value, 0) + COALESCE(ia.freight_value, 0) AS order_value,
    ia.min_shipping_limit_date,
    ia.max_shipping_limit_date,
    pa.payment_rows,
    pa.payment_type_count,
    pa.payment_value,
    pa.max_payment_installments,
    ra.review_rows,
    ra.review_score,
    ra.min_review_score,
    ra.max_review_score,
    cg.latitude AS customer_lat,
    cg.longitude AS customer_lng,
    sg.latitude AS seller_lat,
    sg.longitude AS seller_lng,
    CASE
        WHEN o.order_status = 'delivered'
         AND o.order_purchase_timestamp IS NOT NULL
         AND o.order_delivered_carrier_date IS NOT NULL
         AND o.order_delivered_customer_date IS NOT NULL
         AND o.order_estimated_delivery_date IS NOT NULL
         AND julianday(o.order_delivered_carrier_date) >= julianday(o.order_purchase_timestamp)
         AND julianday(o.order_delivered_customer_date) >= julianday(o.order_delivered_carrier_date)
        THEN 1 ELSE 0
    END AS analysis_eligible,
    CASE
        WHEN o.order_status = 'delivered'
         AND o.order_delivered_customer_date IS NOT NULL
         AND o.order_estimated_delivery_date IS NOT NULL
         AND julianday(o.order_delivered_customer_date) > julianday(o.order_estimated_delivery_date)
        THEN 1 ELSE 0
    END AS late_flag,
    julianday(o.order_delivered_carrier_date) - julianday(o.order_purchase_timestamp)
        AS seller_stage_days,
    julianday(o.order_delivered_customer_date) - julianday(o.order_delivered_carrier_date)
        AS carrier_stage_days,
    julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp)
        AS total_cycle_days,
    julianday(o.order_delivered_customer_date) - julianday(o.order_estimated_delivery_date)
        AS days_vs_promise,
    CASE
        WHEN ia.seller_count = 1
         AND o.order_delivered_carrier_date IS NOT NULL
         AND ia.max_shipping_limit_date IS NOT NULL
         AND julianday(o.order_delivered_carrier_date) > julianday(ia.max_shipping_limit_date)
        THEN 1
        WHEN ia.seller_count = 1 THEN 0
        ELSE NULL
    END AS seller_handoff_after_limit_flag
FROM raw_orders o
LEFT JOIN raw_customers c
    ON c.customer_id = o.customer_id
LEFT JOIN vw_order_items_agg ia
    ON ia.order_id = o.order_id
LEFT JOIN vw_order_payments_agg pa
    ON pa.order_id = o.order_id
LEFT JOIN vw_order_reviews_agg ra
    ON ra.order_id = o.order_id
LEFT JOIN vw_order_primary_category pc
    ON pc.order_id = o.order_id
LEFT JOIN vw_order_primary_seller ps
    ON ps.order_id = o.order_id
LEFT JOIN raw_sellers s
    ON s.seller_id = ps.primary_seller_id
LEFT JOIN dim_geo_zip cg
    ON cg.zip_code_prefix = c.customer_zip_code_prefix
LEFT JOIN dim_geo_zip sg
    ON sg.zip_code_prefix = s.seller_zip_code_prefix;
