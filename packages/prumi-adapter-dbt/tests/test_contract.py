"""Contract test: what the dbt adapter relies on from MetricFlow (pinned at 0.213.0).

The `metricflow-compile` spike's checks, ported onto the demo project, plus the behaviors spec
section 4 relies on that the spike didn't test. Each query is compiled by MetricFlow, run over
our own read-only DuckDB connection, and compared with hand-written SQL over the demo warehouse.
The compile-only client must never be called. Rerun this on every MetricFlow bump.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import duckdb
import pytest
from prumi_adapter_dbt._metricflow import CompileRequest, MetricFlowCompiler

METRIC_TIME_DAY = "{{ TimeDimension('metric_time', 'day') }}"
REGION = "{{ Dimension('customer__region') }}"


def window(start: str, end: str) -> tuple[str, ...]:
    """The adapter's window form: inclusive bounds on metric_time at day grain (spec 4)."""
    return (f"{METRIC_TIME_DAY} >= '{start}'", f"{METRIC_TIME_DAY} <= '{end}'")


@pytest.fixture(scope="module")
def mf(demo_project) -> MetricFlowCompiler:
    # Its own compiler, so the zero-calls check covers exactly these queries.
    return MetricFlowCompiler(demo_project.manifest_path, "duckdb")


@pytest.fixture(scope="module")
def conn(demo_project):
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def metricflow_runs_nothing(mf):
    yield
    assert mf.client.calls == []


def normalize(value: object) -> object:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, float | Decimal):
        return round(float(value), 6)
    return value


def fetch(conn, sql: str) -> list[dict[str, object]]:
    """Rows as dicts keyed by column name: MetricFlow reorders columns (spike caveat 5)."""
    cursor = conn.execute(sql)
    names = [d[0] for d in cursor.description]
    return [{n: normalize(v) for n, v in zip(names, row, strict=True)} for row in cursor.fetchall()]


def unordered(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: repr(sorted(row.items())))


def run(mf, conn, **request) -> list[dict[str, object]]:
    return fetch(conn, mf.compile(CompileRequest(**request)))


def check(mf, conn, request: dict, expected_sql: str, ordered: bool = False) -> list[dict]:
    """MetricFlow's rows equal the hand-written SQL's, whose columns use MetricFlow's names."""
    got = run(mf, conn, **request)
    expected = fetch(conn, expected_sql)
    assert got, "the check needs rows to mean anything"
    assert set(got[0]) == set(expected[0])
    if ordered:
        assert got == expected
    else:
        assert unordered(got) == unordered(expected)
    return got


# Hand-written building blocks. Revenue and cost come from order lines; orders and customers
# from orders. AOV's numerator and denominator come from different semantic models.
ITEMS = """
    SELECT i.*, date_trunc('month', i.order_date)::date AS month, c.region, c.country
    FROM main.order_items i LEFT JOIN main.customers c USING (customer_id)
"""
ORDERS = """
    SELECT o.*, date_trunc('month', o.order_date)::date AS month, c.region, c.country
    FROM main.orders o LEFT JOIN main.customers c USING (customer_id)
"""
MEASURES = """
    revenue,
    orders,
    revenue / orders AS average_order_value,
    customers,
    (revenue - cost) / revenue AS gross_margin_pct
"""


def sales_by(key: str, where: str = "TRUE") -> str:
    """Hand-written revenue, orders, AOV, customers and margin %, grouped by `key` (or not)."""
    if not key:
        return f"""
            SELECT {MEASURES} FROM
              (SELECT sum(revenue) AS revenue, sum(cost) AS cost FROM ({ITEMS}) WHERE {where}),
              (SELECT count(*) AS orders, count(DISTINCT customer_id) AS customers
               FROM ({ORDERS}) WHERE {where})
        """
    return f"""
        WITH i AS (SELECT {key} AS k, sum(revenue) AS revenue, sum(cost) AS cost
                   FROM ({ITEMS}) WHERE {where} GROUP BY 1),
             o AS (SELECT {key} AS k, count(*) AS orders, count(DISTINCT customer_id) AS customers
                   FROM ({ORDERS}) WHERE {where} GROUP BY 1)
        SELECT o.k AS KEY_NAME, {MEASURES}
        FROM o JOIN i ON o.k IS NOT DISTINCT FROM i.k
    """


