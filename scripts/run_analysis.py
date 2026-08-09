"""Run the delivery analysis and export reproducible result tables."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data" / "processed" / "olist_supply_chain.sqlite"
QUERY_PATH = PROJECT_DIR / "sql" / "02_analysis_queries.sql"
OUTPUT_DIR = PROJECT_DIR / "outputs" / "tables"
SUMMARY_PATH = PROJECT_DIR / "outputs" / "analysis_summary.json"


def parse_named_queries(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^-- name: ([a-zA-Z0-9_]+)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    queries = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        queries[match.group(1)] = text[start:end].strip().rstrip(";")
    return queries


def proportion_difference_ci(success_a, total_a, success_b, total_b, alpha=0.05):
    rate_a = success_a / total_a
    rate_b = success_b / total_b
    difference = rate_a - rate_b
    z = NormalDist().inv_cdf(1 - alpha / 2)
    standard_error = math.sqrt(
        rate_a * (1 - rate_a) / total_a + rate_b * (1 - rate_b) / total_b
    )
    return difference, difference - z * standard_error, difference + z * standard_error


def mean_difference_ci(a: pd.Series, b: pd.Series, alpha=0.05):
    a = a.dropna().astype(float)
    b = b.dropna().astype(float)
    difference = a.mean() - b.mean()
    standard_error = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    z = NormalDist().inv_cdf(1 - alpha / 2)
    return difference, difference - z * standard_error, difference + z * standard_error


def money(value):
    return f"R${value:,.0f}"


def percentage(value):
    return f"{value:.1%}"


def markdown_table(frame: pd.DataFrame) -> str:
    """Render a small dataframe without an optional tabulate dependency."""
    if frame.empty:
        return ""
    columns = list(frame.columns)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in frame.itertuples(index=False, name=None):
        values = [str(value).replace("|", "\\|") for value in row]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def build_financial_scenarios(
    eligible: pd.DataFrame, repeat_gap: float
) -> pd.DataFrame:
    late = eligible.loc[eligible["late_flag"].eq(1)]
    late_orders = len(late)
    average_late_order_value = late["order_value"].mean()
    average_repeat_order_value = eligible.loc[
        eligible["repeat_90d_flag"].eq(1), "next_order_value"
    ].mean()
    if pd.isna(average_repeat_order_value):
        average_repeat_order_value = eligible["order_value"].mean()

    rows = []
    for late_reduction in [0.10, 0.20, 0.30]:
        for attributable_share in [0.25, 0.50, 1.00]:
            reduced_orders = late_orders * late_reduction
            incremental_repeats = (
                reduced_orders * max(repeat_gap, 0) * attributable_share
            )
            rows.append(
                {
                    "relative_late_reduction": late_reduction,
                    "attributable_share_of_repeat_gap": attributable_share,
                    "late_orders_reduced": reduced_orders,
                    "protected_order_value": reduced_orders
                    * average_late_order_value,
                    "estimated_incremental_repeat_orders": incremental_repeats,
                    "estimated_incremental_repeat_gmv": incremental_repeats
                    * average_repeat_order_value,
                    "scenario_note": "Directional; not a causal forecast",
                }
            )
    return pd.DataFrame(rows)


def add_segment_opportunity(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["avg_late_order_value"] = np.where(
        result["late_orders"].gt(0),
        result["late_order_value"] / result["late_orders"],
        0,
    )
    result["protected_order_value_at_baseline"] = (
        result["excess_late_orders"] * result["avg_late_order_value"]
    )
    return result


def run_analysis():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)

    queries = parse_named_queries(QUERY_PATH)
    results = {}
    with sqlite3.connect(DATABASE_PATH) as connection:
        for name, query in queries.items():
            results[name] = pd.read_sql_query(query, connection)

        fact = pd.read_sql_query(
            """
            SELECT order_id, customer_unique_id, analysis_eligible, late_flag,
                   seller_count, seller_handoff_after_limit_flag,
                   seller_stage_days, carrier_stage_days, days_vs_promise,
                   order_value, freight_value, review_score, distance_band,
                   repeat_90d_eligible, repeat_90d_flag, next_order_value
            FROM fact_order_analysis
            """,
            connection,
        )
        validation_base = pd.read_sql_query(
            """
            SELECT
                (SELECT COUNT(*) FROM raw_orders) AS raw_orders,
                (SELECT COUNT(*) FROM fact_order_analysis) AS fact_rows,
                (SELECT COUNT(DISTINCT order_id) FROM fact_order_analysis) AS distinct_fact_orders,
                (SELECT SUM(price + freight_value) FROM raw_order_items) AS raw_order_value,
                (SELECT SUM(order_value) FROM fact_order_analysis) AS fact_order_value,
                (SELECT COUNT(*) FROM fact_order_analysis
                  WHERE analysis_eligible = 1
                    AND (seller_stage_days < 0 OR carrier_stage_days < 0)) AS invalid_stage_rows_in_eligible
            """,
            connection,
        ).iloc[0]

    eligible = fact.loc[fact["analysis_eligible"].eq(1)].copy()

    review_on_time = eligible.loc[eligible["late_flag"].eq(0), "review_score"]
    review_late = eligible.loc[eligible["late_flag"].eq(1), "review_score"]
    review_gap, review_ci_low, review_ci_high = mean_difference_ci(
        review_on_time, review_late
    )

    low_on_time = review_on_time.le(2).sum()
    low_late = review_late.le(2).sum()
    low_review_gap, low_ci_low, low_ci_high = proportion_difference_ci(
        low_late,
        review_late.notna().sum(),
        low_on_time,
        review_on_time.notna().sum(),
    )

    repeat_population = eligible.loc[eligible["repeat_90d_eligible"].eq(1)]
    repeat_on_time = repeat_population.loc[repeat_population["late_flag"].eq(0)]
    repeat_late = repeat_population.loc[repeat_population["late_flag"].eq(1)]
    repeat_gap, repeat_ci_low, repeat_ci_high = proportion_difference_ci(
        repeat_on_time["repeat_90d_flag"].sum(),
        len(repeat_on_time),
        repeat_late["repeat_90d_flag"].sum(),
        len(repeat_late),
    )

    statistical_effects = pd.DataFrame(
        [
            {
                "metric": "review_score_on_time_minus_late",
                "effect": review_gap,
                "ci_95_low": review_ci_low,
                "ci_95_high": review_ci_high,
                "interpretation": "Association, not causal effect",
            },
            {
                "metric": "low_review_rate_late_minus_on_time",
                "effect": low_review_gap,
                "ci_95_low": low_ci_low,
                "ci_95_high": low_ci_high,
                "interpretation": "Association, not causal effect",
            },
            {
                "metric": "repeat_90d_on_time_minus_late",
                "effect": repeat_gap,
                "ci_95_low": repeat_ci_low,
                "ci_95_high": repeat_ci_high,
                "interpretation": "Censored observational association",
            },
        ]
    )

    results["priority_sellers"] = add_segment_opportunity(
        results["priority_sellers"]
    )
    results["priority_routes"] = add_segment_opportunity(results["priority_routes"])
    results["priority_categories"] = add_segment_opportunity(
        results["priority_categories"]
    )
    financial_scenarios = build_financial_scenarios(eligible, repeat_gap)
    results["financial_scenarios"] = financial_scenarios
    results["statistical_effects"] = statistical_effects

    for name, frame in results.items():
        frame.to_csv(OUTPUT_DIR / f"{name}.csv", index=False)

    overall = results["overall_kpis"].iloc[0]
    stages = results["stage_attribution"].set_index("stage_bucket")
    seller_involved = int(
        stages.loc["late_seller_stage_involved", "orders"]
        if "late_seller_stage_involved" in stages.index
        else 0
    )
    downstream_candidate = int(
        stages.loc["late_downstream_candidate", "orders"]
        if "late_downstream_candidate" in stages.index
        else 0
    )
    multi_seller_unclassified = int(
        stages.loc["late_unclassified_multi_seller", "orders"]
        if "late_unclassified_multi_seller" in stages.index
        else 0
    )
    classified_late = seller_involved + downstream_candidate

    review_distance = results["review_impact_by_distance"].pivot(
        index="distance_band", columns="delivery_group", values="avg_review_score"
    )
    review_distance["on_time_minus_late"] = (
        review_distance.get("on_time") - review_distance.get("late")
    )
    known_review_bands = review_distance.drop(index="unknown", errors="ignore")
    consistent_review_direction = bool(
        known_review_bands["on_time_minus_late"].dropna().gt(0).all()
    )

    repeat_distance = results["repeat_90d_by_distance"].pivot(
        index="distance_band", columns="delivery_group", values="repeat_90d_rate"
    )
    repeat_distance["on_time_minus_late"] = (
        repeat_distance.get("on_time") - repeat_distance.get("late")
    )
    known_repeat_bands = repeat_distance.drop(index="unknown", errors="ignore")
    positive_repeat_bands = int(
        known_repeat_bands["on_time_minus_late"].dropna().gt(0).sum()
    )
    total_repeat_bands = int(
        known_repeat_bands["on_time_minus_late"].dropna().shape[0]
    )

    top_sellers = results["priority_sellers"].head(10)
    top_routes = results["priority_routes"].head(10)
    top_categories = results["priority_categories"].head(10)

    spike_distance = results["spike_distance_decomposition"]
    spike_rows = spike_distance.loc[
        spike_distance["period_group"].eq("spike_months")
    ].copy()
    other_rows = spike_distance.loc[
        spike_distance["period_group"].eq("other_complete_months")
    ].copy()
    baseline_by_distance = other_rows.set_index("distance_band")["late_rate"]
    spike_rows["baseline_late_rate"] = spike_rows["distance_band"].map(
        baseline_by_distance
    )
    spike_rows["expected_late_at_band_baseline"] = (
        spike_rows["orders"] * spike_rows["baseline_late_rate"]
    )
    spike_actual_late = float(spike_rows["late_orders"].sum())
    spike_orders = float(spike_rows["orders"].sum())
    expected_late_with_spike_mix = float(
        spike_rows["expected_late_at_band_baseline"].sum()
    )
    other_baseline_rate = float(
        other_rows["late_orders"].sum() / other_rows["orders"].sum()
    )
    expected_late_without_mix = spike_orders * other_baseline_rate
    total_spike_excess = spike_actual_late - expected_late_without_mix
    distance_mix_effect = expected_late_with_spike_mix - expected_late_without_mix
    within_band_effect = spike_actual_late - expected_late_with_spike_mix
    within_band_share_of_spike_excess = (
        within_band_effect / total_spike_excess if total_spike_excess > 0 else np.nan
    )
    results["spike_distance_decomposition_detail"] = spike_rows
    spike_rows.to_csv(
        OUTPUT_DIR / "spike_distance_decomposition_detail.csv", index=False
    )

    spike_stage = results["spike_stage_mix"].pivot(
        index="period_group", columns="stage_bucket", values="late_orders"
    ).fillna(0)
    spike_seller_share = float(
        spike_stage.loc["spike_months", "seller_stage_involved"]
        / spike_stage.loc["spike_months"].sum()
    )
    other_seller_share = float(
        spike_stage.loc["other_complete_months", "seller_stage_involved"]
        / spike_stage.loc["other_complete_months"].sum()
    )

    top_seller_excess_share = (
        top_sellers["excess_late_orders"].sum() / overall["late_orders"]
    )
    top_route_excess_share = (
        top_routes["excess_late_orders"].sum() / overall["late_orders"]
    )

    validation_checks = pd.DataFrame(
        [
            {
                "check": "One fact row per raw order",
                "status": "PASS"
                if validation_base.raw_orders
                == validation_base.fact_rows
                == validation_base.distinct_fact_orders
                else "FAIL",
                "evidence": f"{int(validation_base.fact_rows)} fact rows / {int(validation_base.raw_orders)} raw orders",
            },
            {
                "check": "Order value reconciles to raw item value plus freight",
                "status": "PASS"
                if abs(validation_base.raw_order_value - validation_base.fact_order_value)
                < 0.01
                else "FAIL",
                "evidence": f"difference={validation_base.fact_order_value - validation_base.raw_order_value:.6f}",
            },
            {
                "check": "No negative stage durations in eligible population",
                "status": "PASS"
                if validation_base.invalid_stage_rows_in_eligible == 0
                else "FAIL",
                "evidence": f"{int(validation_base.invalid_stage_rows_in_eligible)} rows",
            },
            {
                "check": "Stage buckets reconcile to eligible population",
                "status": "PASS"
                if int(results["stage_attribution"]["orders"].sum())
                == int(overall["eligible_orders"])
                else "FAIL",
                "evidence": f"{int(results['stage_attribution']['orders'].sum())} / {int(overall['eligible_orders'])}",
            },
            {
                "check": "Review association direction holds in all known distance bands",
                "status": "PASS" if consistent_review_direction else "WARN",
                "evidence": f"{int(known_review_bands['on_time_minus_late'].gt(0).sum())}/{len(known_review_bands)} bands",
            },
            {
                "check": "Repeat association direction across distance bands",
                "status": "PASS"
                if positive_repeat_bands == total_repeat_bands
                else "WARN",
                "evidence": f"{positive_repeat_bands}/{total_repeat_bands} bands",
            },
        ]
    )
    validation_checks.to_csv(OUTPUT_DIR / "validation_checks.csv", index=False)

    summary = {
        "eligible_orders": int(overall["eligible_orders"]),
        "late_orders": int(overall["late_orders"]),
        "on_time_rate": float(overall["on_time_rate"]),
        "late_rate": float(overall["late_rate"]),
        "avg_late_days": float(overall["avg_late_days"]),
        "late_order_value": float(overall["late_order_value"]),
        "late_freight_value": float(overall["late_freight_value"]),
        "review_gap_on_time_minus_late": float(review_gap),
        "review_gap_ci_95": [float(review_ci_low), float(review_ci_high)],
        "low_review_rate_gap_late_minus_on_time": float(low_review_gap),
        "repeat_90d_gap_on_time_minus_late": float(repeat_gap),
        "repeat_90d_gap_ci_95": [float(repeat_ci_low), float(repeat_ci_high)],
        "seller_involved_late_orders": seller_involved,
        "downstream_candidate_late_orders": downstream_candidate,
        "multi_seller_unclassified_late_orders": multi_seller_unclassified,
        "seller_involved_share_classified_late": seller_involved / classified_late
        if classified_late
        else None,
        "top_10_seller_excess_share_of_all_late": float(top_seller_excess_share),
        "top_10_route_excess_share_of_all_late": float(top_route_excess_share),
        "review_gap_consistent_across_distance_bands": consistent_review_direction,
        "repeat_positive_distance_bands": positive_repeat_bands,
        "repeat_total_distance_bands": total_repeat_bands,
        "spike_actual_late_orders": int(spike_actual_late),
        "spike_expected_late_without_mix": float(expected_late_without_mix),
        "spike_distance_mix_effect_orders": float(distance_mix_effect),
        "spike_within_band_effect_orders": float(within_band_effect),
        "spike_within_band_share_of_excess": float(
            within_band_share_of_spike_excess
        ),
        "spike_seller_stage_share": spike_seller_share,
        "other_months_seller_stage_share": other_seller_share,
        "validation_status": "Share with caveats"
        if "FAIL" not in set(validation_checks["status"])
        else "Needs revision",
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    top_seller_lines = "\n".join(
        f"- `{row.primary_seller_id}` ({row.seller_state}): {int(row.orders):,} orders, "
        f"late rate {percentage(row.late_rate)}, excess late orders {row.excess_late_orders:.1f}."
        for row in top_sellers.head(5).itertuples()
    )
    top_route_lines = "\n".join(
        f"- `{row.route}`: {int(row.orders):,} orders, late rate "
        f"{percentage(row.late_rate)}, excess late orders {row.excess_late_orders:.1f}."
        for row in top_routes.head(5).itertuples()
    )
    top_category_lines = "\n".join(
        f"- `{row.primary_category}`: {int(row.orders):,} orders, late rate "
        f"{percentage(row.late_rate)}, excess late orders {row.excess_late_orders:.1f}."
        for row in top_categories.head(5).itertuples()
    )

    findings = f"""# Analysis findings

