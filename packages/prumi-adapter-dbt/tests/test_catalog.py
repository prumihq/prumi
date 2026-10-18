"""Catalog extraction: kinds, additivity, formats, owners, dimensions, relations."""

import pytest
from prumi.adapters import LOCAL_VIEWER, AdapterStartupError, Filter, Format, SemanticQuery
from prumi_adapter_dbt._metricflow import SourceDimension, SourceMetric
from prumi_adapter_dbt.catalog import build_catalog, parse_format, shortest_paths

USD = Format("currency", "USD", 2)

# The demo's metrics: name -> (kind, aggregation, additive, format, owner)
DEMO_METRICS = {
    "revenue": ("simple", "sum", True, USD, "sales-analytics"),
    "orders": ("simple", "count", True, Format("integer", "orders"), "sales-analytics"),
    "units_sold": ("simple", "sum", True, Format("integer", "units"), "sales-analytics"),
    "average_order_value": ("ratio", None, False, USD, "sales-analytics"),
    "customers": (
        "simple",
        "count_distinct",
        False,
        Format("integer", "customers"),
        "sales-analytics",
    ),
    "gross_margin": ("derived", None, False, USD, "finance"),
    "gross_margin_pct": ("ratio", None, False, Format("percent", None, 1), "finance"),
    "units_on_hand": ("simple", "sum", False, Format("integer", "units"), "operations"),
    "discount_rate": ("ratio", None, False, Format(), None),
    "cost": ("simple", "sum", True, USD, "finance"),
    "gross_sales": ("simple", "sum", True, USD, "sales-analytics"),
    "discounts": ("simple", "sum", True, USD, "sales-analytics"),
}
CUSTOMER = {"customer__region", "customer__country", "customer__segment"}
ALL_DIMENSIONS = CUSTOMER | {"order__channel", "product__category"}


def test_demo_metrics_match_the_spec_table(adapter):
    catalog = adapter.discover()
    got = {m.name: (m.kind, m.aggregation, m.additive, m.format, m.owner) for m in catalog.metrics}
    assert got == DEMO_METRICS


def test_demo_metric_labels_and_descriptions(adapter):
    metrics = {m.name: m for m in adapter.discover().metrics}
    assert metrics["average_order_value"].label == "Average order value"
    assert metrics["customers"].description.startswith("Number of distinct customers")
    assert all(m.filter is None for m in metrics.values())


def test_the_only_catalog_warning_is_discount_rate_without_format(adapter):
    [warning] = adapter.discover().warnings
    assert (warning.code, warning.subject) == ("R10.no_format", "discount_rate")
    assert "config.meta.prumi.format" in warning.message


def test_demo_dimensions_are_the_five_one_hop_names_plus_metric_time(adapter):
    dimensions = {d.name: d for d in adapter.discover().dimensions}
    assert set(dimensions) == ALL_DIMENSIONS | {"metric_time"}
    assert dimensions["customer__region"].label == "Region"
    assert dimensions["product__category"].description == "Category of the product."


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        (["revenue"], ALL_DIMENSIONS),
        (["gross_margin_pct", "discount_rate"], ALL_DIMENSIONS),
        (["orders"], CUSTOMER | {"order__channel"}),
        (["average_order_value"], CUSTOMER | {"order__channel"}),  # orders have no product
        (["units_on_hand"], {"product__category"}),
        (["revenue", "units_on_hand"], {"product__category"}),
        (["customers", "units_on_hand"], set()),
        ([], ALL_DIMENSIONS),
    ],
)
def test_relations(adapter, metrics, expected):
    assert adapter.relations(metrics) == frozenset(expected)


def test_relations_of_an_unknown_metric(adapter):
    with pytest.raises(ValueError, match="revnue"):
        adapter.relations(["revnue"])


@pytest.mark.parametrize(
    "query",
    [
        # Dropped by the shortest-path rule, although MetricFlow would accept it.
        SemanticQuery(metrics=("revenue",), dimensions=("order__customer__region",)),
        SemanticQuery(
            metrics=("revenue",),
            filters=(Filter("order__customer__region", "eq", ("Asia Pacific",)),),
        ),
        # R2: inventory isn't joinable to customers.
        SemanticQuery(metrics=("units_on_hand",), dimensions=("customer__region",)),
        SemanticQuery(metrics=(), dimensions=("order__customer__region",)),
    ],
)
def test_execute_accepts_only_catalog_dimensions(adapter, query):
    with pytest.raises(ValueError, match="Not a dimension of"):
        adapter.execute(query, LOCAL_VIEWER)


# The rules, on made-up metrics


def dim(name: str, categorical: bool = True) -> SourceDimension:
    *path, element = name.split("__")
    return SourceDimension(name, element, tuple(path), categorical)


def names(dimensions) -> list[str]:
    return [d.name for d in dimensions]