SALES_METRICS = ("revenue", "orders", "average_order_value", "customers", "gross_margin_pct")


# The spike's checks


def test_ratio_and_distinct_count_by_month(mf, conn):
    check(
        mf,
        conn,
        dict(metrics=SALES_METRICS, group_by=("metric_time__month",)),
        sales_by("month").replace("KEY_NAME", "metric_time__month"),
    )


def test_ratio_and_distinct_count_by_region(mf, conn):
    rows = check(
        mf,
        conn,
        dict(metrics=SALES_METRICS, group_by=("customer__region",)),
        sales_by("region").replace("KEY_NAME", "customer__region"),
    )
    # Customers with a NULL region form their own group; they don't disappear.
    assert None in {row["customer__region"] for row in rows}


def test_ratio_and_distinct_count_in_total(mf, conn):
    check(mf, conn, dict(metrics=SALES_METRICS), sales_by(""))


def test_re_aggregating_finer_rows_would_be_wrong(mf, conn):
    """Why the checks above mean something: the traps are real in the demo data."""
    [total] = run(mf, conn, metrics=("average_order_value", "customers"))
    by_month = run(
        mf, conn, metrics=("average_order_value", "customers"), group_by=("metric_time__month",)
    )
    mean_of_monthly_aov = sum(r["average_order_value"] for r in by_month) / len(by_month)
    assert abs(mean_of_monthly_aov - total["average_order_value"]) > 0.5
    assert sum(r["customers"] for r in by_month) > 2 * total["customers"]


def test_filter_and_window_by_month(mf, conn):
    in_h1_2026 = "order_date BETWEEN '2026-01-01' AND '2026-06-30'"
    check(
        mf,
        conn,
        dict(
            metrics=("revenue", "average_order_value", "customers"),
            group_by=("metric_time__month",),
            where=(f"{REGION} = 'North America'", *window("2026-01-01", "2026-06-30")),
        ),
        f"""
        SELECT metric_time__month, revenue, average_order_value, customers FROM (
            {sales_by("month", f"region = 'North America' AND {in_h1_2026}")}
        )
        """.replace("KEY_NAME", "metric_time__month"),
    )


def test_day_grain_by_region_with_window(mf, conn):
    rows = check(
        mf,
        conn,
        dict(
            metrics=("revenue", "customers"),
            group_by=("metric_time__day", "customer__region"),
            where=window("2026-09-10", "2026-09-20"),
        ),
        f"""
        WITH i AS (SELECT order_date, region, sum(revenue) AS revenue FROM ({ITEMS})
                   WHERE order_date BETWEEN '2026-09-10' AND '2026-09-20' GROUP BY ALL),
             o AS (SELECT order_date, region, count(DISTINCT customer_id) AS customers
                   FROM ({ORDERS})
                   WHERE order_date BETWEEN '2026-09-10' AND '2026-09-20' GROUP BY ALL)
        SELECT o.order_date AS metric_time__day, o.region AS customer__region, revenue, customers
        FROM o JOIN i ON o.order_date = i.order_date AND o.region IS NOT DISTINCT FROM i.region
        """,
    )
    days = {row["metric_time__day"] for row in rows}
    assert min(days) == dt.date(2026, 9, 10) and max(days) == dt.date(2026, 9, 20)


# What spec section 4 relies on beyond the spike


def test_window_without_a_time_group_by(mf, conn):
    """KPIs: the window where clause works with no metric_time in the group-by."""
    check(
        mf,
        conn,
        dict(metrics=SALES_METRICS, where=window("2026-07-01", "2026-09-30")),
        sales_by("", "order_date BETWEEN '2026-07-01' AND '2026-09-30'"),
    )


