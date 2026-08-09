"""Build a reproducible SQLite analytical database from the original Olist CSVs."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_DIR / "data" / "raw" / "olist"
PROCESSED_DIR = PROJECT_DIR / "data" / "processed"
DATABASE_PATH = PROCESSED_DIR / "olist_supply_chain.sqlite"
VIEW_SQL_PATH = PROJECT_DIR / "sql" / "01_create_views.sql"


CSV_TABLES = {
    "raw_customers": "olist_customers_dataset.csv",
    "raw_geolocation": "olist_geolocation_dataset.csv",
    "raw_order_items": "olist_order_items_dataset.csv",
    "raw_order_payments": "olist_order_payments_dataset.csv",
    "raw_order_reviews": "olist_order_reviews_dataset.csv",
    "raw_orders": "olist_orders_dataset.csv",
    "raw_products": "olist_products_dataset.csv",
    "raw_sellers": "olist_sellers_dataset.csv",
    "raw_category_translation": "product_category_name_translation.csv",
}


DATE_COLUMNS = {
    "raw_order_items": ["shipping_limit_date"],
    "raw_order_reviews": ["review_creation_date", "review_answer_timestamp"],
    "raw_orders": [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
}


def normalize_dates(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Store timestamps in SQLite-friendly ISO format while preserving nulls."""
    result = frame.copy()
    for column in columns:
        parsed = pd.to_datetime(result[column], errors="coerce")
        result[column] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S")
    return result


def build_geo_dimension(geolocation: pd.DataFrame) -> pd.DataFrame:
    """Remove exact duplicates and create one robust coordinate per zip prefix."""
    deduplicated = geolocation.drop_duplicates().copy()

    def first_mode(series: pd.Series):
        mode = series.dropna().mode()
        return mode.iloc[0] if not mode.empty else None

    return (
        deduplicated.groupby("geolocation_zip_code_prefix", as_index=False)
        .agg(
            latitude=("geolocation_lat", "median"),
            longitude=("geolocation_lng", "median"),
            state=("geolocation_state", first_mode),
        )
        .rename(columns={"geolocation_zip_code_prefix": "zip_code_prefix"})
    )


def haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized great-circle distance; null coordinates remain null."""
    lat1 = np.radians(pd.to_numeric(lat1, errors="coerce"))
    lon1 = np.radians(pd.to_numeric(lon1, errors="coerce"))
    lat2 = np.radians(pd.to_numeric(lat2, errors="coerce"))
    lon2 = np.radians(pd.to_numeric(lon2, errors="coerce"))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = (
        np.sin(delta_lat / 2.0) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(delta_lon / 2.0) ** 2
    )
    return 6371.0088 * 2 * np.arcsin(np.sqrt(a))


def add_repeat_purchase_fields(frame: pd.DataFrame) -> pd.DataFrame:
    """Find the first purchase by the same customer after each delivery."""
    result = frame.copy()
    result["purchase_ts"] = pd.to_datetime(
        result["order_purchase_timestamp"], errors="coerce"
    )
    result["delivery_ts"] = pd.to_datetime(
        result["order_delivered_customer_date"], errors="coerce"
    )
    result["next_purchase_after_delivery"] = pd.NaT
    result["next_order_value"] = np.nan

    for _, group in result.loc[result["customer_unique_id"].notna()].groupby(
        "customer_unique_id", sort=False
    ):
        ordered = group.sort_values(["purchase_ts", "order_id"])
        purchases = ordered["purchase_ts"].to_numpy(dtype="datetime64[ns]")
        order_values = ordered["order_value"].to_numpy(dtype=float)
        positions = ordered.index.to_numpy()

        for row_index in positions:
            delivery = result.at[row_index, "delivery_ts"]
            if pd.isna(delivery):
                continue
            next_position = np.searchsorted(
                purchases, np.datetime64(delivery), side="right"
            )
            if next_position < len(purchases):
                result.at[row_index, "next_purchase_after_delivery"] = pd.Timestamp(
                    purchases[next_position]
                )
                result.at[row_index, "next_order_value"] = order_values[next_position]

    observation_end = result["purchase_ts"].max()
    result["repeat_90d_eligible"] = (
        result["delivery_ts"].notna()
        & (result["delivery_ts"] <= observation_end - pd.Timedelta(days=90))
    ).astype(int)
    days_to_next = (
        result["next_purchase_after_delivery"] - result["delivery_ts"]
    ).dt.total_seconds() / 86400
    result["days_to_next_purchase"] = days_to_next
    result["repeat_90d_flag"] = (
        result["repeat_90d_eligible"].eq(1)
        & days_to_next.gt(0)
        & days_to_next.le(90)
    ).astype(int)
    result["observation_end_date"] = observation_end.strftime("%Y-%m-%d")
    return result.drop(columns=["purchase_ts", "delivery_ts"])


def create_indexes(connection: sqlite3.Connection):
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_orders_order_id
            ON raw_orders(order_id);
        CREATE INDEX IF NOT EXISTS idx_orders_customer_id
            ON raw_orders(customer_id);
        CREATE INDEX IF NOT EXISTS idx_items_order_id
            ON raw_order_items(order_id);
        CREATE INDEX IF NOT EXISTS idx_items_product_id
            ON raw_order_items(product_id);
        CREATE INDEX IF NOT EXISTS idx_items_seller_id
            ON raw_order_items(seller_id);
        CREATE INDEX IF NOT EXISTS idx_payments_order_id
            ON raw_order_payments(order_id);
        CREATE INDEX IF NOT EXISTS idx_reviews_order_id
            ON raw_order_reviews(order_id);
        CREATE INDEX IF NOT EXISTS idx_customers_customer_id
            ON raw_customers(customer_id);
        CREATE INDEX IF NOT EXISTS idx_customers_unique_id
            ON raw_customers(customer_unique_id);
        CREATE INDEX IF NOT EXISTS idx_products_product_id
            ON raw_products(product_id);
        CREATE INDEX IF NOT EXISTS idx_sellers_seller_id
            ON raw_sellers(seller_id);
        CREATE INDEX IF NOT EXISTS idx_geo_zip
            ON dim_geo_zip(zip_code_prefix);
        """
    )


