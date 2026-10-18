"""The validator: static rules first, then a dry run of every block, then the page check."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from prumi.adapters import LOCAL_VIEWER, Viewer
from prumi.engine import BlockRequestError, Engine
from prumi.issues import Issue, Report, error, warning
from prumi.source import Source
from prumi.spec import DashboardSpec
from prumi.validate._parse import parse_spec
from prumi.validate.html import DEFAULT_HOSTS, AllowedHosts, check_html
from prumi.validate.rules import check_spec

__all__ = ["AllowedHosts", "PageCheck", "parse_spec", "source_failed", "validate"]

_MAX_REASON = 500

# Renders the page with the dry run's block results and reports what it shows; with filter
# values, the page starts with those instead of the defaults.
PageCheck = Callable[..., list[Issue]]
MAX_CHECK_STATES = 5


def validate(
    spec: Any,
    html: str | None,
    source: Source,
    metric_request_ids: Collection[int],
    *,
    as_of: dt.date,
    hosts: AllowedHosts = DEFAULT_HOSTS,
    engine: Engine | None = None,
    viewer: Viewer = LOCAL_VIEWER,
    page_check: PageCheck | None = None,
    check_states: Sequence[Mapping[str, Any]] = (),
    layout_devices: Sequence[str] = (),
) -> Report:
    """Validate a spec (data or a DashboardSpec) and, when given, its HTML.

    With an engine, a spec without errors is dry-run: every block runs once with the filters'
    default values, as `viewer`. Failures are reported as query.failed, and the warnings in
    the block results (R3.window_snapped, R3.empty_window, R3.stale_data, result.truncated)
    at the block, so the agent sees them before publishing. With a page check too, HTML that
    passes all of that is rendered with the dry run's results, and its numbers are read.

    `check_states` are more filter states ({filter id: value}, at most 5) to dry-run and
    render the same way. Their issues name the state; one the default state already has
    isn't repeated. The page check measures the layout on a desktop render, and on
    `layout_devices` too ("phone").
    """
    devices = ("desktop", *(d for d in layout_devices if d != "desktop"))
    parsed, issues = parse_spec(spec)
    if parsed is None:
        return Report.of(issues)
    issues = list(check_spec(parsed, source, metric_request_ids, as_of))
    if html is not None:
        issues += check_html(parsed, html, hosts)
    if engine is not None and not _has_errors(issues):
        ran, results = _dry_run(parsed, engine, viewer)
        issues += ran
        if html is not None and page_check is not None and not _has_errors(issues):
            issues += page_check(parsed, html, results, devices=devices)
        seen = {(i.code, i.path, i.message) for i in issues}
        for n, state in enumerate(check_states[:MAX_CHECK_STATES]):
            checks = _check_state(parsed, html, engine, viewer, page_check, n, state, devices)
            for issue in checks:
                if (issue.code, issue.path, issue.message) in seen:
                    continue
                label = json.dumps(state, ensure_ascii=False, sort_keys=True)
                named = f"With the filters {label}: {issue.message}"
                issues.append(issue.model_copy(update={"message": named}))
    return Report.of(issues)


def _check_state(
    spec: DashboardSpec,
    html: str | None,
    engine: Engine,
    viewer: Viewer,
    page_check: PageCheck | None,
    n: int,
    state: Mapping[str, Any],
    devices: Sequence[str],
) -> list[Issue]:
    known = {f.id for f in spec.filters}
    unknown = [f for f in state if f not in known]
    if unknown:
        return [
            error(
                "spec.unknown_filter",
                f"check_states[{n}].filters.{f}",
                f"The dashboard has no filter {f!r} (its filters are: "
                f"{', '.join(sorted(known)) or 'none'}).",
                "Name only the spec's filters in a check state.",
            )
            for f in unknown
        ]
    ran, results = _dry_run(spec, engine, viewer, state)
    if html is None or page_check is None or _has_errors(ran):
        return ran
    return ran + page_check(spec, html, results, state, devices=devices)


def source_failed(path: str, what: str, exc: BaseException) -> Issue:
    """query.failed for an error the source raised. The database's own message can hold
    values, so it's logged nowhere and sent nowhere: only the error's type."""
    return error(
        "query.failed",
        path,
        f"The source couldn't run {what} ({type(exc).__name__}).",
        "Check the metrics, dimensions and filters with describe_metric, or ask the data team: "
        "the server's log has the error type.",
    )


def _has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def _dry_run(
    spec: DashboardSpec, engine: Engine, viewer: Viewer, values: Mapping[str, Any] | None = None
) -> tuple[list[Issue], dict[str, dict[str, Any]]]:
    """Issues from running every block once, and the block results (as the browser gets them).
    `values` are filter values in place of the defaults."""
    issues: list[Issue] = []
    results: dict[str, dict[str, Any]] = {}
    for i, block in enumerate(spec.data):
        try:
            origin = f"dry run {spec.dashboard.id}/{block.id}"
            result = engine.run_block(spec, block.id, dict(values or {}), viewer, origin)
        except Exception as exc:  # any failure of the source counts; report it, don't crash
            if isinstance(exc, BlockRequestError):
                reason = " ".join(issue.message for issue in exc.issues)
            else:
                # The database's own message can hold values: only its type goes out.
                reason = f"the source raised {type(exc).__name__}."
            if len(reason) > _MAX_REASON:
                reason = reason[:_MAX_REASON] + "..."
            issues.append(
                error(
                    "query.failed",
                    f"data[{i}]",
                    f"Running the block {block.id!r} with the filters' "
                    + ("values" if values else "default values")
                    + " failed: "
                    + reason,
                    "Check the block's metrics, dimensions and filter defaults with "
                    "describe_metric and query, then validate again.",
                )
            )
            continue
        results[block.id] = result.to_json()
        # What the viewer will see in the block's details: window and freshness warnings.
        for w in result.warnings:
            issues.append(warning(w.code, f"data[{i}]", f"Block {block.id!r}: {w.message}", w.hint))
    return issues, results
