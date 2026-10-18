"""The engine against the demo warehouse: every number equals hand-written SQL (AC 9, 10, 11)."""

import datetime as dt

import duckdb
import pytest
from prumi.adapters import LOCAL_VIEWER, Filter
from prumi.engine import BlockInputs, BlockRequestError, Engine, UnknownBlockError
from prumi.spec import Block, DashboardSpec
from prumi.time import LastWindow, ToDateWindow

AS_OF = dt.date(2026, 10, 15)
SIX_MONTHS = LastWindow(last=6, grain="month")  # 2026-04-01 to 2026-09-30
IN_WINDOW = "order_date BETWEEN '2026-04-01' AND '2026-09-30'"


@pytest.fixture(scope="module")
def engine(demo_source):
    return Engine(demo_source, AS_OF)


@pytest.fixture(scope="module")
def sql(demo_project):
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:

        def query(text: str) -> list[tuple]:
            return [
                tuple(
                    float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v
                    for v in row
                )
                for row in conn.execute(text).fetchall()
            ]

        yield query


def run(engine, window=SIX_MONTHS, filters=(), **fields):
    block = Block.model_validate({"id": "b", "filters": [], **fields})
    return engine.run(block, BlockInputs(window, tuple(filters)), LOCAL_VIEWER)


def by(rows, key, metric):
    return {row[key]: row[metric] for row in rows}


# Hand-written SQL. Revenue and cost come from order lines, orders and customers from orders.
ITEMS = """(SELECT i.*, strftime(date_trunc('month', i.order_date), '%Y-%m-%d') AS month, c.region
            FROM main.order_items i LEFT JOIN main.customers c USING (customer_id))"""
ORDERS = """(SELECT o.*, strftime(date_trunc('month', o.order_date), '%Y-%m-%d') AS month, c.region
             FROM main.orders o LEFT JOIN main.customers c USING (customer_id))"""


def hand_sales(sql, key: str | None, where: str = IN_WINDOW) -> dict:
    """{key: (aov, customers, gross_margin_pct, revenue)} computed by hand at the shown grain."""
    group = f"{key}," if key else ""
    by_key = "GROUP BY 1" if key else ""
    items = sql(f"SELECT {group} sum(revenue), sum(cost) FROM {ITEMS} WHERE {where} {by_key}")
    orders = sql(
        f"SELECT {group} count(*), count(DISTINCT customer_id) FROM {ORDERS} WHERE {where} {by_key}"
    )
    items_by = {row[:-2] if key else (): row[-2:] for row in items}
    result = {}
    for row in orders:
        k = row[:-2] if key else ()
        n_orders, customers = row[-2:]
        revenue, cost = items_by[k]
        result[k[0] if key else None] = (
            revenue / n_orders,
            customers,
            (revenue - cost) / revenue,
            revenue,
        )
    return result


# R1: non-additive metrics at the grain they're shown (AC 10)

R1_METRICS = ["average_order_value", "customers", "gross_margin_pct", "revenue"]


@pytest.mark.parametrize(
    ("fields", "key", "sql_key"),
    [
        ({"time_grain": "month"}, "metric_time", "month"),
        ({"dimensions": ["customer__region"]}, "customer__region", "region"),
        ({}, None, None),
    ],
    ids=["by_month", "by_region", "total"],
)
def test_r1_ratios_and_distinct_counts(engine, sql, fields, key, sql_key):
    result = run(engine, metrics=R1_METRICS, **fields)
    expected = hand_sales(sql, sql_key)
    got = {row.get(key) if key else None: tuple(row[m] for m in R1_METRICS) for row in result.rows}
    assert got.keys() == expected.keys()
    for k, values in got.items():
        assert values == pytest.approx(expected[k]), k
    if key == "customer__region":
        assert None in got  # NULL region is its own row


