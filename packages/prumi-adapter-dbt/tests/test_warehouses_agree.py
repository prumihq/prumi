"""DuckDB and Postgres give the same numbers, with the same Python types, for the same queries.

The other tests compare each warehouse with hand-written SQL on that same warehouse, which
would pass even if the two disagreed. Here one `SemanticQuery` goes through a DuckDB adapter
and two Postgres adapters (the connection's own time zone, and one far from UTC) and the
results must be identical.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from prumi.adapters import LOCAL_VIEWER, DateRange, Filter, OrderBy, SemanticQuery
from prumi_adapter_dbt.adapter import DbtAdapter

WINDOW = DateRange(dt.date(2025, 7, 1), dt.date(2026, 9, 30))
FAR_FROM_UTC = "Pacific/Kiritimati"  # UTC+14
REGION = "customer__region"


def _duckdb_adapter() -> DbtAdapter:
    """The DuckDB demo's adapter, built on demand: the queries are generated from its catalog
    when this module is collected, before any fixture runs."""
    root_conftest = Path(__file__).resolve().parents[3] / "conftest.py"
    spec = importlib.util.spec_from_file_location("_prumi_root_conftest", root_conftest)
    assert spec and spec.loader
    root = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = root  # dataclasses look their module up here
    spec.loader.exec_module(root)
    if root.demo_is_stale():
        root.build_demo()
    config = {
        "semantic_manifest": str(root.DEMO_OUTPUTS[1]),
        "connection": {"type": "duckdb", "path": str(root.DEMO_OUTPUTS[0])},
    }
    return DbtAdapter.from_config(config, root.DEMO_DIR)


def _queries(adapter: DbtAdapter) -> list[SemanticQuery]:
    queries: list[SemanticQuery] = []
    grains = adapter.capabilities().time_grains
    for metric in adapter.discover().metrics:
        queries.append(SemanticQuery(metrics=(metric.name,), window=WINDOW))
        queries += [
            SemanticQuery(metrics=(metric.name,), time_grain=grain, window=WINDOW)
            for grain in grains
        ]
    queries += [
        SemanticQuery(metrics=("revenue",), dimensions=(REGION,)),
        SemanticQuery(
            metrics=("revenue",),
            filters=(Filter("customer__country", "eq", ("Côte d'Ivoire",)),),
        ),
        SemanticQuery(
            metrics=("revenue",),
            dimensions=(REGION,),
            filters=(Filter(REGION, "in", (None, "Western Europe")),),
        ),
        # The "data through" query.
        SemanticQuery(
            metrics=("revenue",),
            time_grain="day",
            order_by=(OrderBy("metric_time", descending=True),),
            limit=1,
        ),
        SemanticQuery(metrics=(), dimensions=(REGION,)),
    ]
    return queries


def query_id(query: SemanticQuery) -> str:
    parts = [",".join(query.metrics) or "values"]
    parts += query.dimensions
    if query.time_grain:
        parts.append(query.time_grain)
    if query.window:
        parts.append("window")
    parts += [f"{f.dimension}-{f.op}" for f in query.filters]
    if query.limit:
        parts.append(f"limit{query.limit}")
    return "-".join(parts)


QUERIES = _queries(_duckdb_adapter())


def normalized(result) -> tuple[tuple[str, ...], list[tuple[tuple[str, object], ...]]]:
    """Columns and sorted rows, each value paired with its Python type name, so an `int`
    against a `float` for the same number counts as a difference."""

    def cell(value: object) -> tuple[str, object]:
        if isinstance(value, float | Decimal):
            value = round(float(value), 6)
        return type(value).__name__, value

    rows = [tuple(cell(v) for v in row) for row in result.rows]
    return tuple(c.name for c in result.columns), sorted(rows, key=repr)


@pytest.fixture(scope="module")
def duck() -> DbtAdapter:
    return _duckdb_adapter()


@pytest.fixture(scope="module")
def postgres(postgres_demo) -> DbtAdapter:
    return DbtAdapter.from_config(
        {
            "semantic_manifest": str(postgres_demo.manifest_path),
            "connection": postgres_demo.connection_config(),
        },
        postgres_demo.manifest_path.parent,
    )


@pytest.fixture(scope="module")
def postgres_far_from_utc(postgres_demo) -> DbtAdapter:
    return DbtAdapter.from_config(
        {
            "semantic_manifest": str(postgres_demo.manifest_path),
            "connection": postgres_demo.connection_config(timezone=FAR_FROM_UTC),
        },
        postgres_demo.manifest_path.parent,
    )


@pytest.mark.parametrize("query", QUERIES, ids=query_id)
def test_postgres_gives_duckdb_s_numbers(duck, postgres, postgres_far_from_utc, query):
    expected = normalized(duck.execute(query, LOCAL_VIEWER))
    for adapter in (postgres, postgres_far_from_utc):
        assert normalized(adapter.execute(query, LOCAL_VIEWER)) == expected
