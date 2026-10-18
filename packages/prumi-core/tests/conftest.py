"""Fixtures for core tests: a fake source with a demo-like catalog, no warehouse needed."""

import datetime as dt

import pytest
from prumi.adapters import (
    METRIC_TIME,
    Capabilities,
    Catalog,
    CatalogWarning,
    Column,
    Dimension,
    Format,
    Metric,
    Result,
)
from prumi.source import Source

AS_OF = dt.date(2026, 10, 15)
USD = Format("currency", "USD", 2)
SALES = frozenset({"customer__region", "customer__country", "customer__segment", "order__channel"})


def _metric(name, kind="simple", additive=False, fmt=None, aggregation=None) -> Metric:
    return Metric(
        name=name,
        label=name.replace("_", " ").capitalize(),
        description=f"The {name}.",
        kind=kind,
        aggregation=aggregation,
        additive=additive,
        format=fmt or Format(),
        owner="sales-analytics",
        filter=None,
    )


METRICS = {
    "revenue": (
        _metric("revenue", additive=True, fmt=USD, aggregation="sum"),
        SALES | {"product__category"},
    ),
    "orders": (
        _metric("orders", additive=True, fmt=Format("integer", "orders"), aggregation="count"),
        SALES,
    ),
    "average_order_value": (_metric("average_order_value", "ratio", fmt=USD), SALES),
    "customers": (
        _metric("customers", fmt=Format("integer", "customers"), aggregation="count_distinct"),
        SALES,
    ),
    "gross_margin_pct": (
        _metric("gross_margin_pct", "ratio", fmt=Format("percent", None, 1)),
        SALES | {"product__category"},
    ),
    "units_on_hand": (
        _metric("units_on_hand", fmt=Format("integer", "units"), aggregation="sum"),
        frozenset({"product__category"}),
    ),
    "discount_rate": (_metric("discount_rate", "ratio"), SALES | {"product__category"}),
    # Valid in the catalog, but the warehouse fails every query for it.
    "broken_metric": (_metric("broken_metric", fmt=Format("integer")), SALES),
}


class FakeAdapter:
    """Answers every query with one row of placeholder values, like a tiny warehouse."""

    name = "fake"

    def __init__(self):
        self.queries = []

    def capabilities(self):
        return Capabilities(identity_mode="shared_account")

    def discover(self):
        dims = sorted(set().union(*(d for _, d in METRICS.values())))
        return Catalog(
            metrics=tuple(m for m, _ in METRICS.values()),
            dimensions=(
                *(Dimension(d, d.split("__")[-1].capitalize(), "") for d in dims),
                Dimension(METRIC_TIME, "Time", ""),
            ),
            warnings=(CatalogWarning("R10.no_format", "discount_rate", "No format declared."),),
        )

    def relations(self, metrics):
        if not metrics:
            return frozenset().union(*(d for _, d in METRICS.values()))
        return frozenset.intersection(*(METRICS[m][1] for m in metrics))

    def execute(self, query, viewer):
        self.queries.append(query)
        if "broken_metric" in query.metrics:
            raise RuntimeError("the warehouse refused the query")
        columns = (
            *((Column(METRIC_TIME, "time"),) if query.time_grain else ()),
            *(Column(d, "dimension") for d in query.dimensions),
            *(Column(m, "metric") for m in query.metrics),
        )
        start = query.window.start if query.window else dt.date(2026, 1, 1)
        row = tuple(
            start if c.kind == "time" else "x" if c.kind == "dimension" else 1.0 for c in columns
        )
        return Result(columns=columns, rows=(row,), row_count=1)


@pytest.fixture
def as_of():
    return AS_OF


@pytest.fixture
def fake_adapter():
    return FakeAdapter()


@pytest.fixture
def fake_source(fake_adapter):
    return Source("default", fake_adapter)


@pytest.fixture(scope="session")
def demo_config(demo_project):
    from prumi.config import load_config

    return load_config(demo_project.config_path)


@pytest.fixture(scope="session")
def demo_source(demo_config):
    """The demo's dbt source, loaded through the adapter's entry point like the server does."""
    return Source.from_config(demo_config.sources["default"])