## Short answer

Of {int(overall['eligible_orders']):,} eligible delivered orders,
{int(overall['late_orders']):,} were late: {percentage(overall['late_rate'])}.
Late orders represented {money(overall['late_order_value'])} in order value, including
{money(overall['late_freight_value'])} in freight value. This is value exposed to a poor
customer experience, not proven financial loss.

## Stage localization

Among classifiable single-seller late orders:

- {seller_involved:,} were handed off after the seller shipping limit;
- {downstream_candidate:,} were handed off on time and are post-handoff candidates;
- {multi_seller_unclassified:,} multi-seller late orders remain unclassified because the
  order-level handoff date is ambiguous.

Seller-stage involvement accounts for
{percentage(seller_involved / classified_late if classified_late else 0)} of classified late orders.

## Time pattern

The three peak months—2017-11, 2018-02, and 2018-03—contain {int(spike_actual_late):,}
late orders. At the late rate of the other complete months, approximately
{expected_late_without_mix:,.0f} would be expected. Of the excess, only
{distance_mix_effect:,.0f} orders are explained by a change in distance mix, while
{within_band_effect:,.0f} are explained by higher late rates within distance bands.

Most of the spike above baseline is therefore associated with within-band deterioration,
not a larger share of long-distance deliveries. Seller-stage involvement was
{percentage(spike_seller_share)} in peak months versus {percentage(other_seller_share)}
in other complete months. The dataset does not contain the external event behind the spike,
so its cause remains a hypothesis for operational investigation.

