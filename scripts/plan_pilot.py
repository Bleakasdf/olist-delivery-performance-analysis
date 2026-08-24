"""Size an operational late-delivery pilot from committed order-level outputs."""

import csv
import math
from datetime import date
from pathlib import Path
from statistics import NormalDist


ROOT = Path(__file__).resolve().parents[1]
FACT = ROOT / "data" / "powerbi" / "fact_orders.csv"
OUTPUT = ROOT / "PILOT_PLAN.md"
ALPHA = 0.05
POWER = 0.80
TARGET_RELATIVE_REDUCTION = 0.20


def sample_size_per_arm(baseline: float, variant: float) -> int:
    z_alpha = NormalDist().inv_cdf(1 - ALPHA / 2)
    z_power = NormalDist().inv_cdf(POWER)
    pooled = (baseline + variant) / 2
    numerator = (
        z_alpha * math.sqrt(2 * pooled * (1 - pooled))
        + z_power * math.sqrt(
            baseline * (1 - baseline) + variant * (1 - variant)
        )
    ) ** 2
    return math.ceil(numerator / (baseline - variant) ** 2)


orders = 0
late_orders = 0
dates = []
with FACT.open(encoding="utf-8", newline="") as fact_file:
    for row in csv.DictReader(fact_file):
        orders += 1
        late_orders += int(row["late_flag"])
        dates.append(date.fromisoformat(row["purchase_date"]))

baseline = late_orders / orders
target = baseline * (1 - TARGET_RELATIVE_REDUCTION)
per_arm = sample_size_per_arm(baseline, target)
total_sample = per_arm * 2
historical_days = (max(dates) - min(dates)).days + 1
historical_orders_per_day = orders / historical_days
lower_bound_days = math.ceil(total_sample / historical_orders_per_day)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(
    f"""# Pilot plan: reduce late delivery on priority routes

## Decision

Test an operational intervention on high-volume route-seller clusters with excess late orders. Assign clusters, not individual orders, to avoid mixing operating procedures within the same route and seller.

## Individual-order sizing benchmark

- Historical late-delivery rate: **{baseline:.2%}** ({late_orders:,} of {orders:,} orders).
- Target: **{TARGET_RELATIVE_REDUCTION:.0%} relative reduction** to **{target:.2%}**.
- Naive independent-order requirement: **{per_arm:,} orders per arm** (**{total_sample:,} total**) at two-sided alpha **{ALPHA:.0%}** and power **{POWER:.0%}**.
- At the full historical flow of **{historical_orders_per_day:,.0f} orders/day**, this equals at least **{lower_bound_days} days**. A priority-route pilot will take longer because it covers only part of the flow.

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
""",
    encoding="utf-8",
)

print(f"Wrote {OUTPUT.relative_to(ROOT)}")

