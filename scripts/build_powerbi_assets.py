"""Export a compact Power BI model and source-backed dashboard mockups."""

from __future__ import annotations

import sqlite3
from html import escape
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_DIR / "data" / "processed" / "olist_supply_chain.sqlite"
OUTPUT_DIR = PROJECT_DIR / "data" / "powerbi"
MOCKUP_DIR = PROJECT_DIR / "powerbi" / "mockups"


def export_csv(frame: pd.DataFrame, filename: str) -> None:
    """Use UTF-8 with BOM so Russian labels also import cleanly in Power BI."""
    frame.to_csv(OUTPUT_DIR / filename, index=False, encoding="utf-8-sig")


def build_model() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        fact = pd.read_sql_query(
            "SELECT * FROM fact_order_analysis WHERE analysis_eligible = 1",
            connection,
        )

    fact["purchase_date"] = pd.to_datetime(
        fact["order_purchase_timestamp"], errors="coerce"
    ).dt.date
    fact["delivery_date"] = pd.to_datetime(
        fact["order_delivered_customer_date"], errors="coerce"
    ).dt.date
    fact["estimated_delivery_date"] = pd.to_datetime(
        fact["order_estimated_delivery_date"], errors="coerce"
    ).dt.date
    fact["on_time_flag"] = 1 - fact["late_flag"]
    fact["delivery_group"] = fact["late_flag"].map({0: "on_time", 1: "late"})
    fact["stage_bucket"] = "on_time"
    late = fact["late_flag"].eq(1)
    multi = fact["seller_count"].ne(1)
    seller = fact["seller_handoff_after_limit_flag"].eq(1)
    fact.loc[late & multi, "stage_bucket"] = "late_unclassified_multi_seller"
    fact.loc[late & ~multi & seller, "stage_bucket"] = "late_seller_stage_involved"
    fact.loc[late & ~multi & ~seller, "stage_bucket"] = "late_downstream_candidate"
    fact["reviewed_flag"] = fact["review_score"].notna().astype(int)
    fact["low_review_flag"] = (
        fact["review_score"].notna() & fact["review_score"].le(2)
    ).astype(int)
    fact["five_star_flag"] = fact["review_score"].eq(5).astype(int)
    fact["route_key"] = (
        fact["seller_state"].fillna("unknown")
        + " -> "
        + fact["customer_state"].fillna("unknown")
    )
    fact["primary_category"] = fact["primary_category"].fillna("unknown")
    fact["primary_seller_id"] = fact["primary_seller_id"].fillna("unknown")
    fact["distance_band"] = fact["distance_band"].fillna("unknown")

    fact_columns = [
        "order_id",
        "purchase_date",
        "delivery_date",
        "estimated_delivery_date",
        "primary_seller_id",
        "route_key",
        "primary_category",
        "distance_band",
        "late_flag",
        "on_time_flag",
        "delivery_group",
        "stage_bucket",
        "seller_count",
        "seller_handoff_after_limit_flag",
        "seller_stage_days",
        "carrier_stage_days",
        "total_cycle_days",
        "days_vs_promise",
        "distance_km",
        "item_value",
        "freight_value",
        "order_value",
        "review_score",
        "reviewed_flag",
        "low_review_flag",
        "five_star_flag",
        "repeat_90d_eligible",
        "repeat_90d_flag",
        "days_to_next_purchase",
        "next_order_value",
    ]
    fact_orders = fact[fact_columns].copy()

    date_start = pd.Timestamp(fact_orders["purchase_date"].min())
    date_end = pd.Timestamp(fact_orders["purchase_date"].max())
    dates = pd.date_range(date_start, date_end, freq="D")
    dim_date = pd.DataFrame({"date": dates.date})
    dim_date["year"] = dates.year
    dim_date["quarter"] = "Q" + dates.quarter.astype(str)
    dim_date["month_number"] = dates.month
    dim_date["month_name"] = dates.strftime("%b")
    dim_date["year_month"] = dates.strftime("%Y-%m")
    dim_date["year_month_sort"] = dates.year * 100 + dates.month

    dim_seller = (
        fact[["primary_seller_id", "seller_state"]]
        .fillna({"seller_state": "unknown"})
        .drop_duplicates("primary_seller_id")
        .sort_values("primary_seller_id")
    )
    dim_route = (
        fact[["route_key", "seller_state", "customer_state"]]
        .fillna({"seller_state": "unknown", "customer_state": "unknown"})
        .drop_duplicates("route_key")
        .sort_values("route_key")
    )
    dim_category = (
        fact[["primary_category"]]
        .drop_duplicates()
        .rename(columns={"primary_category": "category_key"})
        .sort_values("category_key")
    )
    dim_distance_band = pd.DataFrame(
        {
            "distance_band": [
                "<250 km",
                "250-749 km",
                "750-1499 km",
                "1500+ km",
                "unknown",
            ],
            "distance_band_sort": [1, 2, 3, 4, 5],
        }
    )
    scenarios = pd.read_csv(PROJECT_DIR / "outputs" / "tables" / "financial_scenarios.csv")

    tables = {
        "dim_date.csv": dim_date,
        "dim_seller.csv": dim_seller,
        "dim_route.csv": dim_route,
        "dim_category.csv": dim_category,
        "dim_distance_band.csv": dim_distance_band,
        "scenario_assumptions.csv": scenarios,
    }
    return fact_orders, tables


