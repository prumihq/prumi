"""execute(): compile with MetricFlow, run on Prumi's own connection, return semantic names."""

import datetime as dt
import shutil

import duckdb
import pytest
from prumi.adapters import (
    LOCAL_VIEWER,
    Capabilities,
    Column,
    DateRange,
    OrderBy,
    SemanticQuery,
    SourceInfo,
)
from prumi_adapter_dbt.adapter import DbtAdapter

Q3_2026 = DateRange(dt.date(2026, 7, 1), dt.date(2026, 9, 30))


def hand(demo_project, sql: str) -> list[tuple]:
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        return conn.execute(sql).fetchall()


def test_capabilities(adapter):
    assert adapter.capabilities() == Capabilities(identity_mode="shared_account")


def test_describe_names_the_adapter_and_the_connection_type(adapter):
    assert adapter.describe() == SourceInfo(adapter="dbt", connection="duckdb")


def test_time_series_uses_semantic_names_and_dates(adapter, demo_project):
    query = SemanticQuery(metrics=("revenue",), time_grain="month", window=Q3_2026)
    result = adapter.execute(query, LOCAL_VIEWER)
    assert result.columns == (Column("metric_time", "time"), Column("revenue", "metric"))
    expected = hand(
        demo_project,
        "SELECT date_trunc('month', order_date)::date, sum(revenue)::double FROM main.order_items"
        " WHERE order_date BETWEEN '2026-07-01' AND '2026-09-30' GROUP BY 1",
    )
    assert sorted(result.rows) == pytest.approx(sorted(expected))
    assert result.row_count == 3
    for month, revenue in result.rows:
        assert type(month) is dt.date
        assert type(revenue) is float


def test_columns_follow_the_query_not_metricflow(adapter, demo_project):
    # MetricFlow puts ratio metrics after simple ones; the result keeps the query's order.
    query = SemanticQuery(
        metrics=("average_order_value", "orders"), dimensions=("order__channel",), window=Q3_2026
    )
    result = adapter.execute(query, LOCAL_VIEWER)
    assert [c.name for c in result.columns] == ["order__channel", "average_order_value", "orders"]
    expected = hand(
        demo_project,
        """
        SELECT o.channel, i.revenue / o.orders, o.orders FROM
          (SELECT channel, count(*) AS orders FROM main.orders
           WHERE order_date BETWEEN '2026-07-01' AND '2026-09-30' GROUP BY 1) o
        JOIN (SELECT o.channel, sum(i.revenue) AS revenue FROM main.order_items i
              JOIN main.orders o USING (order_id)
              WHERE i.order_date BETWEEN '2026-07-01' AND '2026-09-30' GROUP BY 1) i
        USING (channel)
        """,
    )
    assert sorted(result.rows) == pytest.approx(sorted(expected))
    assert all(type(orders) is int for _, _, orders in result.rows)


def test_kpi_with_a_window_and_no_grouping(adapter, demo_project):
    result = adapter.execute(SemanticQuery(metrics=("customers",), window=Q3_2026), LOCAL_VIEWER)
    assert result.columns == (Column("customers", "metric"),)
    assert result.rows == tuple(
        hand(
            demo_project,
            "SELECT count(DISTINCT customer_id) FROM main.orders"
            " WHERE order_date BETWEEN '2026-07-01' AND '2026-09-30'",
        )
    )


def test_freshness_query(adapter):
    query = SemanticQuery(
        metrics=("units_on_hand",),
        time_grain="day",
        order_by=(OrderBy("metric_time", descending=True),),
        limit=1,
    )
    result = adapter.execute(query, LOCAL_VIEWER)
    assert result.rows[0][0] == dt.date(2026, 10, 14)


def test_dimension_values(adapter, demo_project):
    result = adapter.execute(
        SemanticQuery(metrics=(), dimensions=("customer__region",)), LOCAL_VIEWER
    )
    assert result.columns == (Column("customer__region", "dimension"),)
    expected = hand(demo_project, "SELECT DISTINCT region FROM main.customers")
    assert set(result.rows) == set(expected)
    assert (None,) in result.rows


def test_order_by_must_name_a_query_field(adapter):
    with pytest.raises(ValueError, match="time_grain"):
        adapter.execute(
            SemanticQuery(metrics=("revenue",), order_by=(OrderBy("metric_time"),)), LOCAL_VIEWER
        )
    with pytest.raises(ValueError, match="'orders'"):
        adapter.execute(
            SemanticQuery(metrics=("revenue",), order_by=(OrderBy("orders"),)), LOCAL_VIEWER
        )


def test_the_connection_is_closed_after_each_query(demo_project, dbt_config, tmp_path):
    # DuckDB refuses a read-write connection in a process that still holds a read-only one
    # to the same file, so opening one proves execute() closed its connection.
    shutil.copy(demo_project.db_path, tmp_path / "demo.duckdb")
    config = {
        "semantic_manifest": str(demo_project.manifest_path),
        "connection": {"type": "duckdb", "path": "demo.duckdb"},
    }
    adapter = DbtAdapter.from_config(config, tmp_path)
    adapter.execute(SemanticQuery(metrics=("revenue",)), LOCAL_VIEWER)
    duckdb.connect(str(tmp_path / "demo.duckdb")).close()