## Customer impact

- The average review score for on-time orders is {review_gap:.2f} points higher than for late orders
  (95% CI {review_ci_low:.2f}–{review_ci_high:.2f}).
- The share of 1–2 star reviews is {percentage(low_review_gap)} higher for late orders
  (95% CI {percentage(low_ci_low)}–{percentage(low_ci_high)}).
- The review-score direction is consistent across all known distance bands:
  {'yes' if consistent_review_direction else 'no'}.

This is a consistent association, not a causal estimate.

## Repeat purchase

For orders with a complete 90-day observation window, the repeat-rate gap between
on-time and late orders is {percentage(repeat_gap)} (95% CI {percentage(repeat_ci_low)}–
{percentage(repeat_ci_high)}). The direction is positive in
{positive_repeat_bands} of {total_repeat_bands} distance bands.

Repeat purchase depends on other factors. Financial scenarios use 25%, 50%, and 100%
of the observed gap as sensitivity cases, not forecasts.

## Priority sellers

{top_seller_lines}

The top 10 sellers account for {percentage(top_seller_excess_share)} of all excess late orders.

## Priority routes

{top_route_lines}

The top 10 routes account for {percentage(top_route_excess_share)} of all excess late orders.

## Categories for additional monitoring

{top_category_lines}

Category is a diagnostic slice, not a process owner. The primary pilot should be assigned
by seller and route.