def build_database():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()

    loaded_tables = {}
    with sqlite3.connect(DATABASE_PATH) as connection:
        for table_name, filename in CSV_TABLES.items():
            frame = pd.read_csv(RAW_DIR / filename)
            if table_name in DATE_COLUMNS:
                frame = normalize_dates(frame, DATE_COLUMNS[table_name])
            frame.to_sql(table_name, connection, index=False, if_exists="replace")
            loaded_tables[table_name] = len(frame)

        geo_dimension = build_geo_dimension(
            pd.read_csv(RAW_DIR / CSV_TABLES["raw_geolocation"])
        )
        geo_dimension.to_sql(
            "dim_geo_zip", connection, index=False, if_exists="replace"
        )
        create_indexes(connection)
        connection.executescript(VIEW_SQL_PATH.read_text(encoding="utf-8"))

        analysis = pd.read_sql_query("SELECT * FROM vw_orders_enriched", connection)
        analysis["distance_km"] = haversine_km(
            analysis["seller_lat"],
            analysis["seller_lng"],
            analysis["customer_lat"],
            analysis["customer_lng"],
        )
        analysis["distance_band"] = pd.cut(
            analysis["distance_km"],
            bins=[-math.inf, 250, 750, 1500, math.inf],
            labels=["<250 km", "250-749 km", "750-1499 km", "1500+ km"],
            right=False,
        ).astype("string")
        analysis["distance_band"] = analysis["distance_band"].fillna("unknown")
        analysis = add_repeat_purchase_fields(analysis)
        analysis.to_sql(
            "fact_order_analysis", connection, index=False, if_exists="replace"
        )
        connection.executescript(
            """
            CREATE UNIQUE INDEX idx_fact_order_id
                ON fact_order_analysis(order_id);
            CREATE INDEX idx_fact_analysis_eligible
                ON fact_order_analysis(analysis_eligible);
            CREATE INDEX idx_fact_late_flag
                ON fact_order_analysis(late_flag);
            CREATE INDEX idx_fact_customer_unique
                ON fact_order_analysis(customer_unique_id);
            CREATE INDEX idx_fact_primary_seller
                ON fact_order_analysis(primary_seller_id);
            CREATE INDEX idx_fact_route
                ON fact_order_analysis(seller_state, customer_state);
            """
        )

        fact_rows = connection.execute(
            "SELECT COUNT(*) FROM fact_order_analysis"
        ).fetchone()[0]
        distinct_orders = connection.execute(
            "SELECT COUNT(DISTINCT order_id) FROM fact_order_analysis"
        ).fetchone()[0]

    print(f"Database: {DATABASE_PATH}")
    print(f"Raw tables: {loaded_tables}")
    print(f"Geo zip rows: {len(geo_dimension)}")
    print(f"Fact rows / distinct orders: {fact_rows} / {distinct_orders}")


if __name__ == "__main__":
    build_database()