@pytest.mark.parametrize(
    ("dimension", "expected_sql"),
    [
        ("customer__region", "SELECT DISTINCT region AS customer__region FROM main.customers"),
        ("customer__country", "SELECT DISTINCT country AS customer__country FROM main.customers"),
        ("order__channel", "SELECT DISTINCT channel AS order__channel FROM main.orders"),
        ("product__category", "SELECT DISTINCT category AS product__category FROM main.products"),
    ],
)
def test_dimension_values_from_a_group_by_only_request(mf, conn, dimension, expected_sql):
    """No metrics and one group-by compiles to the dimension's distinct values, NULL included."""
    check(mf, conn, dict(group_by=(dimension,)), expected_sql)


def test_order_by_and_limit_for_freshness(mf, conn):
    """The engine's freshness query: latest metric_time day with data."""
    for metric in ("revenue", "customers", "units_on_hand"):
        [row] = run(
            mf,
            conn,
            metrics=(metric,),
            group_by=("metric_time__day",),
            order_by=("-metric_time__day",),
            limit=1,
        )
        assert row["metric_time__day"] == dt.date(2026, 10, 14), metric


def test_order_by_metric_descending_and_limit_for_top_n(mf, conn):
    check(
        mf,
        conn,
        dict(
            metrics=("revenue",),
            group_by=("customer__region",),
            order_by=("-revenue",),
            limit=3,
        ),
        f"""
        SELECT region AS customer__region, sum(revenue) AS revenue FROM ({ITEMS})
        GROUP BY 1 ORDER BY 2 DESC LIMIT 3
        """,
        ordered=True,
    )


def test_order_by_ascending(mf, conn):
    rows = run(
        mf,
        conn,
        metrics=("revenue",),
        group_by=("metric_time__month",),
        order_by=("metric_time__month",),
    )
    months = [row["metric_time__month"] for row in rows]
    assert months == sorted(months) and len(months) == 34


def test_not_in_keeps_null_only_when_asked(mf, conn):
    """`not_in` renders as NOT IN (...) OR IS NULL; plain NOT IN drops NULL rows (SQL semantics)."""
    regions = "('North America', 'Western Europe')"
    keep_null = f"({REGION} NOT IN {regions} OR {REGION} IS NULL)"
    check(
        mf,
        conn,
        dict(metrics=SALES_METRICS, where=(keep_null,)),
        sales_by("", f"(region NOT IN {regions} OR region IS NULL)"),
    )
    [with_null] = run(mf, conn, metrics=("revenue",), where=(keep_null,))
    [without_null] = run(mf, conn, metrics=("revenue",), where=(f"{REGION} NOT IN {regions}",))
    [null_region] = run(mf, conn, metrics=("revenue",), where=(f"{REGION} IS NULL",))
    assert null_region["revenue"] > 0
    assert with_null["revenue"] == pytest.approx(without_null["revenue"] + null_region["revenue"])


def test_eq_or_null(mf, conn):
    check(
        mf,
        conn,
        dict(metrics=SALES_METRICS, where=(f"({REGION} = 'Asia Pacific' OR {REGION} IS NULL)",)),
        sales_by("", "(region = 'Asia Pacific' OR region IS NULL)"),
    )


def test_group_rows_of_an_additive_metric_add_up_to_the_total(mf, conn):
    """ "Others" for additive metrics is total minus the top rows; NULL rows must be among them."""
    rows = run(mf, conn, metrics=("revenue",), group_by=("customer__region",))
    [total] = run(mf, conn, metrics=("revenue",))
    assert sum(row["revenue"] for row in rows) == pytest.approx(total["revenue"])