## Recommendation

1. Pilot sellers and routes with the highest excess late-order counts, not small segments
   with high rates alone.
2. For seller-stage delays, monitor handoff before the shipping limit and add an early alert.
3. For post-handoff delays, review route and carrier SLAs. Olist has no carrier ID, so a
   specific carrier cannot be identified.
4. Use OTD as the primary KPI; seller SLA breach and carrier-stage duration as diagnostic
   metrics; freight value and review score as guardrails.
5. Target at least a 20% relative reduction in late rate, then validate it through an
   experiment or quasi-experiment.

## Limitations

- No picking, packing, or sorting events.
- No carrier ID or realized compensation and return costs.
- Review and repeat-purchase results are observational and do not prove causality.
- Historical 2016–2018 data are not a current operating benchmark.
"""

    failed_checks = validation_checks.loc[validation_checks["status"].eq("FAIL")]
    warning_checks = validation_checks.loc[validation_checks["status"].eq("WARN")]
    validation = f"""# Validation report

## Overall assessment: {summary['validation_status']}

The methodology, grain, key totals, and denominators are reproducible. The findings are
suitable for a portfolio case and pilot selection when causal limitations remain visible.

## Calculation spot-checks

{markdown_table(validation_checks)}

## Issues and caveats

- **High:** financial impact is not directly observed; value at risk and repeat-GMV scenarios are not realized loss or guaranteed gain.
- **Medium:** seller-stage classification is limited to single-seller orders.
- **Medium:** no carrier ID is available, so post-handoff performance cannot be split by carrier.
- **Medium:** 90-day repeat purchase is an observational association with end-of-data censoring.
- **Low:** the historical period limits the transferability of an absolute benchmark.

## Share blockers

{'No calculation blockers.' if failed_checks.empty else markdown_table(failed_checks)}

## Warnings requiring visible disclosure

{'No additional warnings.' if warning_checks.empty else markdown_table(warning_checks)}
"""

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_analysis()
