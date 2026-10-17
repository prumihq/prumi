"""Spike: MetricFlow compiles, Prumi executes.

Run from this folder:  uv run python spike.py

1. Runs dbt (build to load the seed into DuckDB, parse to write the semantic manifest).
2. Loads the semantic manifest into MetricFlow with a SQL client that can only compile.
3. Compiles queries to SQL, runs them over our own DuckDB connection and checks the rows
   against hand-written SQL.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import duckdb
from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow.sql.render.sql_plan_renderer import SqlPlanRenderer
from metricflow_semantics.model.dbt_manifest_parser import (
    parse_manifest_from_dbt_generated_manifest,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

HERE = Path(__file__).parent
PROJECT_DIR = HERE / "dbt_project"
DB_PATH = HERE / "spike.duckdb"
MANIFEST_PATH = PROJECT_DIR / "target" / "semantic_manifest.json"

FAILURES: list[str] = []


class CompileOnlySqlClient:
    """Gives MetricFlow a dialect and nothing else. Attempts to run SQL are recorded and refused."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._renderer = DuckDbSqlPlanRenderer()

    @property
    def sql_engine_type(self) -> SqlEngine:
        return SqlEngine.DUCKDB

    @property
    def sql_plan_renderer(self) -> SqlPlanRenderer:
        return self._renderer

    def _refuse(self, method: str, stmt: str) -> None:
        self.calls.append(method)
        raise RuntimeError(
            f"CompileOnlySqlClient.{method} called; MetricFlow tried to run SQL:\n{stmt}"
        )

    def query(self, stmt, sql_bind_parameter_set=None):
        self._refuse("query", stmt)

    def execute(self, stmt, sql_bind_parameter_set=None):
        self._refuse("execute", stmt)

    def dry_run(self, stmt, sql_bind_parameter_set=None):
        self._refuse("dry_run", stmt)

    def close(self) -> None:
        pass

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        return f"${bind_parameter_key}"


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def run_dbt() -> None:
    # A separate process: dbt-duckdb keeps the database file open in-process (even after
    # dbtRunner.invoke returns), and DuckDB then refuses our own read-only connection.
    # Prumi will read a manifest the customer's dbt wrote, so this matches reality anyway.
    env = {**os.environ, "SPIKE_DUCKDB_PATH": str(DB_PATH), "DO_NOT_TRACK": "1"}
    dbt = Path(sys.executable).parent / "dbt"
    args = ["--project-dir", str(PROJECT_DIR), "--profiles-dir", str(PROJECT_DIR), "--quiet"]
    for command in ("build", "parse"):
        subprocess.run([str(dbt), command, *args], env=env, check=True)


def normalize(rows: list[tuple]) -> list[tuple]:
    """Make rows comparable: dates as ISO strings, numbers rounded to 6 places."""

    def norm(value: object) -> object:
        if isinstance(value, dt.datetime | dt.date):
            return value.isoformat()[:10]
        if isinstance(value, float | Decimal):
            return round(float(value), 6)
        return value

    return sorted(tuple(norm(v) for v in row) for row in rows)


def compile_sql(engine: MetricFlowEngine, **request: object) -> str:
    statement = engine.explain(MetricFlowQueryRequest.create(**request)).sql_statement
    if statement.bind_parameter_set.param_dict:
        raise AssertionError(f"Unexpected bind parameters: {statement.bind_parameter_set}")
    return statement.without_descriptions.sql


def check(
    name: str,
    engine: MetricFlowEngine,
    conn: duckdb.DuckDBPyConnection,
    request: dict,
    expected_sql: str,
    show_sql: bool = False,
) -> list[tuple]:
    section(name)
    print("request:", json.dumps(request, default=str))
    started = time.perf_counter()
    sql = compile_sql(engine, **request)
    compile_ms = (time.perf_counter() - started) * 1000
    if show_sql:
        print(f"\n-- compiled SQL ({compile_ms:.0f} ms)\n{sql}\n")
    else:
        print(f"compiled in {compile_ms:.0f} ms")

    cursor = conn.execute(sql)
    columns = [d[0] for d in cursor.description]
    got = cursor.fetchall()
    # Hand-written SQL aliases its columns with MetricFlow's names; compare in MetricFlow's order.
    expected_cursor = conn.execute(expected_sql)
    expected_columns = [d[0] for d in expected_cursor.description]
    if sorted(expected_columns) != sorted(columns):
        raise AssertionError(f"column names differ: {columns} vs {expected_columns}")
    positions = [expected_columns.index(c) for c in columns]
    expected = [tuple(row[i] for i in positions) for row in expected_cursor.fetchall()]

    print("columns:", columns)
    for row in normalize(got):
        print("  ", row)
    if normalize(got) == normalize(expected):
        print("MATCHES hand-written SQL")
    else:
        print("MISMATCH. Hand-written SQL returned:")
        for row in normalize(expected):
            print("  ", row)
        FAILURES.append(name)
    return got


def print_catalog(engine: MetricFlowEngine) -> None:
    section("Catalog metadata from MetricFlowEngine.list_metrics()")
    for metric in engine.list_metrics():
        print(f"- {metric.name}: type={metric.type.value} label={metric.label!r}")
        print(f"    description: {metric.description!r}")
        print(f"    type_params: {metric.type_params.json(exclude_none=True)}")
        print(f"    metadata/config: {metric.metadata!r} / {metric.config!r}")
        dims = ", ".join(f"{d.dunder_name} ({d.type.value})" for d in metric.dimensions)
        print(f"    dimensions: {dims}")
    print()
    for dim in engine.list_dimensions():
        grain = dim.type_params.time_granularity.value if dim.type_params else None
        print(
            f"dimension {dim.dunder_name}: label={dim.label!r} description={dim.description!r} "
            f"grain={grain}"
        )
    group_bys = engine.list_group_bys(["revenue"])
    print("group-bys for revenue:", sorted(getattr(g, "dunder_name", g.name) for g in group_bys))


