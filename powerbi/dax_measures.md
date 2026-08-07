# DAX measures

The measures below are included in the semantic model. Technical field names remain unchanged so the calculations are reproducible.

```DAX
Orders analyzed =
DISTINCTCOUNT ( fact_orders[order_id] )

Late orders =
CALCULATE ( [Orders analyzed], fact_orders[late_flag] = 1 )

On-time orders =
CALCULATE ( [Orders analyzed], fact_orders[late_flag] = 0 )

Late rate % =
DIVIDE ( [Late orders], [Orders analyzed] )

On-time delivery OTD % =
DIVIDE ( [On-time orders], [Orders analyzed] )

OTD target % =
0.935

Average delay, days =
CALCULATE (
    AVERAGE ( fact_orders[days_vs_promise] ),
    fact_orders[late_flag] = 1
)

Total order value =
SUM ( fact_orders[order_value] )

Value of late orders =
CALCULATE ( [Total order value], fact_orders[late_flag] = 1 )

Seller-stage late orders =
CALCULATE (
    [Late orders],
    fact_orders[stage_bucket] = "late_seller_stage_involved"
)

Post-handoff late orders =
CALCULATE (
    [Late orders],
    fact_orders[stage_bucket] = "late_downstream_candidate"
)

Classified late orders =
[Seller-stage late orders] + [Post-handoff late orders]

Seller-stage share % =
DIVIDE ( [Seller-stage late orders], [Classified late orders] )

Post-handoff share % =
DIVIDE ( [Post-handoff late orders], [Classified late orders] )

Orders with reviews =
CALCULATE ( [Orders analyzed], fact_orders[reviewed_flag] = 1 )

Average review score =
AVERAGE ( fact_orders[review_score] )

Low review rate % =
DIVIDE ( SUM ( fact_orders[low_review_flag] ), [Orders with reviews] )

Orders eligible for 90-day repeat window =
CALCULATE ( [Orders analyzed], fact_orders[repeat_90d_eligible] = 1 )

Repeat orders within 90 days =
CALCULATE (
    SUM ( fact_orders[repeat_90d_flag] ),
    fact_orders[repeat_90d_eligible] = 1
)

90-day repeat order rate % =
DIVIDE ( [Repeat orders within 90 days], [Orders eligible for 90-day repeat window] )

Baseline late rate % =
CALCULATE ( [Late rate %], REMOVEFILTERS ( dim_route ) )

Excess late orders =
MAX (
    0,
    [Late orders] - [Orders analyzed] * [Baseline late rate %]
)

Scenario: prevented late orders =
[Late orders] * SELECTEDVALUE ( scenario_assumptions[Late-rate reduction], 0.2 )

Scenario: protected order value =
[Value of late orders] * SELECTEDVALUE ( scenario_assumptions[Late-rate reduction], 0.2 )
```

Percentage measures use percentage formatting and monetary measures use Brazilian real (R$). The 93.5% OTD target is a pilot reference equal to a 20% relative reduction in the current late rate, not a contractual SLA.
