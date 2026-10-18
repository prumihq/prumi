"""Generate the demo shop's raw data into the `raw` schema of a DuckDB file.

    python generate.py [--db PATH]

Deterministic: one seeded random generator, so every run writes the same tables. Only the
standard library and `duckdb` are needed. The dbt models in `models/` build on these tables.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import math
import random
import tempfile
from collections import defaultdict
from pathlib import Path

import duckdb

HERE = Path(__file__).parent
DEFAULT_DB = HERE / "demo.duckdb"
SEED = 20261015

FIRST_DAY = dt.date(2024, 1, 1)
LAST_DAY = dt.date(2026, 10, 14)  # the demo's as_of is 2026-10-15; data ends the day before

N_CUSTOMERS = 3000
NULL_REGION_SHARE = 0.02  # customers whose region mapping is missing

# region -> (weight, countries). "Côte d'Ivoire" has a quote on purpose: filter values with
# a `'` must be escaped (spec section 4).
REGIONS = {
    "North America": (30, ["United States", "Canada"]),
    "Western Europe": (20, ["France", "Germany", "Netherlands", "Belgium"]),
    "Southern Europe": (12, ["Spain", "Italy", "Portugal"]),
    "Asia Pacific": (12, ["Japan", "Australia", "Singapore"]),
    "Northern Europe": (10, ["Sweden", "Denmark", "Norway", "Finland"]),
    "Latin America": (9, ["Brazil", "Mexico", "Argentina"]),
    "Middle East & Africa": (7, ["South Africa", "Côte d'Ivoire", "United Arab Emirates"]),
}
SEGMENTS = {"Consumer": 80, "Small business": 15, "Enterprise": 5}
CHANNELS = {"web": 55, "store": 30, "marketplace": 15}

# category -> (cost range, product names). Six categories of ten products.
CATEGORIES = {
    "Outdoor": (
        (12, 140),
        [
            "Trail Backpack",
            "Camping Stove",
            "Trekking Poles",
            "Sleeping Bag",
            "Rain Shell",
            "Headlamp",
            "Water Filter",
            "Camp Chair",
            "Dome Tent",
            "Hiking Socks",
        ],
    ),
    "Kitchen": (
        (4, 90),
        [
            "Chef Knife",
            "Cast Iron Pan",
            "Pepper Mill",
            "Cutting Board",
            "Kettle",
            "Espresso Cups",
            "Mixing Bowls",
            "Spice Rack",
            "Dutch Oven",
            "Tea Towels",
        ],
    ),
    "Electronics": (
        (10, 180),
        [
            "Wireless Earbuds",
            "Desk Lamp",
            "Power Bank",
            "Smart Plug",
            "Keyboard",
            "Webcam",
            "USB Hub",
            "Speaker",
            "E-Reader",
            "Phone Stand",
        ],
    ),
    "Apparel": (
        (5, 70),
        [
            "Wool Sweater",
            "Rain Boots",
            "Linen Shirt",
            "Beanie",
            "Denim Jacket",
            "Running Shorts",
            "Scarf",
            "Sneakers",
            "Gloves",
            "Cap",
        ],
    ),
    "Books": (
        (3, 25),
        [
            "Field Guide",
            "Cookbook",
            "Atlas",
            "Notebook",
            "Novel",
            "Sketchbook",
            "Poetry",
            "Travel Guide",
            "Planner",
            "Puzzle Book",
        ],
    ),
    "Toys": (
        (3, 60),
        [
            "Building Blocks",
            "Puzzle",
            "Kite",
            "Board Game",
            "Plush Bear",
            "Wooden Train",
            "Yo-Yo",
            "Card Game",
            "Marbles",
            "Paint Set",
        ],
    ),
}

# Orders per day: base * growth * weekday * season * noise. Sundays get far fewer orders.
BASE_ORDERS_PER_DAY = 55
YEARLY_GROWTH = 0.45
WEEKDAY_FACTOR = [1.0, 1.0, 1.05, 1.05, 1.15, 1.1, 0.55]  # Monday .. Sunday
PRICE_INCREASE_DATE = dt.date(2025, 7, 1)  # list prices go up 5%
DISCOUNTS = {0.0: 70, 0.05: 12, 0.10: 12, 0.20: 6}


def _weighted(rng: random.Random, weights: dict) -> object:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _days() -> list[dt.date]:
    return [FIRST_DAY + dt.timedelta(days=i) for i in range((LAST_DAY - FIRST_DAY).days + 1)]


def generate_customers(rng: random.Random) -> list[tuple]:
    # A fifth of the customers signed up before the first order day; the rest during the period.
    early = [dt.date(2023, 1, 1) + dt.timedelta(days=rng.randrange(365)) for _ in range(600)]
    late_span = (dt.date(2026, 9, 30) - FIRST_DAY).days
    late = [
        FIRST_DAY + dt.timedelta(days=int(late_span * rng.random() ** 0.8))
        for _ in range(N_CUSTOMERS - len(early))
    ]
    region_names = list(REGIONS)
    region_weights = [REGIONS[r][0] for r in region_names]
    customers = []
    for customer_id, signup in enumerate(sorted(early + late), start=1):
        region = rng.choices(region_names, weights=region_weights)[0]
        country = rng.choice(REGIONS[region][1])
        if rng.random() < NULL_REGION_SHARE:
            region = None
        segment = _weighted(rng, SEGMENTS)
        customers.append((customer_id, signup, country, region, segment))
    return customers


def generate_products(rng: random.Random) -> tuple[list[tuple], dict[int, float]]:
    products = []
    list_prices = {}
    product_id = 0
    for category, ((low, high), names) in CATEGORIES.items():
        for name in names:
            product_id += 1
            unit_cost = round(rng.uniform(low, high), 2)
            list_prices[product_id] = math.floor(unit_cost * rng.uniform(1.4, 2.2)) + 0.99
            products.append((product_id, name, category, unit_cost))
    return products, list_prices


def generate_orders(
    rng: random.Random, customers: list[tuple], list_prices: dict[int, float]
) -> tuple[list[tuple], list[tuple], dict[tuple[dt.date, int], int]]:
    signups = [c[1] for c in customers]
    # Some customers order much more often than others.
    cumulative_activity = []
    total = 0.0
    for _ in customers:
        total += rng.paretovariate(1.6)
        cumulative_activity.append(total)

    product_ids = sorted(list_prices)
    product_popularity = [rng.uniform(0.3, 3.0) for _ in product_ids]

    orders, items = [], []
    sold: dict[tuple[dt.date, int], int] = defaultdict(int)
    order_id = order_item_id = 0
    for day in _days():
        years = (day - FIRST_DAY).days / 365.25
        season = 1 + 0.25 * math.cos(2 * math.pi * (day.timetuple().tm_yday - 350) / 365.25)
        expected = (
            BASE_ORDERS_PER_DAY
            * (1 + YEARLY_GROWTH * years)
            * WEEKDAY_FACTOR[day.weekday()]
            * season
        )
        n_orders = max(1, round(expected * rng.gauss(1.0, 0.08)))
        eligible = bisect.bisect_right(signups, day)
        price_factor = 1.05 if day >= PRICE_INCREASE_DATE else 1.0
        for _ in range(n_orders):
            order_id += 1
            pick = rng.random() * cumulative_activity[eligible - 1]
            customer_id = customers[bisect.bisect_left(cumulative_activity, pick)][0]
            channel = _weighted(rng, CHANNELS)
            orders.append((order_id, day, customer_id, channel))

            n_lines = rng.choices([1, 2, 3, 4, 5], weights=[40, 30, 15, 10, 5])[0]
            lines = rng.choices(product_ids, weights=product_popularity, k=n_lines)
            for product_id in dict.fromkeys(lines):  # distinct products, in pick order
                order_item_id += 1
                quantity = rng.choices([1, 2, 3], weights=[70, 22, 8])[0]
                unit_price = round(list_prices[product_id] * price_factor, 2)
                discount = _weighted(rng, DISCOUNTS)
                items.append((order_item_id, order_id, product_id, quantity, unit_price, discount))
                sold[(day, product_id)] += quantity
    return orders, items, sold


def generate_inventory(
    rng: random.Random, products: list[tuple], sold: dict[tuple[dt.date, int], int]
) -> list[tuple]:
    """Units on hand at the end of each day: stock goes down with sales and is restocked."""
    on_hand = {p[0]: rng.randrange(80, 400) for p in products}
    reorder = {p[0]: rng.randrange(150, 500) for p in products}
    snapshots = []
    for day in _days():
        for product_id in on_hand:
            units = on_hand[product_id] - sold.get((day, product_id), 0)
            if units < 40:
                units += reorder[product_id]
            units = max(units, 0)
            on_hand[product_id] = units
            snapshots.append((day, product_id, units))
    return snapshots


TABLES = {
    "customers": (
        "customer_id INTEGER, signup_date DATE, country VARCHAR, region VARCHAR, segment VARCHAR"
    ),
    "products": "product_id INTEGER, name VARCHAR, category VARCHAR, unit_cost DECIMAL(10, 2)",
    "orders": "order_id INTEGER, order_date DATE, customer_id INTEGER, channel VARCHAR",
    "order_items": (
        "order_item_id INTEGER, order_id INTEGER, product_id INTEGER, quantity INTEGER, "
        "unit_price DECIMAL(10, 2), discount DECIMAL(4, 2)"
    ),
    "inventory_snapshots": "snapshot_date DATE, product_id INTEGER, units_on_hand INTEGER",
}


def generate(db_path: Path, seed: int = SEED) -> dict[str, int]:
    """Write the raw tables into `db_path` (replacing them) and return their row counts."""
    rng = random.Random(seed)
    customers = generate_customers(rng)
    products, list_prices = generate_products(rng)
    orders, items, sold = generate_orders(rng, customers, list_prices)
    snapshots = generate_inventory(rng, products, sold)
    data = {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": items,
        "inventory_snapshots": snapshots,
    }

    with tempfile.TemporaryDirectory() as tmp, duckdb.connect(str(db_path)) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
        for table, rows in data.items():
            csv_path = Path(tmp) / f"{table}.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                # None is written as an empty field, which DuckDB reads back as NULL.
                csv.writer(f).writerows(rows)
            conn.execute(f"CREATE OR REPLACE TABLE raw.{table} ({TABLES[table]})")
            conn.execute(
                f"COPY raw.{table} FROM '{csv_path.as_posix()}' (HEADER false, NULLSTR '')"
            )
    return {table: len(rows) for table, rows in data.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="DuckDB file to write")
    args = parser.parse_args()
    counts = generate(args.db)
    for table, count in counts.items():
        print(f"raw.{table}: {count} rows")


if __name__ == "__main__":
    main()
