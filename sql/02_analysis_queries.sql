-- name: overall_kpis
SELECT
    COUNT(*) AS eligible_orders,
    SUM(late_flag) AS late_orders,
    1.0 - AVG(late_flag) AS on_time_rate,
    AVG(late_flag) AS late_rate,
    AVG(CASE WHEN late_flag = 1 THEN days_vs_promise END) AS avg_late_days,
    SUM(order_value) AS gross_order_value,
    SUM(CASE WHEN late_flag = 1 THEN order_value ELSE 0 END) AS late_order_value,
    SUM(CASE WHEN late_flag = 1 THEN freight_value ELSE 0 END) AS late_freight_value,
    AVG(CASE WHEN late_flag = 0 THEN review_score END) AS on_time_review_score,
    AVG(CASE WHEN late_flag = 1 THEN review_score END) AS late_review_score
FROM fact_order_analysis
WHERE analysis_eligible = 1;

-- name: monthly_trend
SELECT
    substr(order_purchase_timestamp, 1, 7) AS purchase_month,
    COUNT(*) AS orders,
    SUM(late_flag) AS late_orders,
    AVG(late_flag) AS late_rate,
    AVG(days_vs_promise) AS avg_days_vs_promise
FROM fact_order_analysis
WHERE analysis_eligible = 1
  AND order_purchase_timestamp >= '2017-01-01'
  AND order_purchase_timestamp < '2018-08-01'
GROUP BY substr(order_purchase_timestamp, 1, 7)
ORDER BY purchase_month;

-- name: stage_attribution
SELECT
    CASE
        WHEN late_flag = 0 THEN 'on_time'
        WHEN seller_count <> 1 THEN 'late_unclassified_multi_seller'
        WHEN seller_handoff_after_limit_flag = 1 THEN 'late_seller_stage_involved'
        ELSE 'late_downstream_candidate'
    END AS stage_bucket,
    COUNT(*) AS orders,
    SUM(order_value) AS order_value,
    AVG(review_score) AS avg_review_score,
    AVG(seller_stage_days) AS avg_seller_stage_days,
    AVG(carrier_stage_days) AS avg_carrier_stage_days
FROM fact_order_analysis
WHERE analysis_eligible = 1
GROUP BY stage_bucket
ORDER BY orders DESC;

-- name: distance_bands
SELECT
    distance_band,
    COUNT(*) AS orders,
    SUM(late_flag) AS late_orders,
    AVG(late_flag) AS late_rate,
    AVG(carrier_stage_days) AS avg_carrier_stage_days,
    AVG(review_score) AS avg_review_score,
    SUM(order_value) AS order_value
FROM fact_order_analysis
WHERE analysis_eligible = 1
GROUP BY distance_band
ORDER BY CASE distance_band
    WHEN '<250 km' THEN 1
    WHEN '250-749 km' THEN 2
    WHEN '750-1499 km' THEN 3
    WHEN '1500+ km' THEN 4
    ELSE 5 END;

-- name: spike_distance_decomposition
SELECT
    CASE
        WHEN substr(order_purchase_timestamp, 1, 7)
             IN ('2017-11', '2018-02', '2018-03')
        THEN 'spike_months' ELSE 'other_complete_months'
    END AS period_group,
    distance_band,
    COUNT(*) AS orders,
    SUM(late_flag) AS late_orders,
    AVG(late_flag) AS late_rate
FROM fact_order_analysis
WHERE analysis_eligible = 1
  AND order_purchase_timestamp >= '2017-01-01'
  AND order_purchase_timestamp < '2018-08-01'
GROUP BY period_group, distance_band
ORDER BY period_group, distance_band;

-- name: spike_stage_mix
SELECT
    CASE
        WHEN substr(order_purchase_timestamp, 1, 7)
             IN ('2017-11', '2018-02', '2018-03')
        THEN 'spike_months' ELSE 'other_complete_months'
    END AS period_group,
    CASE
        WHEN seller_count <> 1 THEN 'unclassified_multi_seller'
        WHEN seller_handoff_after_limit_flag = 1 THEN 'seller_stage_involved'
        ELSE 'downstream_candidate'
    END AS stage_bucket,
    COUNT(*) AS late_orders
FROM fact_order_analysis
WHERE analysis_eligible = 1
  AND late_flag = 1
  AND order_purchase_timestamp >= '2017-01-01'
  AND order_purchase_timestamp < '2018-08-01'
GROUP BY period_group, stage_bucket
ORDER BY period_group, stage_bucket;

-- name: priority_sellers
WITH baseline AS (
    SELECT AVG(late_flag) AS baseline_late_rate
    FROM fact_order_analysis
    WHERE analysis_eligible = 1
), seller_perf AS (
    SELECT
        primary_seller_id,
        seller_state,
        COUNT(*) AS orders,
        SUM(late_flag) AS late_orders,
        AVG(late_flag) AS late_rate,
        SUM(order_value) AS order_value,
        SUM(CASE WHEN late_flag = 1 THEN order_value ELSE 0 END) AS late_order_value,
        AVG(review_score) AS avg_review_score
    FROM fact_order_analysis
    WHERE analysis_eligible = 1
      AND primary_seller_id IS NOT NULL
    GROUP BY primary_seller_id, seller_state
)
SELECT
    s.*,
    b.baseline_late_rate,
    MAX(0, s.late_orders - s.orders * b.baseline_late_rate) AS excess_late_orders
