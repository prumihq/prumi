"""Dashboard spec models, loading and the committed JSON Schema (spec section 6, AC 16)."""

import json
from pathlib import Path

import pytest
from prumi.spec import DashboardSpec, DimensionFilter, TimeFilter, load_spec_file, parse_yaml
from prumi.spec.schema import SCHEMA_PATH, render_schema
from prumi.time import LastWindow
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures" / "specs"
EXAMPLE = FIXTURES / "spec-example.yaml"  # the example in spec section 6, verbatim


def minimal(**changes) -> dict:
    spec = {
        "spec_version": 0,
        "dashboard": {"id": "sales", "title": "Sales"},
        "filters": [{"id": "period", "type": "time", "default": {"last": 12, "grain": "week"}}],
        "data": [{"id": "kpi", "metrics": ["revenue"], "filters": ["period"]}],
    }
    spec.update(changes)
    return spec


def test_the_committed_json_schema_is_up_to_date():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render_schema(), (
        "The JSON Schema is out of date: run `uv run python -m prumi.spec.schema`."
    )


def test_the_spec_example_parses():
    spec = DashboardSpec.model_validate(load_spec_file(EXAMPLE))
    assert spec.dashboard.id == "sales-weekly"
    period, region = spec.filters
    assert isinstance(period, TimeFilter) and period.default == LastWindow(last=12, grain="week")
    assert period.calendar == "standard"
    assert isinstance(region, DimensionFilter) and region.default == ()
    assert [b.id for b in spec.data] == [
        "kpi_revenue",
        "revenue_by_region",
        "weekly_trend",
        "monthly_trend",
    ]
    assert spec.block("revenue_by_region").top_n == 5
    assert spec.block("monthly_trend").window == LastWindow(last=24, grain="month")
    [interaction] = spec.interactions
    assert (interaction.from_, interaction.on, interaction.sets_filter) == (
        "revenue_by_region",
        "click",
        "region",
    )
    assert spec.metric_requests == (3,)
    assert spec.presentation.bindings["#kpi"] == "kpi_revenue"


def test_yaml_keeps_on_as_a_key():
    # YAML 1.1 would read `on:` as the boolean True.
    assert parse_yaml("on: click\nyes: no\nflag: true") == {
        "on": "click",
        "yes": "no",
        "flag": True,
    }


def test_json_and_yaml_files_load_the_same(tmp_path):
    data = load_spec_file(EXAMPLE)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(data, default=str))
    assert DashboardSpec.model_validate(load_spec_file(path)) == DashboardSpec.model_validate(data)


def test_defaults_are_filled_in():
    spec = DashboardSpec.model_validate(minimal())
    block = spec.data[0]
    assert (block.dimensions, block.time_grain, block.window, block.compare) == (
        (),
        None,
        None,
        None,
    )
    assert (block.top_n, block.others, block.total, block.sort) == (None, False, False, None)
    assert spec.interactions == () and spec.metric_requests == ()
    assert spec.presentation.bindings == {}


def test_specs_are_immutable():
    spec = DashboardSpec.model_validate(minimal())
    with pytest.raises(ValidationError):
        spec.data[0].top_n = 3


@pytest.mark.parametrize(
    "changes",
    [
        {"spec_version": 1},
        {"dashboard": {"id": "Sales Weekly", "title": "Sales"}},
        {"dashboard": {"id": "-sales", "title": "Sales"}},
        {"dashboard": {"id": "sales", "title": ""}},
        {"surprise": True},
        {"data": [{"id": "kpi", "metrics": ["revenue"]}]},  # filters is required
        {"data": [{"id": "kpi", "metrics": [], "filters": []}]},
        {"data": [{"id": "kpi", "metrics": ["m"] * 11, "filters": []}]},
        {
            "data": [
                {"id": "kpi", "metrics": ["m"], "dimensions": ["a", "b", "c", "d"], "filters": []}
            ]
        },
        {"data": [{"id": "kpi", "metrics": ["m"], "filters": [], "top_n": 0}]},
        {"data": [{"id": "kpi", "metrics": ["m"], "filters": [], "top_n": 101}]},
        {"data": [{"id": "kpi", "metrics": ["m"], "filters": [], "time_grain": "hour"}]},
        {"data": [{"id": "kpi", "metrics": ["m"], "filters": [], "compare": "last_week"}]},
        {"data": [{"id": "_kpi", "metrics": ["m"], "filters": []}]},
        {
            "data": [
                {
                    "id": "kpi",
                    "metrics": ["m"],
                    "filters": [],
                    "window": {"last": 500, "grain": "day"},
                }
            ]
        },
        {"filters": [{"id": "period", "type": "time", "default": {"last": 3}}]},
        {"filters": [{"id": "period", "type": "range", "default": {}}]},
        {"filters": [{"id": "region", "type": "dimension", "dimension": "r", "default": [[1]]}]},
        {"interactions": [{"from": "kpi", "on": "hover", "sets_filter": "period"}]},
    ],
)
def test_shape_violations(changes):
    with pytest.raises(ValidationError):
        DashboardSpec.model_validate(minimal(**changes))