@pytest.mark.parametrize(
    ("fields", "key", "expected_sql"),
    [
        (
            {"time_grain": "month"},
            "metric_time",
            """SELECT strftime(snapshot_date, '%Y-%m-01'), sum(units_on_hand)
               FROM main.inventory_snapshots
               WHERE snapshot_date IN (SELECT max(snapshot_date) FROM main.inventory_snapshots
                                       WHERE snapshot_date BETWEEN '2026-04-01' AND '2026-09-30'
                                       GROUP BY date_trunc('month', snapshot_date))
               GROUP BY 1""",
        ),
        (
            {"dimensions": ["product__category"]},
            "product__category",
            """SELECT p.category, sum(units_on_hand) FROM main.inventory_snapshots s
               JOIN main.products p USING (product_id)
               WHERE snapshot_date = DATE '2026-09-30' GROUP BY 1""",
        ),
        (
            {},
            None,
            """SELECT NULL, sum(units_on_hand) FROM main.inventory_snapshots
               WHERE snapshot_date = DATE '2026-09-30'""",
        ),
    ],
    ids=["by_month", "by_category", "total"],
)
def test_r1_semi_additive_balance(engine, sql, fields, key, expected_sql):
    # units_on_hand can't be joined to regions (R2); by category is its dimension.
    result = run(engine, metrics=["units_on_hand"], **fields)
    got = {row.get(key) if key else None: row["units_on_hand"] for row in result.rows}
    assert got == dict(sql(expected_sql))


def test_r1_summing_rows_would_be_wrong(engine):
    """Why the checks above matter: re-aggregating the monthly rows gives other numbers."""
    by_month = run(engine, metrics=["customers", "average_order_value"], time_grain="month")
    [total] = run(engine, metrics=["customers", "average_order_value"]).rows
    assert sum(r["customers"] for r in by_month.rows) > 1.5 * total["customers"]
    mean_aov = sum(r["average_order_value"] for r in by_month.rows) / len(by_month.rows)
    assert mean_aov != pytest.approx(total["average_order_value"], abs=0.01)


# R6: total and Others (AC 11)


@pytest.fixture(scope="module")
def top3(engine):
    return run(
        engine,
        metrics=["revenue", "customers", "average_order_value"],
        dimensions=["customer__region"],
        sort={"by": "revenue", "descending": True},
        top_n=3,
        others=True,
        total=True,
    )


def test_r6_top_rows(top3, sql):
    expected = sql(
        f"SELECT region, sum(revenue) FROM {ITEMS} WHERE {IN_WINDOW}"
        " GROUP BY 1 ORDER BY 2 DESC LIMIT 3"
    )
    assert [(r["customer__region"], r["revenue"]) for r in top3.rows] == pytest.approx(expected)


def test_r6_total_is_a_source_query_without_the_dimension(top3, sql):
    [(aov, customers, _, revenue)] = hand_sales(sql, None).values()
    assert top3.total == pytest.approx(
        {"revenue": revenue, "customers": customers, "average_order_value": aov}
    )


def test_r6_others(top3, sql):
    top = ", ".join(f"'{r['customer__region']}'" for r in top3.rows)
    outside = f"{IN_WINDOW} AND (region NOT IN ({top}) OR region IS NULL)"
    [(aov, customers, _, revenue)] = hand_sales(sql, None, outside).values()
    others = top3.others
    # Additive: total minus the top rows, which equals the not_in query.
    assert others["revenue"] == pytest.approx(
        top3.total["revenue"] - sum(r["revenue"] for r in top3.rows)
    )
    assert others["revenue"] == pytest.approx(revenue)
    # Non-additive: the not_in query.
    assert others["customers"] == customers
    assert others["average_order_value"] == pytest.approx(aov)
    # NULL-region rows are counted in Others.
    [(without_null,)] = sql(
        f"SELECT sum(revenue) FROM {ITEMS} WHERE {IN_WINDOW} AND region NOT IN ({top})"
    )
    assert others["revenue"] > without_null


