"""Filter rendering: quoting, `{`/`}` rejection, literals and NULL semantics (AC 6)."""

import duckdb
import pytest
from prumi.adapters import LOCAL_VIEWER, Filter, SemanticQuery
from prumi_adapter_dbt._query import render_filter

REGION = "{{ Dimension('customer__region') }}"


@pytest.mark.parametrize(
    ("f", "where"),
    [
        (Filter("customer__region", "eq", ("North",)), f"{REGION} = 'North'"),
        (Filter("customer__region", "eq", (None,)), f"{REGION} IS NULL"),
        (Filter("customer__region", "in", ("A", "B")), f"{REGION} IN ('A', 'B')"),
        (Filter("customer__region", "in", ("A", None)), f"({REGION} IN ('A') OR {REGION} IS NULL)"),
        (Filter("customer__region", "in", (None,)), f"{REGION} IS NULL"),
        (Filter("customer__region", "ne", ("A",)), f"({REGION} <> 'A' OR {REGION} IS NULL)"),
        (Filter("customer__region", "ne", (None,)), f"{REGION} IS NOT NULL"),
        (
            Filter("customer__region", "not_in", ("A", "B")),
            f"({REGION} NOT IN ('A', 'B') OR {REGION} IS NULL)",
        ),
        (
            Filter("customer__region", "not_in", ("A", None)),
            f"({REGION} NOT IN ('A') AND {REGION} IS NOT NULL)",
        ),
        (Filter("customer__region", "not_in", (None,)), f"{REGION} IS NOT NULL"),
        (Filter("customer__region", "eq", ("Côte d'Ivoire",)), f"{REGION} = 'Côte d''Ivoire'"),
        (Filter("customer__region", "eq", ("''",)), f"{REGION} = ''''''"),
    ],
)
def test_rendering(f, where):
    assert render_filter(f) == where


@pytest.mark.parametrize(
    ("op", "value", "literal"),
    [
        ("lt", 5, "5"),
        ("le", -2, "-2"),
        ("gt", 2.5, "2.5"),
        ("ge", 1e-05, "1e-05"),
        ("eq", True, "TRUE"),
        ("eq", False, "FALSE"),
        ("ge", "2026-01-01", "'2026-01-01'"),
    ],
)
def test_literals(op, value, literal):
    symbol = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "="}[op]
    assert (
        render_filter(Filter("d", op, (value,))) == "{{ Dimension('d') }} " + f"{symbol} {literal}"
    )


@pytest.mark.parametrize("value", ["{{ 1 }}", "{% raw %}", "{# x #}", "a}", "{"])
def test_braces_are_rejected(value):
    with pytest.raises(ValueError, match="can't contain"):
        render_filter(Filter("customer__region", "in", ("North", value)))


@pytest.mark.parametrize(
    "f",
    [
        Filter("customer__region", "eq", (float("nan"),)),
        Filter("customer__region", "lt", (float("inf"),)),
        Filter("customer__region", "lt", (None,)),
        Filter("customer__region') }} OR 1=1 --", "eq", ("x",)),
        Filter("customer__region", "eq", (b"bytes",)),
    ],
)
def test_invalid_filters_are_rejected(f):
    with pytest.raises(ValueError):
        render_filter(f)


# Against the demo warehouse


def revenue(adapter, *filters: Filter) -> float | None:
    [(value,)] = adapter.execute(
        SemanticQuery(metrics=("revenue",), filters=filters), LOCAL_VIEWER
    ).rows
    return value


def hand_revenue(demo_project, where: str) -> float:
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        return conn.execute(
            "SELECT sum(i.revenue)::double FROM main.order_items i"
            f" LEFT JOIN main.customers c USING (customer_id) WHERE {where}"
        ).fetchone()[0]


@pytest.mark.parametrize(
    ("op", "values", "where"),
    [
        ("eq", ("Asia Pacific",), "c.region = 'Asia Pacific'"),
        ("eq", (None,), "c.region IS NULL"),
        ("in", ("Asia Pacific", None), "c.region = 'Asia Pacific' OR c.region IS NULL"),
        ("in", (None,), "c.region IS NULL"),
        ("ne", ("Asia Pacific",), "c.region IS DISTINCT FROM 'Asia Pacific'"),  # keeps NULL
        ("ne", (None,), "c.region IS NOT NULL"),
        (
            "not_in",
            ("Asia Pacific", "North America"),
            "c.region NOT IN ('Asia Pacific', 'North America') OR c.region IS NULL",
        ),
        ("not_in", ("Asia Pacific", None), "c.region <> 'Asia Pacific'"),
    ],
)
def test_null_semantics(adapter, demo_project, op, values, where):
    got = revenue(adapter, Filter("customer__region", op, values))
    assert got == pytest.approx(hand_revenue(demo_project, where))


def test_ne_and_not_in_keep_the_null_rows(adapter, demo_project):
    everything = revenue(adapter)
    in_region = revenue(adapter, Filter("customer__region", "eq", ("Asia Pacific",)))
    null_region = revenue(adapter, Filter("customer__region", "eq", (None,)))
    others = revenue(adapter, Filter("customer__region", "not_in", ("Asia Pacific",)))
    assert null_region > 0
    assert in_region + others == pytest.approx(everything)


def test_a_value_with_a_quote_returns_its_rows(adapter, demo_project):
    got = revenue(adapter, Filter("customer__country", "eq", ("Côte d'Ivoire",)))
    assert got > 0
    assert got == pytest.approx(hand_revenue(demo_project, "c.country = 'Côte d''Ivoire'"))


@pytest.mark.parametrize(
    "value",
    ["x' OR '1'='1", "x') OR ('1'='1", "x' OR TRUE --", "'; DROP TABLE main.orders; --"],
)
def test_injection_attempts_match_nothing(adapter, value):
    query = SemanticQuery(
        metrics=("revenue",),
        dimensions=("customer__country",),
        filters=(Filter("customer__country", "in", (value,)),),
    )
    assert adapter.execute(query, LOCAL_VIEWER).rows == ()


def test_braces_are_rejected_before_compiling(adapter):
    query = SemanticQuery(
        metrics=("revenue",), filters=(Filter("customer__country", "eq", ("{{ 7 * 7 }}",)),)
    )
    with pytest.raises(ValueError, match="can't contain"):
        adapter.execute(query, LOCAL_VIEWER)
