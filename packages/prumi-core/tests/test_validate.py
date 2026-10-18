"""The validator: one fixture per code, each producing that code and only it (AC 12, 14)."""

import datetime as dt
from pathlib import Path

import pytest
from prumi.engine import Engine
from prumi.spec import load_spec_file
from prumi.validate import validate

FIXTURES = Path(__file__).parent / "fixtures" / "validator"
METRIC_REQUESTS = {1, 2}

# Section 7's codes, plus spec.invalid_interaction (section 13's interaction check).
# html.too_large is tested in test_html.py, with a generated page.
CODES = {
    "spec.invalid",
    "spec.duplicate_id",
    "spec.unknown_filter",
    "spec.unknown_block",
    "spec.unsupported_calendar",
    "spec.invalid_interaction",
    "R2.invalid_dimension",
    "R2.filter_dimension_invalid",
    "R3.no_window",
    "R3.window_too_short",
    "R3.partial_in_comparison",
    "R4.grain_mismatch",
    "R4.compare_with_time_grain",
    "R5.multiple_windows",
    "R6.top_n_shape",
    "R6.others_requires_top_n",
    "R6.total_requires_grouping",
    "R7.unknown_metric",
    "R7.unknown_metric_request",
    "R10.no_format",
    "R10.mixed_units",
    "R8.embedded_numbers",
    "R8.numbers_in_text",
    "R8.numbers_in_table",
    "html.binding_selector",
    "html.unknown_block",
    "html.unbound_block",
    "html.script_host",
    "html.unpinned_script",
    "query.failed",
    "filter.invalid_value",
    # Warnings in the dry-run block results.
    "R3.window_snapped",
    "R3.empty_window",
    "R3.stale_data",
    "result.truncated",
}


def fixture_header(path: Path) -> dict[str, str]:
    header = {}
    for line in path.read_text().splitlines():
        if not line.startswith("# ") or ": " not in line:
            break
        key, value = line[2:].split(": ", 1)
        header[key] = value
    return header


FIXTURE_FILES = sorted(p for p in FIXTURES.glob("*.yaml") if not p.name.startswith("_"))


def run(path: Path, source, as_of):
    pinned = fixture_header(path).get("as_of")
    as_of = dt.date.fromisoformat(pinned) if pinned else as_of
    html_path = path.with_suffix(".html")
    html = html_path.read_text() if html_path.exists() else None
    engine = Engine(source, as_of)  # static checks, then the dry run
    return validate(load_spec_file(path), html, source, METRIC_REQUESTS, as_of=as_of, engine=engine)


def test_the_base_spec_and_page_are_valid(fake_source, as_of):
    report = run(FIXTURES / "_base.yaml", fake_source, as_of)  # with _base.html
    assert report.ok and not report.warnings, report.issues


def test_every_code_has_a_fixture():
    assert CODES <= {fixture_header(p)["code"] for p in FIXTURE_FILES}


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_a_fixture_produces_its_code_and_only_it(path, fake_source, as_of):
    header = fixture_header(path)
    report = run(path, fake_source, as_of)
    [issue] = report.issues
    assert (issue.code, issue.path) == (header["code"], header["path"])
    assert issue.severity == header.get("severity", "error")
    assert issue.message and issue.hint
    assert report.ok is (issue.severity == "warning")


def test_unknown_metric_suggests_close_names_and_request_metric(fake_source, as_of):
    report = run(FIXTURES / "R7.unknown_metric.yaml", fake_source, as_of)
    [issue] = report.errors
    assert "'revnue'" in issue.message and "Did you mean revenue" in issue.message
    assert "request_metric" in issue.hint


def test_spec_invalid_reports_every_pydantic_error_with_spec_paths(fake_source, as_of):
    spec = load_spec_file(FIXTURES / "_base.yaml")
    spec["data"][0]["metrics"] = []
    spec["data"][1]["top_n"] = 500
    spec["filters"][0]["default"] = {"last": 0, "grain": "week"}
    spec["filters"][1]["colour"] = "red"
    del spec["data"][2]["filters"]
    report = validate(spec, None, fake_source, METRIC_REQUESTS, as_of=as_of)
    assert {(i.code, i.path) for i in report.errors} == {
        ("spec.invalid", "data[0].metrics"),
        ("spec.invalid", "data[1].top_n"),
        ("spec.invalid", "filters[0].default.last"),
        ("spec.invalid", "filters[1].colour"),
        ("spec.invalid", "data[2].filters"),
    }
    messages = {i.path: i.message for i in report.errors}
    assert messages["filters[1].colour"] == "Unknown field 'colour'."
    assert messages["data[2].filters"] == "Missing required field 'filters'."


def test_issues_found_through_a_shared_filter_are_reported_once(fake_source, as_of):
    spec = load_spec_file(FIXTURES / "_base.yaml")
    spec["filters"][0]["default"] = {"start": "2026-01-15", "end": "2026-02-10", "grain": "month"}
    report = validate(spec, None, fake_source, METRIC_REQUESTS, as_of=as_of)
    assert [(i.code, i.path) for i in report.errors] == [
        ("R3.window_too_short", "filters[0].default")
    ]


def test_a_time_filter_is_not_an_interaction_target(fake_source, as_of):
    spec = load_spec_file(FIXTURES / "_base.yaml")
    spec["interactions"][0]["sets_filter"] = "period"
    report = validate(spec, None, fake_source, METRIC_REQUESTS, as_of=as_of)
    assert [(i.code, i.path) for i in report.errors] == [
        ("spec.invalid_interaction", "interactions[0].sets_filter")
    ]


def test_the_dry_run_uses_the_filter_defaults(fake_source, fake_adapter, as_of):
    report = run(FIXTURES / "_base.yaml", fake_source, as_of)
    assert report.ok
    windows = {q.window for q in fake_adapter.queries if q.window is not None}
    assert {(w.start.isoformat(), w.end.isoformat()) for w in windows} >= {
        ("2026-07-20", "2026-10-11")  # {last: 12, grain: week} on 2026-10-15
    }


def test_no_dry_run_when_static_checks_fail(fake_source, fake_adapter, as_of):
    report = run(FIXTURES / "R6.others_requires_top_n.yaml", fake_source, as_of)
    assert not report.ok and fake_adapter.queries == []


def test_query_failed_says_why(fake_source, as_of):
    [issue] = run(FIXTURES / "query.failed.yaml", fake_source, as_of).errors
    assert "'trend'" in issue.message and "the warehouse refused the query" in issue.message