def card(x: int, y: int, title: str, value: str, note: str = "") -> str:
    return f"""
    <g transform="translate({x} {y})">
      <rect width="238" height="110" rx="12" fill="#ffffff" stroke="#dfe5ec"/>
      <text x="18" y="28" class="small">{escape(title)}</text>
      <text x="18" y="66" class="value">{escape(value)}</text>
      <text x="18" y="91" class="note">{escape(note)}</text>
    </g>"""


def panel(x: int, y: int, w: int, h: int, title: str, subtitle: str) -> str:
    return f"""
    <g transform="translate({x} {y})">
      <rect width="{w}" height="{h}" rx="12" fill="#ffffff" stroke="#dfe5ec"/>
      <text x="20" y="29" class="panel-title">{escape(title)}</text>
      <text x="20" y="49" class="note">{escape(subtitle)}</text>
    </g>"""


def svg_shell(title: str, question: str, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900">
  <style>
    text {{ font-family: 'Segoe UI', Arial, sans-serif; fill: #17212b; }}
    .title {{ font-size: 26px; font-weight: 700; }}
    .panel-title {{ font-size: 16px; font-weight: 650; }}
    .small {{ font-size: 13px; fill: #5d6b78; }}
    .note {{ font-size: 11px; fill: #71808e; }}
    .value {{ font-size: 28px; font-weight: 700; }}
    .axis {{ stroke: #ccd5df; stroke-width: 1; }}
    .blue {{ fill: #2f6fed; }}
    .blue-line {{ fill: none; stroke: #2f6fed; stroke-width: 3; }}
    .orange {{ fill: #e8923a; }}
    .gold {{ fill: #d3a72e; }}
    .olive {{ fill: #7d9142; }}
  </style>
  <rect width="1440" height="900" fill="#f5f7fa"/>
  <text x="36" y="44" class="title">{escape(title)}</text>
  <text x="36" y="68" class="small">Question: {escape(question)}</text>
  <rect x="1180" y="27" width="220" height="38" rx="8" fill="#ffffff" stroke="#dfe5ec"/>
  <text x="1200" y="51" class="small">Filters: date · route · seller</text>
  <text x="36" y="884" class="note">Source: Olist, fact_order_analysis · delivered orders · historical data</text>
  {body}
</svg>"""


def build_mockups(fact: pd.DataFrame) -> None:
    monthly = (
        fact.assign(month=pd.to_datetime(fact["purchase_date"]).dt.to_period("M").astype(str))
        .loc[lambda frame: frame["month"].between("2017-01", "2018-07")]
        .groupby("month", as_index=False)
        .agg(orders=("order_id", "count"), late_rate=("late_flag", "mean"))
    )
    late_orders = int(fact["late_flag"].sum())
    total_orders = len(fact)
    otd = 1 - fact["late_flag"].mean()
    avg_late = fact.loc[fact["late_flag"].eq(1), "days_vs_promise"].mean()
    late_value = fact.loc[fact["late_flag"].eq(1), "order_value"].sum()

    points = []
    for idx, row in monthly.iterrows():
        x = 90 + idx * (820 / max(len(monthly) - 1, 1))
        y = 470 - float(row["late_rate"]) * 680
        points.append(f"{x:.1f},{y:.1f}")

    month_labels = []
    for idx, row in monthly.iterrows():
        if idx % 2 == 0 or idx == len(monthly) - 1:
            x = 90 + idx * (820 / max(len(monthly) - 1, 1))
            label = pd.Timestamp(row["month"] + "-01").strftime("%m.%y")
            month_labels.append(
                f'<text x="{x:.1f}" y="505" text-anchor="middle" class="note">{label}</text>'
            )
    rate_grid = []
    for rate in [0.00, 0.05, 0.10, 0.15, 0.20, 0.25]:
        y = 470 - rate * 680
        rate_grid.append(
            f'<line x1="90" y1="{y:.1f}" x2="910" y2="{y:.1f}" class="axis"/>'
            f'<text x="78" y="{y + 4:.1f}" text-anchor="end" class="note">{rate:.0%}</text>'
        )

    stage = (
        fact.loc[fact["late_flag"].eq(1)]
        .groupby("stage_bucket", as_index=False)
        .size()
        .sort_values("size", ascending=False)
    )
    stage_bars = []
    for idx, row in stage.iterrows():
        y = 630 + idx * 58
        width = 330 * row["size"] / stage["size"].max()
        label = {
            "late_downstream_candidate": "Post-handoff",
            "late_seller_stage_involved": "Seller stage",
            "late_unclassified_multi_seller": "Unclassified",
        }.get(row["stage_bucket"], row["stage_bucket"])
        stage_bars.append(
            f'<text x="56" y="{y + 18}" class="small">{escape(label)}</text>'
            f'<rect x="250" y="{y}" width="{width:.1f}" height="24" rx="4" class="blue"/>'
            f'<text x="{260 + width:.1f}" y="{y + 18}" class="small">{f"{int(row['size']):,}".replace(",", " ")}</text>'
        )

    overview = "".join(
        [
            card(36, 96, "On-time delivery (OTD)", f"{otd:.1%}".replace(".", ","), "Pilot target: 93.5%"),
            card(292, 96, "Late orders", f"{late_orders:,}".replace(",", " "), f"{late_orders / total_orders:.1%}".replace(".", ",") + " of analyzed orders"),
            card(548, 96, "Average delay, days", f"{avg_late:.1f}".replace(".", ","), "Late orders only"),
            card(804, 96, "Value of late orders", (f"R$ {late_value / 1_000_000:.2f}M").replace(".", ","), "Value at risk, not realized loss"),
            panel(36, 230, 900, 300, "Monthly late rate", "Monthly: January 2017 — July 2018"),
            "".join(rate_grid),
            '<line x1="90" y1="425.8" x2="910" y2="425.8" stroke="#6f7c89" stroke-width="1.5" stroke-dasharray="6 5"/>',
            '<text x="902" y="420" text-anchor="end" class="note">Target: 6.5%</text>',
            f'<polyline points="{" ".join(points)}" class="blue-line"/>',
            "".join(month_labels),
            panel(960, 230, 440, 300, "Routes with excess late orders", "At least 150 orders; descending"),
            '<rect x="990" y="304" width="300" height="28" rx="4" class="blue"/><text x="1000" y="323" fill="#ffffff" font-size="12">SP → RJ · 603</text>',
            '<rect x="990" y="350" width="79" height="28" rx="4" class="blue"/><text x="1000" y="369" fill="#ffffff" font-size="12">SP → BA · 158</text>',
            '<rect x="990" y="396" width="45" height="28" rx="4" class="blue"/><text x="1000" y="415" fill="#ffffff" font-size="12">SP → ES · 89</text>',
            panel(36, 552, 690, 300, "Where the delay may have occurred", "Diagnostic classification, not a proven cause"),
            "".join(stage_bars),
            panel(750, 552, 650, 300, "Key takeaway", "What the overview shows"),
            '<text x="780" y="640" class="panel-title">8.1% of orders were delivered late.</text>',
            '<text x="780" y="690" class="small">Peaks: November 2017 and February–March 2018.</text>',
            '<text x="780" y="740" class="small">First investigation priority: SP → RJ route.</text>',
            '<text x="780" y="790" class="small">Main direction: the post-handoff stage.</text>',
        ]
    )
    (MOCKUP_DIR / "page_1_executive_overview.svg").write_text(
        svg_shell(
            "Delivery performance",
            "How large is the delay problem, when did it intensify, and where should the investigation start?",
            overview,
        ),
        encoding="utf-8",
    )

    distance = (
        fact.groupby("distance_band", as_index=False)
        .agg(orders=("order_id", "count"), late_rate=("late_flag", "mean"))
    )
    distance_order = {
        "<250 km": 1,
        "250-749 km": 2,
        "750-1499 km": 3,
        "1500+ km": 4,
        "unknown": 5,
    }
    distance["sort_order"] = distance["distance_band"].map(distance_order)
    distance = distance.sort_values("sort_order").reset_index(drop=True)
    route_stats = (
        fact.groupby("route_key", as_index=False)
        .agg(
            orders=("order_id", "count"),
            late_orders=("late_flag", "sum"),
            late_rate=("late_flag", "mean"),
        )
    )
    baseline_late_rate = fact["late_flag"].mean()
    route_stats["excess_late_orders"] = (
        route_stats["late_orders"] - route_stats["orders"] * baseline_late_rate
    ).clip(lower=0)
    route_stats = (
        route_stats.loc[route_stats["orders"].ge(150)]
        .nlargest(15, "excess_late_orders")
        .reset_index(drop=True)
    )
    max_orders = route_stats["orders"].max()
    max_late = route_stats["late_orders"].max()
    max_rate = max(route_stats["late_rate"].max(), 0.20)
    route_bubbles = []
    for idx, row in route_stats.iterrows():
        x = 790 + (float(row["orders"]) / max_orders) ** 0.5 * 520
        y = 510 - float(row["late_rate"]) / max_rate * 180
        radius = 7 + (float(row["late_orders"]) / max_late) ** 0.5 * 24
        if idx == 0:
            fill = "#2f6fed"
        elif idx < 3:
            fill = "#e8923a"
        else:
            fill = "#b8c3cf"
        route_bubbles.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{fill}" fill-opacity="0.78"/>'
        )
        if idx < 3:
            route_bubbles.append(
                f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" font-size="11">{escape(row["route_key"])}</text>'
            )
    distance_bars = []
    for idx, row in distance.iterrows():
        y = 322 + idx * 54
        width = 330 * row["late_rate"] / distance["late_rate"].max()
        distance_label = str(row["distance_band"]).replace("km", "km").replace("unknown", "unknown")
        distance_bars.append(
            f'<text x="64" y="{y + 18}" class="small">{escape(distance_label)}</text>'
            f'<rect x="190" y="{y}" width="{width:.1f}" height="24" rx="4" class="orange"/>'
            f'<text x="{200 + width:.1f}" y="{y + 18}" class="small">{f"{row['late_rate']:.1%}".replace(".", ",")}</text>'
        )
    drivers = "".join(
        [
            card(36, 96, "Post-handoff", "5 680", "72.8% of classified late orders"),
            card(292, 96, "Seller stage", "2 124", "27.2% of classified late orders"),
            card(548, 96, "Late orders in peak months", "3 587", "Nov 2017, Feb–Mar 2018"),
            card(804, 96, "Top-10 route share", "15,6%", "Of excess late orders"),
            panel(36, 230, 650, 340, "Late rate by distance", "Compare late rate and order volume together"),
            "".join(distance_bars),
            panel(710, 230, 690, 340, "Priority-route matrix", "X: order count · Y: late rate · label: route"),
            '<line x1="770" y1="520" x2="1350" y2="520" class="axis"/>',
            '<line x1="770" y1="300" x2="770" y2="520" class="axis"/>',
            "".join(route_bubbles),
            panel(36, 594, 1364, 258, "How to prioritize", "Route, seller, category, stage, and distance filters affect all charts"),
            '<text x="66" y="675" class="small">Rule: sufficient volume + above-baseline late rate + many excess late orders.</text>',
            '<text x="66" y="720" class="small">Limitation: Olist has no carrier scans, warehouse events, traffic, or weather data.</text>',
            '<text x="66" y="765" class="small">The post-handoff stage is therefore an investigation direction, not a proven root cause.</text>',
        ]
    )
    (MOCKUP_DIR / "page_2_driver_diagnostics.svg").write_text(
        svg_shell(
            "Delay-driver diagnostics",
            "At which stage and in which segments are delays most concentrated?",
            drivers,
        ),
        encoding="utf-8",
    )

    reviewed = fact.loc[fact["review_score"].notna()]
    review_stats = reviewed.groupby("delivery_group").agg(
        avg_review=("review_score", "mean"), low_rate=("low_review_flag", "mean")
    )
    repeat = fact.loc[fact["repeat_90d_eligible"].eq(1)].groupby("delivery_group")[
        "repeat_90d_flag"
    ].mean()
    impact = "".join(
        [
            card(36, 96, "Average review score gap", f"{review_stats.loc['on_time', 'avg_review'] - review_stats.loc['late', 'avg_review']:.2f}".replace(".", ","), "On time minus late"),
            card(292, 96, "Low-review-rate gap", f"{review_stats.loc['late', 'low_rate'] - review_stats.loc['on_time', 'low_rate']:.1%}".replace(".", ","), "Late minus on time"),
            card(548, 96, "90-day repeat-rate gap", f"{repeat['on_time'] - repeat['late']:.3%}".replace(".", ","), "Supporting signal only"),
            card(804, 96, "Scenario: 20% fewer late orders", "R$ 270K", "Protected order value"),
            panel(36, 230, 650, 300, "Average review score", "Reviewed orders: on time vs late"),
            '<rect x="110" y="325" width="180" height="150" class="blue"/><text x="165" y="310" class="value">4,29</text><text x="160" y="500" class="small">On time</text>',
            '<rect x="375" y="385" width="180" height="90" class="orange"/><text x="430" y="370" class="value">2,57</text><text x="415" y="500" class="small">Late</text>',
            panel(710, 230, 690, 300, "Share of low reviews (1–2 stars)", "Reviewed orders; consistent denominator"),
            '<rect x="800" y="440" width="180" height="35" class="blue"/><text x="855" y="425" class="value">9,2%</text><text x="850" y="500" class="small">On time</text>',
            '<rect x="1060" y="300" width="180" height="175" class="orange"/><text x="1107" y="285" class="value">54,0%</text><text x="1100" y="500" class="small">Late</text>',
            panel(36, 552, 1364, 300, "Improvement scenarios", "Sensitivity estimate, not a causal financial forecast"),
            '<text x="72" y="630" class="panel-title">Late-rate reduction</text><text x="360" y="630" class="panel-title">Prevented late orders</text><text x="650" y="630" class="panel-title">Protected order value</text>',
            '<text x="72" y="685" class="small">10%</text><text x="360" y="685" class="small">782</text><text x="650" y="685" class="small">R$ 135 118</text>',
            '<text x="72" y="730" class="small">20%</text><text x="360" y="730" class="small">1 564</text><text x="650" y="730" class="small">R$ 270 237</text>',
            '<text x="72" y="775" class="small">30%</text><text x="360" y="775" class="small">2 347</text><text x="650" y="775" class="small">R$ 405 355</text>',
        ]
    )
    (MOCKUP_DIR / "page_3_customer_financial_impact.svg").write_text(
        svg_shell(
            "Customer and business impact",
            "How are delays associated with reviews, and what scale could improvement deliver?",
            impact,
        ),
        encoding="utf-8",
    )


def validate(fact: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> None:
    checks = {
        "fact_rows": len(fact),
        "distinct_orders": fact["order_id"].nunique(),
        "late_orders": int(fact["late_flag"].sum()),
        "otd": float(fact["on_time_flag"].mean()),
        "order_value": float(fact["order_value"].sum()),
        "date_keys_unique": tables["dim_date.csv"]["date"].is_unique,
        "seller_keys_unique": tables["dim_seller.csv"]["primary_seller_id"].is_unique,
        "route_keys_unique": tables["dim_route.csv"]["route_key"].is_unique,
        "category_keys_unique": tables["dim_category.csv"]["category_key"].is_unique,
    }
    expected = {
        "fact_rows": 96281,
        "distinct_orders": 96281,
        "late_orders": 7822,
    }
    for key, value in expected.items():
        if checks[key] != value:
            raise ValueError(f"Power BI export failed reconciliation: {key}")
    if abs(checks["otd"] - 0.9187586335829498) > 1e-12:
        raise ValueError("Power BI export failed OTD reconciliation")
    if abs(checks["order_value"] - 15390683.77) > 0.01:
        raise ValueError("Power BI export failed order-value reconciliation")
    if not all(
        checks[name]
        for name in [
            "date_keys_unique",
            "seller_keys_unique",
            "route_keys_unique",
            "category_keys_unique",
        ]
    ):
        raise ValueError("Power BI export contains duplicate dimension keys")
    pd.DataFrame([checks]).to_csv(
        OUTPUT_DIR / "model_validation.csv", index=False, encoding="utf-8-sig"
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MOCKUP_DIR.mkdir(parents=True, exist_ok=True)
    fact, tables = build_model()
    export_csv(fact, "fact_orders.csv")
    for filename, frame in tables.items():
        export_csv(frame, filename)
    validate(fact, tables)
    build_mockups(fact)
    print(f"Rows in the Power BI fact table: {len(fact):,}")
    print(f"Late orders: {int(fact['late_flag'].sum()):,}")
    print(f"OTD: {fact['on_time_flag'].mean():.3%}")
    print(f"Tables: {OUTPUT_DIR}")
    print(f"Mockups: {MOCKUP_DIR}")


if __name__ == "__main__":
    main()
