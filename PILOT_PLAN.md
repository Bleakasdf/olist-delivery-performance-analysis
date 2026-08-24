# Pilot plan: reduce late delivery on priority routes

## Decision

Test an operational intervention on high-volume route-seller clusters with excess late orders. Assign clusters, not individual orders, to avoid mixing operating procedures within the same route and seller.

## Individual-order sizing benchmark

- Historical late-delivery rate: **8.12%** (7,822 of 96,281 orders).
- Target: **20% relative reduction** to **6.50%**.
- Naive independent-order requirement: **4,029 orders per arm** (**8,058 total**) at two-sided alpha **5%** and power **80%**.
- At the full historical flow of **135 orders/day**, this equals at least **60 days**. A priority-route pilot will take longer because it covers only part of the flow.

The independent-order result is a lower bound. Final sizing must apply a cluster design effect estimated from pre-period route-seller data.

## Evaluation design

1. Match or stratify clusters on pre-period order volume, late rate, seller handoff compliance, distance band, and freight intensity.
2. Randomize matched clusters to intervention or business-as-usual control.
3. Compare pre-to-post change between treatment and control (difference-in-differences) and report cluster-robust uncertainty.
4. Primary outcome: late-delivery rate. Diagnostics: seller-stage delay and post-handoff duration.
5. Guardrails: review score, freight cost per order, cancellations, and promised lead time.

## Decision rule

Scale only if the late-delivery rate improves against control, operational guardrails remain acceptable, and the effect is not driven by one cluster. Do not attribute improvement to a carrier without carrier identifiers and logistics-event data.

## Caveats

The historical data are observational and dated. They size a pilot and define an evaluation plan; they do not forecast current performance or prove that the proposed intervention will work.