def test_shortest_path_drops_longer_routes_to_the_same_attribute():
    found = [dim("order__customer__region"), dim("customer__region"), dim("order__channel")]
    assert names(shortest_paths(found)) == ["customer__region", "order__channel"]


def test_shortest_path_keeps_an_attribute_only_reachable_through_two_hops():
    assert names(shortest_paths([dim("order__customer__region")])) == ["order__customer__region"]


def test_shortest_path_keeps_ties_and_separates_entities():
    found = [
        dim("order__customer__region"),
        dim("shipment__customer__region"),  # same length: Prumi can't tell which is meant
        dim("store__region"),  # another entity's region
    ]
    assert names(shortest_paths(found)) == [
        "order__customer__region",
        "shipment__customer__region",
        "store__region",
    ]


def test_the_rule_applies_per_metric():
    # `a` reaches region directly, `b` only through orders: no shared name.
    catalog = build_catalog(
        [
            SourceMetric("a", "simple", aggregation="sum", dimensions=(dim("customer__region"),)),
            SourceMetric(
                "b",
                "simple",
                aggregation="sum",
                dimensions=(dim("order__customer__region"), dim("metric_time__day", False)),
            ),
        ]
    )
    assert catalog.relations(["a"]) == {"customer__region"}
    assert catalog.relations(["b"]) == {"order__customer__region"}
    assert catalog.relations(["a", "b"]) == frozenset()
    assert [d.name for d in catalog.catalog.dimensions] == [
        "customer__region",
        "order__customer__region",
        "metric_time",
    ]


@pytest.mark.parametrize(
    ("kind", "aggregation", "non_additive", "additive"),
    [
        ("simple", "sum", None, True),
        ("simple", "count", None, True),
        ("simple", "sum_boolean", None, True),
        ("simple", "sum", "snapshot_date", False),
        ("simple", "count_distinct", None, False),
        ("simple", "average", None, False),
        ("simple", "min", None, False),
        ("simple", "max", None, False),
        ("simple", "median", None, False),
        ("simple", "percentile", None, False),
        ("ratio", None, None, False),
        ("derived", None, None, False),
        ("cumulative", None, None, False),
        ("conversion", None, None, False),
    ],
)
def test_additivity(kind, aggregation, non_additive, additive):
    source = SourceMetric("m", kind, aggregation=aggregation, non_additive_dimension=non_additive)
    [metric] = build_catalog([source]).catalog.metrics
    assert metric.additive is additive
    assert metric.aggregation == (aggregation if kind == "simple" else None)


def test_labels_owner_and_filter_fallbacks():
    source = SourceMetric(
        "m",
        "simple",
        aggregation="sum",
        meta={"owner": ["not", "a", "string"]},
        filter="{{ Dimension('order__channel') }} = 'web'",
    )
    [metric] = build_catalog([source]).catalog.metrics
    assert (metric.label, metric.description, metric.owner) == ("m", "", None)
    assert metric.filter == "{{ Dimension('order__channel') }} = 'web'"


def test_an_unknown_metric_type_fails_startup():
    with pytest.raises(AdapterStartupError, match="'magic'"):
        build_catalog([SourceMetric("m", "magic")])


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ({"format": "currency", "unit": "EUR"}, Format("currency", "EUR")),
        ({"format": "percent", "decimals": 0}, Format("percent", None, 0)),
        ({"format": "integer", "unit": "orders"}, Format("integer", "orders")),
        ({"format": "number", "decimals": 3}, Format("number", None, 3)),
    ],
)
def test_valid_formats(declared, expected):
    assert parse_format("m", {"prumi": declared}) == (expected, None)


@pytest.mark.parametrize(
    ("meta", "problem"),
    [
        ({}, "declares no format"),
        ({"prumi": "currency"}, "declares no format"),
        ({"prumi": {"unit": "USD"}}, "declares no format"),
        ({"prumi": {"format": "money"}}, "'money' isn't one of"),
        ({"prumi": {"format": "currency"}}, "3-letter"),
        ({"prumi": {"format": "currency", "unit": "usd"}}, "3-letter"),
        ({"prumi": {"format": "currency", "unit": "Dollars"}}, "3-letter"),
        ({"prumi": {"format": "integer", "unit": 3}}, "unit must be text"),
        ({"prumi": {"format": "number", "decimals": "2"}}, "decimals"),
        ({"prumi": {"format": "number", "decimals": True}}, "decimals"),
        ({"prumi": {"format": "number", "decimals": -1}}, "decimals"),
    ],
)
def test_missing_or_invalid_formats_fall_back_to_number_with_a_warning(meta, problem):
    metric_format, warning = parse_format("m", meta)
    assert metric_format == Format()
    assert warning.code == "R10.no_format" and warning.subject == "m"
    assert problem in warning.message
