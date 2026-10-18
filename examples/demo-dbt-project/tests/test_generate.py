"""The demo data generator is deterministic and matches spec section 15."""

import datetime as dt
import importlib.util
from pathlib import Path

import duckdb
import pytest

GENERATE_PY = Path(__file__).parents[1] / "generate.py"
TABLES = ["customers", "products", "orders", "order_items", "inventory_snapshots"]


@pytest.fixture(scope="module")
def generate():
    spec = importlib.util.spec_from_file_location("demo_generate", GENERATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate


@pytest.fixture(scope="module")
def two_runs(generate, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("generate")
    paths = [tmp / "first.duckdb", tmp / "second.duckdb"]
    for path in paths:
        generate(path)
    return paths


def query(path: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(path), read_only=True) as conn:
        return conn.execute(sql).fetchall()


def test_two_runs_write_identical_tables(two_runs):
    first, second = two_runs
    for table in TABLES:
        sql = f"SELECT * FROM raw.{table} ORDER BY ALL"
        assert query(first, sql) == query(second, sql), table


def test_rerun_into_the_same_file_replaces_the_tables(generate, two_runs, tmp_path):
    path = tmp_path / "rerun.duckdb"
    generate(path)
    generate(path)
    sql = "SELECT count(*) FROM raw.orders"
    assert query(path, sql) == query(two_runs[0], sql)


def test_customers(two_runs):
    path = two_runs[0]
    [(count, null_regions, regions)] = query(
        path, "SELECT count(*), count(*) - count(region), count(DISTINCT region) FROM raw.customers"
    )
    assert count == 3000
    assert 0.015 <= null_regions / count <= 0.025  # about 2% have NULL region
    assert regions > 5  # enough for a top 5 with "Others"
    # A filter value with a quote, for the escaping tests.
    assert query(path, "SELECT count(*) FROM raw.customers WHERE country = 'Côte d''Ivoire'")[0][0]


def test_products(two_runs):
    rows = query(two_runs[0], "SELECT category, count(*) FROM raw.products GROUP BY 1")
    assert len(rows) == 6
    assert sum(n for _, n in rows) == 60


def test_orders(two_runs):
    path = two_runs[0]
    [(first, last)] = query(path, "SELECT min(order_date), max(order_date) FROM raw.orders")
    assert (first, last) == (dt.date(2024, 1, 1), dt.date(2026, 10, 14))
    channels = query(path, "SELECT DISTINCT channel FROM raw.orders ORDER BY 1")
    assert channels == [("marketplace",), ("store",), ("web",)]
    # Orders only reference known customers who had signed up by then.
    assert query(
        path,
        "SELECT count(*) FROM raw.orders o LEFT JOIN raw.customers c USING (customer_id)"
        " WHERE c.customer_id IS NULL OR c.signup_date > o.order_date",
    ) == [(0,)]


def test_order_volume_grows_and_dips_on_sundays(two_runs):
    path = two_runs[0]
    by_weekday = dict(query(path, "SELECT isodow(order_date), count(*) FROM raw.orders GROUP BY 1"))
    assert by_weekday[7] < 0.7 * min(by_weekday[d] for d in range(1, 7))
    by_year = dict(
        query(
            path,
            "SELECT year(order_date), count(*) / count(DISTINCT order_date) FROM raw.orders"
            " GROUP BY 1",
        )
    )
    assert by_year[2024] < by_year[2025] < by_year[2026]


def test_order_items_and_inventory(two_runs):
    path = two_runs[0]
    assert query(
        path,
        "SELECT count(*) FROM raw.order_items i LEFT JOIN raw.orders o USING (order_id)"
        " WHERE o.order_id IS NULL",
    ) == [(0,)]
    [(days, products, first, last, negative)] = query(
        path,
        "SELECT count(DISTINCT snapshot_date), count(DISTINCT product_id), min(snapshot_date),"
        " max(snapshot_date), count(*) FILTER (WHERE units_on_hand < 0)"
        " FROM raw.inventory_snapshots",
    )
    assert (first, last) == (dt.date(2024, 1, 1), dt.date(2026, 10, 14))
    assert days == (last - first).days + 1  # daily
    assert products == 60
    assert negative == 0
