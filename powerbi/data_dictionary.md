# Power BI data dictionary

## Fact table

`fact_orders.csv` contains one row per analytically eligible delivered order.

| Field group | Important fields | Meaning |
|---|---|---|
| Keys | `order_id`, `purchase_date`, `primary_seller_id`, `route_key`, `primary_category`, `distance_band` | Relationship and drill fields |
| Delivery outcome | `late_flag`, `on_time_flag`, `days_vs_promise` | Late means actual delivery date exceeded promised date |
| Stage diagnosis | `stage_bucket`, `seller_handoff_after_limit_flag`, `seller_stage_days`, `carrier_stage_days` | Candidate responsibility split, not causal attribution |
| Financial exposure | `item_value`, `freight_value`, `order_value` | Gross values associated with orders; no margin or realized-loss data |
| Customer signal | `review_score`, `low_review_flag`, `five_star_flag` | Review association measures |
| Repeat signal | `repeat_90d_eligible`, `repeat_90d_flag`, `days_to_next_purchase` | 90-day observation-window controlled repeat proxy |

## Dimensions

- `dim_date.csv`: calendar and chronological sort columns.
- `dim_seller.csv`: seller identifier and state.
- `dim_route.csv`: seller-state to customer-state route.
- `dim_category.csv`: primary order category.
- `dim_distance_band.csv`: ordered distance ranges.
- `scenario_assumptions.csv`: disconnected sensitivity table.

## Known limits

Olist does not provide warehouse scans, carrier event logs, capacity, traffic,
weather, promised-service tier, cost-to-serve or margin. The dashboard therefore
identifies where investigation should start; it cannot prove the operational
root cause or calculate actual financial loss.