def test_no_others_when_every_group_is_in_the_top(engine):
    result = run(
        engine,
        metrics=["revenue", "customers"],
        dimensions=["order__channel"],
        sort={"by": "revenue", "descending": True},
        top_n=5,
        others=True,
    )
    assert len(result.rows) == 3 and result.others is None


# Time: partial periods and stale data (AC 9)


def test_include_current_marks_the_partial_month(engine):
    window = LastWindow(last=3, grain="month", include_current=True)
    result = run(engine, window, metrics=["revenue"], time_grain="month")
    months = [(r["metric_time"], r.get("_partial", False)) for r in result.rows]
    assert months == [
        ("2026-07-01", False),
        ("2026-08-01", False),
        ("2026-09-01", False),
        ("2026-10-01", True),
    ]
    assert result.window == {
        "start": "2026-07-01",
        "end": "2026-10-14",
        "grain": "month",
        "partial": True,
    }
    assert not result.warnings  # the data runs through the window's end


def test_a_window_past_the_data_gets_stale_data(demo_source):
    later = Engine(demo_source, dt.date(2026, 10, 20))  # last week: Oct 12-18
    result = run(later, LastWindow(last=1, grain="week"), metrics=["revenue", "orders"])
    assert result.window["end"] == "2026-10-18"
    assert [(w.code, w.path) for w in result.warnings] == [
        ("R3.stale_data", "metrics[0]"),
        ("R3.stale_data", "metrics[1]"),
    ]
    assert "data through 2026-10-14; the window ends 2026-10-18" in result.warnings[0].message
    assert result.freshness == {"revenue": "2026-10-14", "orders": "2026-10-14"}


def test_an_empty_to_date_window(demo_source):
    first_day = Engine(demo_source, dt.date(2026, 10, 1))
    result = run(first_day, ToDateWindow(to_date="month"), metrics=["revenue"])
    assert result.rows == [] and result.window["partial"]
    assert [w.code for w in result.warnings] == ["R3.empty_window"]


# Comparisons


def test_compare_previous_year(engine, sql):
    result = run(engine, metrics=["revenue", "average_order_value"], compare="previous_year")
    assert result.comparison_window == {
        "start": "2025-04-01",
        "end": "2025-09-30",
        "grain": "month",
        "partial": False,
    }
    [row] = result.rows
    [(aov, _, _, revenue)] = hand_sales(
        sql, None, "order_date BETWEEN '2025-04-01' AND '2025-09-30'"
    ).values()
    assert row["_previous"] == pytest.approx({"revenue": revenue, "average_order_value": aov})
    assert row["_delta"]["revenue"] == pytest.approx(row["revenue"] - revenue)
    assert row["_delta_pct"]["revenue"] == pytest.approx((row["revenue"] - revenue) / revenue)


def test_compare_matches_rows_on_dimension_values(engine, sql):
    result = run(
        engine, metrics=["revenue"], dimensions=["customer__region"], compare="previous_period"
    )
    previous = dict(
        sql(
            f"SELECT region, sum(revenue) FROM {ITEMS}"
            " WHERE order_date BETWEEN '2025-10-01' AND '2026-03-31' GROUP BY 1"
        )
    )
    for row in result.rows:
        assert row["_previous"]["revenue"] == pytest.approx(previous[row["customer__region"]])


# Rows, filters and the result shape


def test_rows_are_capped_at_5000(engine):
    result = run(
        engine,
        LastWindow(last=2, grain="year"),
        metrics=["revenue"],
        time_grain="day",
        dimensions=["customer__region"],
    )
    assert len(result.rows) == 5000 and result.truncated
    assert [w.code for w in result.warnings] == ["result.truncated"]
    # Ordered by time, then region: the cut keeps the earliest days.
    assert result.rows[0]["metric_time"] == "2024-01-01"