def test_a_quote_in_a_filter_value_is_escaped_by_doubling(mf, conn):
    check(
        mf,
        conn,
        dict(
            metrics=("revenue", "customers"),
            where=("{{ Dimension('customer__country') }} = 'Côte d''Ivoire'",),
        ),
        sales_by("", "country = 'Côte d''Ivoire'").replace(MEASURES, "revenue, customers"),
    )


def test_non_additive_dimension_takes_the_last_day_of_each_period(mf, conn):
    """units_on_hand is a balance: per month, the units on the month's last snapshot day."""
    check(
        mf,
        conn,
        dict(
            metrics=("units_on_hand",),
            group_by=("metric_time__month",),
            where=window("2025-10-01", "2026-09-30"),
        ),
        """
        WITH s AS (SELECT *, date_trunc('month', snapshot_date)::date AS month
                   FROM main.inventory_snapshots
                   WHERE snapshot_date BETWEEN '2025-10-01' AND '2026-09-30')
        SELECT month AS metric_time__month, sum(units_on_hand) AS units_on_hand
        FROM s WHERE snapshot_date = (SELECT max(snapshot_date) FROM s s2 WHERE s2.month = s.month)
        GROUP BY 1
        """,
    )


def test_non_additive_dimension_in_total_takes_the_last_day_of_the_window(mf, conn):
    """The window is applied before the last day is picked."""
    check(
        mf,
        conn,
        dict(metrics=("units_on_hand",), where=window("2026-07-01", "2026-09-30")),
        """
        SELECT sum(units_on_hand) AS units_on_hand FROM main.inventory_snapshots
        WHERE snapshot_date = DATE '2026-09-30'
        """,
    )


def test_non_additive_dimension_by_category(mf, conn):
    check(
        mf,
        conn,
        dict(
            metrics=("units_on_hand",),
            group_by=("product__category",),
            where=window("2026-07-01", "2026-09-30"),
        ),
        """
        SELECT p.category AS product__category, sum(s.units_on_hand) AS units_on_hand
        FROM main.inventory_snapshots s JOIN main.products p USING (product_id)
        WHERE s.snapshot_date = DATE '2026-09-30' GROUP BY 1
        """,
    )


def test_week_grain_starts_on_monday(mf, conn):
    # The week start computed by hand, without relying on date_trunc's definition of a week.
    monday = "order_date - (isodow(order_date) - 1)::int"
    in_weeks = "order_date BETWEEN '2026-08-31' AND '2026-10-11'"
    rows = check(
        mf,
        conn,
        dict(
            metrics=("revenue", "customers"),
            group_by=("metric_time__week",),
            where=window("2026-08-31", "2026-10-11"),  # six ISO weeks, Monday to Sunday
        ),
        f"""
        WITH i AS (SELECT {monday} AS week, sum(revenue) AS revenue
                   FROM ({ITEMS}) WHERE {in_weeks} GROUP BY 1),
             o AS (SELECT {monday} AS week, count(DISTINCT customer_id) AS customers
                   FROM ({ORDERS}) WHERE {in_weeks} GROUP BY 1)
        SELECT week AS metric_time__week, revenue, customers FROM i JOIN o USING (week)
        """,
    )
    assert len(rows) == 6
    assert {row["metric_time__week"].isoweekday() for row in rows} == {1}


@pytest.mark.parametrize("grain", ["month", "quarter", "year"])
def test_coarser_grains(mf, conn, grain):
    check(
        mf,
        conn,
        dict(metrics=("revenue", "average_order_value"), group_by=(f"metric_time__{grain}",)),
        f"""
        WITH i AS (SELECT date_trunc('{grain}', order_date)::date AS p, sum(revenue) AS revenue
                   FROM main.order_items GROUP BY 1),
             o AS (SELECT date_trunc('{grain}', order_date)::date AS p, count(*) AS orders
                   FROM main.orders GROUP BY 1)
        SELECT p AS metric_time__{grain}, revenue, revenue / orders AS average_order_value
        FROM i JOIN o USING (p)
        """,
    )
