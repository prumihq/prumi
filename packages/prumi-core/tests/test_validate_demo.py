"""The validator against the demo catalog (AC 13, 14)."""

import datetime as dt

from prumi.validate import validate

AS_OF = dt.date(2026, 10, 15)


def spec(*blocks, filters=None) -> dict:
    return {
        "spec_version": 0,
        "dashboard": {"id": "demo", "title": "Demo"},
        "filters": filters
        or [
            {"id": "period", "type": "time", "default": {"last": 3, "grain": "month"}},
            {"id": "region", "type": "dimension", "dimension": "customer__region"},
        ],
        "data": list(blocks),
    }


def codes(report) -> list[tuple[str, str]]:
    return [(i.code, i.path) for i in report.issues]


def test_a_valid_demo_spec(demo_source):
    report = validate(
        spec(
            {
                "id": "kpi",
                "metrics": ["revenue", "average_order_value"],
                "filters": ["period", "region"],
            },
            {
                "id": "stock",
                "metrics": ["units_on_hand"],
                "dimensions": ["product__category"],
                "filters": ["period"],
            },
        ),
        None,
        demo_source,
        set(),
        as_of=AS_OF,
    )
    assert codes(report) == []


def test_r2_inventory_by_region(demo_source):
    block = {
        "id": "stock",
        "metrics": ["units_on_hand"],
        "dimensions": ["customer__region"],
        "filters": ["period"],
    }
    report = validate(spec(block), None, demo_source, set(), as_of=AS_OF)
    assert codes(report) == [("R2.invalid_dimension", "data[0].dimensions[0]")]
    assert "product__category" in report.errors[0].message  # the valid ones are listed


def test_r2_region_filter_on_inventory(demo_source):
    block = {"id": "stock", "metrics": ["units_on_hand"], "filters": ["period", "region"]}
    report = validate(spec(block), None, demo_source, set(), as_of=AS_OF)
    assert codes(report) == [("R2.filter_dimension_invalid", "data[0].filters[1]")]


def test_r2_a_longer_join_route_is_not_a_catalog_dimension(demo_source):
    block = {
        "id": "rev",
        "metrics": ["revenue"],
        "dimensions": ["order__customer__region"],
        "filters": ["period"],
    }
    report = validate(spec(block), None, demo_source, set(), as_of=AS_OF)
    assert codes(report) == [("R2.invalid_dimension", "data[0].dimensions[0]")]


def test_r7_misspelled_metric(demo_source):
    block = {"id": "kpi", "metrics": ["revnue"], "filters": ["period"]}
    report = validate(spec(block), None, demo_source, set(), as_of=AS_OF)
    [issue] = report.errors
    assert (issue.code, issue.path) == ("R7.unknown_metric", "data[0].metrics[0]")
    assert "Did you mean revenue" in issue.message
    assert "request_metric" in issue.hint


def test_r10_discount_rate_has_no_format(demo_source):
    block = {"id": "kpi", "metrics": ["discount_rate"], "filters": ["period"]}
    report = validate(spec(block), None, demo_source, set(), as_of=AS_OF)
    assert codes(report) == [("R10.no_format", "data[0].metrics[0]")]