def test_filters_apply(engine, sql):
    result = run(
        engine,
        metrics=["revenue"],
        filters=[Filter("customer__region", "in", ("Asia Pacific", None))],
    )
    [(expected,)] = sql(
        f"SELECT sum(revenue) FROM {ITEMS} WHERE {IN_WINDOW}"
        " AND (region = 'Asia Pacific' OR region IS NULL)"
    )
    assert result.rows[0]["revenue"] == pytest.approx(expected)


def test_result_json_shape(top3):
    data = top3.to_json()
    assert data["block"] == "b"
    assert data["columns"] == [
        {"name": "customer__region", "kind": "dimension", "label": "Region"},
        {
            "name": "revenue",
            "kind": "metric",
            "label": "Revenue",
            "format": {"type": "currency", "unit": "USD", "decimals": 2},
            "additive": True,
        },
        {
            "name": "customers",
            "kind": "metric",
            "label": "Customers",
            "format": {"type": "integer", "unit": "customers", "decimals": None},
            "additive": False,
        },
        {
            "name": "average_order_value",
            "kind": "metric",
            "label": "Average order value",
            "format": {"type": "currency", "unit": "USD", "decimals": 2},
            "additive": False,
        },
    ]
    assert data["window"] == {
        "start": "2026-04-01",
        "end": "2026-09-30",
        "grain": "month",
        "partial": False,
    }
    assert set(data) == {
        "block", "columns", "rows", "others", "total", "window", "comparison_window",
        "freshness", "warnings", "truncated",
    }  # fmt: skip


# run_block: the stored spec plus the viewer's filter values

SPEC = DashboardSpec.model_validate(
    {
        "spec_version": 0,
        "dashboard": {"id": "demo", "title": "Demo"},
        "filters": [
            {"id": "period", "type": "time", "default": {"last": 6, "grain": "month"}},
            {"id": "region", "type": "dimension", "dimension": "customer__region"},
        ],
        "data": [
            {"id": "kpi", "metrics": ["revenue"], "filters": ["period", "region"]},
            {"id": "stock", "metrics": ["units_on_hand"], "filters": ["period"]},
        ],
    }
)


def test_run_block_with_defaults_and_values(engine, sql):
    default = engine.run_block(SPEC, "kpi", {}, LOCAL_VIEWER)
    [(revenue,)] = sql(f"SELECT sum(revenue) FROM {ITEMS} WHERE {IN_WINDOW}")
    assert default.rows[0]["revenue"] == pytest.approx(revenue)

    chosen = engine.run_block(
        SPEC,
        "kpi",
        {"period": {"last": 1, "grain": "month"}, "region": ["Côte d'Ivoire"]},
        LOCAL_VIEWER,
    )
    assert chosen.window["start"] == "2026-09-01"
    assert chosen.rows[0]["revenue"] is None  # Côte d'Ivoire is a country, not a region

    # A filter the block doesn't list is ignored (R5): the region filter isn't on "stock".
    stock = engine.run_block(SPEC, "stock", {"region": ["Asia Pacific"]}, LOCAL_VIEWER)
    assert stock.rows[0]["units_on_hand"] > 0


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"period": "last 3 weeks"}, "filter.invalid_value"),
        ({"period": {"last": 0, "grain": "week"}}, "filter.invalid_value"),
        ({"region": "Asia Pacific"}, "filter.invalid_value"),
        ({"region": [{"a": 1}]}, "filter.invalid_value"),
        ({"region": ["{{ 7 * 7 }}"]}, "filter.invalid_value"),
        (
            {"period": {"start": "2026-01-15", "end": "2026-02-10", "grain": "month"}},
            "R3.window_too_short",
        ),
    ],
)
def test_run_block_rejects_bad_values(engine, values, code):
    with pytest.raises(BlockRequestError) as error:
        engine.run_block(SPEC, "kpi", values, LOCAL_VIEWER)
    assert [i.code for i in error.value.issues] == [code]


def test_run_block_unknown_block(engine):
    with pytest.raises(UnknownBlockError):
        engine.run_block(SPEC, "nope", {}, LOCAL_VIEWER)