def main() -> None:
    section("1. dbt build + parse (writes target/semantic_manifest.json)")
    started = time.perf_counter()
    run_dbt()
    print(f"dbt done in {time.perf_counter() - started:.1f} s; manifest: {MANIFEST_PATH}")

    section("2. Load manifest into MetricFlow with a compile-only SQL client")
    started = time.perf_counter()
    manifest = parse_manifest_from_dbt_generated_manifest(MANIFEST_PATH.read_text())
    lookup = SemanticManifestLookup(manifest)
    client = CompileOnlySqlClient()
    engine = MetricFlowEngine(semantic_manifest_lookup=lookup, sql_client=client)
    print(f"engine ready in {(time.perf_counter() - started) * 1000:.0f} ms")

    print_catalog(engine)

    # Our own connection. MetricFlow never sees it.
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    check(
        "3a. Ratio + distinct count by MONTH",
        engine,
        conn,
        dict(
            metric_names=["revenue", "orders", "average_order_value", "customers"],
            group_by_names=["metric_time__month"],
        ),
        """
        select date_trunc('month', order_date) as metric_time__month, sum(amount) as revenue,
               count(*) as orders, sum(amount) / count(*) as average_order_value,
               count(distinct customer_id) as customers
        from main.orders group by 1
        """,
        show_sql=True,
    )

    check(
        "3b. Ratio + distinct count by REGION",
        engine,
        conn,
        dict(
            metric_names=["revenue", "orders", "average_order_value", "customers"],
            group_by_names=["order__region"],
        ),
        """
        select region as order__region, sum(amount) as revenue, count(*) as orders,
               sum(amount) / count(*) as average_order_value,
               count(distinct customer_id) as customers
        from main.orders group by 1
        """,
    )

    check(
        "3c. Ratio + distinct count with no group-by (grand total)",
        engine,
        conn,
        dict(metric_names=["average_order_value", "customers"]),
        "select sum(amount) / count(*) as average_order_value,"
        " count(distinct customer_id) as customers from main.orders",
    )

    section("3d. Re-aggregating finer rows gives the wrong answer (why the grain matters)")
    daily = conn.execute(
        "select order_date, sum(amount) / count(*) as aov, count(distinct customer_id) as c "
        "from main.orders group by 1"
    ).fetchall()
    monthly = conn.execute(
        "select date_trunc('month', order_date), count(distinct customer_id) "
        "from main.orders group by 1"
    ).fetchall()
    true_aov, true_customers = conn.execute(
        "select sum(amount) / count(*), count(distinct customer_id) from main.orders"
    ).fetchone()
    avg_of_daily = sum(float(r[1]) for r in daily) / len(daily)
    print(f"true AOV {float(true_aov):.4f} vs average of daily AOVs {avg_of_daily:.4f}")
    print(
        f"true customers {true_customers} vs sum of monthly customers "
        f"{sum(r[1] for r in monthly)} vs sum of daily {sum(r[2] for r in daily)}"
    )

    check(
        "4a. Filter region = 'North' + time window Feb 1..Mar 31, by month",
        engine,
        conn,
        dict(
            metric_names=["revenue", "average_order_value", "customers"],
            group_by_names=["metric_time__month"],
            where_constraints=["{{ Dimension('order__region') }} = 'North'"],
            time_constraint_start=dt.datetime(2026, 2, 1),
            time_constraint_end=dt.datetime(2026, 3, 31),
        ),
        """
        select date_trunc('month', order_date) as metric_time__month, sum(amount) as revenue,
               sum(amount) / count(*) as average_order_value,
               count(distinct customer_id) as customers
        from main.orders
        where region = 'North' and order_date between date '2026-02-01' and date '2026-03-31'
        group by 1
        """,
        show_sql=True,
    )

    check(
        "4b. Time window inside a month (Jan 10..Feb 15) at day grain, region + filter on time",
        engine,
        conn,
        dict(
            metric_names=["revenue", "customers"],
            group_by_names=["metric_time__day", "order__region"],
            where_constraints=["{{ TimeDimension('metric_time', 'day') }} >= '2026-01-10'"],
            time_constraint_end=dt.datetime(2026, 2, 15),
        ),
        """
        select order_date as metric_time__day, region as order__region, sum(amount) as revenue,
               count(distinct customer_id) as customers
        from main.orders
        where order_date between date '2026-01-10' and date '2026-02-15'
        group by 1, 2
        """,
    )

    check(
        "4c. Month window that cuts a month in half: does the window clip rows or whole months?",
        engine,
        conn,
        dict(
            metric_names=["revenue"],
            group_by_names=["metric_time__month"],
            time_constraint_start=dt.datetime(2026, 1, 15),
            time_constraint_end=dt.datetime(2026, 2, 10),
        ),
        # Hypothesis: MetricFlow widens the window to whole months at month grain.
        """
        select date_trunc('month', order_date) as metric_time__month, sum(amount) as revenue
        from main.orders
        where order_date between date '2026-01-01' and date '2026-02-28'
        group by 1
        """,
    )

    section("5. Did MetricFlow try to run anything?")
    print(f"compile-only client calls: {client.calls or 'none'}")
    if client.calls:
        FAILURES.append("MetricFlow called the SQL client")

    section("Result")
    print("ALL CHECKS PASSED" if not FAILURES else f"FAILED: {FAILURES}")
    raise SystemExit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
