"""DuckDB and Postgres give the same numbers, with the same Python types, for the same queries.

The other tests compare each warehouse with hand-written SQL on that same warehouse, which
would pass even if the two disagreed. Here one `SemanticQuery` goes through a DuckDB adapter
and two Postgres adapters (the connection's own time zone, and one far from UTC) and the
results must be identical.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from prumi.adapters import LOCAL_VIEWER, DateRange, Filter, OrderBy, SemanticQuery
from prumi.time import GRAINS
from prumi_adapter_dbt.adapter import DbtAdapter

WINDOW = DateRange(dt.date(2025, 7, 1), dt.date(2026, 9, 30))
FAR_FROM_UTC = "Pacific/Kiritimati"  # UTC+14
REGION = "customer__region"


# Listed here so collecting the tests builds nothing; a test checks the list against the catalog.
METRICS = (
    "average_order_value",
    "cost",
    "customers",
    "discount_rate",
    "discounts",
    "gross_margin",
    "gross_margin_pct",
    "gross_sales",
    "orders",
    "revenue",
    "units_on_hand",
    "units_sold",
)


def _queries() -> list[SemanticQuery]:
    queries: list[SemanticQuery] = []
    for metric in METRICS:
        queries.append(SemanticQuery(metrics=(metric,), window=WINDOW))
        queries += [
            SemanticQuery(metrics=(metric,), time_grain=grain, window=WINDOW) for grain in GRAINS
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


QUERIES = _queries()


def normalized(result) -> tuple[tuple[str, ...], list[tuple[tuple[str, object], ...]]]:
    """Columns and sorted rows, each value paired with its Python type name, so an `int`
    against a `float` for the same number counts as a difference."""

    def cell(value: object) -> tuple[str, object]:
        name = type(value).__name__
        if isinstance(value, float | Decimal):
            value = round(float(value), 6)
        return name, value

    rows = [tuple(cell(v) for v in row) for row in result.rows]
    return tuple(c.name for c in result.columns), sorted(rows, key=repr)


@pytest.fixture(scope="module")
def duck(demo_project) -> DbtAdapter:
    config = {
        "semantic_manifest": str(demo_project.manifest_path),
        "connection": {"type": "duckdb", "path": str(demo_project.db_path)},
    }
    return DbtAdapter.from_config(config, demo_project.root)


def test_the_metric_list_is_the_demo_catalog(duck):
    assert set(METRICS) == {m.name for m in duck.discover().metrics}


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
@pytest.mark.postgres
def test_postgres_gives_duckdb_s_numbers(duck, postgres, postgres_far_from_utc, query):
    expected = normalized(duck.execute(query, LOCAL_VIEWER))
    for adapter in (postgres, postgres_far_from_utc):
        assert normalized(adapter.execute(query, LOCAL_VIEWER)) == expected