FROM seller_perf s
CROSS JOIN baseline b
WHERE s.orders >= 100
ORDER BY excess_late_orders DESC, s.orders DESC;

-- name: priority_routes
WITH baseline AS (
    SELECT AVG(late_flag) AS baseline_late_rate
    FROM fact_order_analysis
    WHERE analysis_eligible = 1
), route_perf AS (
    SELECT
        CONCAT(seller_state, ' -> ', customer_state) AS route,
        seller_state,
        customer_state,
        COUNT(*) AS orders,
        SUM(late_flag) AS late_orders,
        AVG(late_flag) AS late_rate,
        AVG(distance_km) AS avg_distance_km,
        SUM(order_value) AS order_value,
        SUM(CASE WHEN late_flag = 1 THEN order_value ELSE 0 END) AS late_order_value,
        AVG(review_score) AS avg_review_score
    FROM fact_order_analysis
    WHERE analysis_eligible = 1
      AND seller_state IS NOT NULL
      AND customer_state IS NOT NULL
    GROUP BY seller_state, customer_state
)
SELECT
    r.*,
    b.baseline_late_rate,
    MAX(0, r.late_orders - r.orders * b.baseline_late_rate) AS excess_late_orders
FROM route_perf r
CROSS JOIN baseline b
WHERE r.orders >= 150
ORDER BY excess_late_orders DESC, r.orders DESC;

-- name: priority_categories
WITH baseline AS (
    SELECT AVG(late_flag) AS baseline_late_rate
    FROM fact_order_analysis
    WHERE analysis_eligible = 1
), category_perf AS (
    SELECT
        primary_category,
        COUNT(*) AS orders,
        SUM(late_flag) AS late_orders,
        AVG(late_flag) AS late_rate,
        SUM(order_value) AS order_value,
        SUM(CASE WHEN late_flag = 1 THEN order_value ELSE 0 END) AS late_order_value,
        AVG(review_score) AS avg_review_score
    FROM fact_order_analysis
    WHERE analysis_eligible = 1
    GROUP BY primary_category
)
SELECT
    c.*,
    b.baseline_late_rate,
    MAX(0, c.late_orders - c.orders * b.baseline_late_rate) AS excess_late_orders
FROM category_perf c
CROSS JOIN baseline b
WHERE c.orders >= 300
ORDER BY excess_late_orders DESC, c.orders DESC;

-- name: review_impact
SELECT
    CASE WHEN late_flag = 1 THEN 'late' ELSE 'on_time' END AS delivery_group,
    COUNT(review_score) AS reviewed_orders,
    AVG(review_score) AS avg_review_score,
    AVG(CASE WHEN review_score <= 2 THEN 1.0 ELSE 0.0 END) AS low_review_rate,
    AVG(CASE WHEN review_score = 5 THEN 1.0 ELSE 0.0 END) AS five_star_rate
FROM fact_order_analysis
WHERE analysis_eligible = 1
  AND review_score IS NOT NULL
GROUP BY late_flag;

-- name: review_impact_by_distance
SELECT
    distance_band,
    CASE WHEN late_flag = 1 THEN 'late' ELSE 'on_time' END AS delivery_group,
    COUNT(review_score) AS reviewed_orders,
    AVG(review_score) AS avg_review_score,
    AVG(CASE WHEN review_score <= 2 THEN 1.0 ELSE 0.0 END) AS low_review_rate
FROM fact_order_analysis
WHERE analysis_eligible = 1
  AND review_score IS NOT NULL
GROUP BY distance_band, late_flag
ORDER BY distance_band, late_flag;

-- name: repeat_90d
SELECT
    CASE WHEN late_flag = 1 THEN 'late' ELSE 'on_time' END AS delivery_group,
    COUNT(*) AS eligible_orders,
    SUM(repeat_90d_flag) AS repeat_orders,
    AVG(repeat_90d_flag) AS repeat_90d_rate,
    AVG(order_value) AS avg_order_value
FROM fact_order_analysis
WHERE analysis_eligible = 1
  AND repeat_90d_eligible = 1
GROUP BY late_flag;

-- name: repeat_90d_by_distance
SELECT
    distance_band,
    CASE WHEN late_flag = 1 THEN 'late' ELSE 'on_time' END AS delivery_group,
    COUNT(*) AS eligible_orders,
    AVG(repeat_90d_flag) AS repeat_90d_rate
FROM fact_order_analysis
WHERE analysis_eligible = 1
  AND repeat_90d_eligible = 1
GROUP BY distance_band, late_flag
ORDER BY distance_band, late_flag;
