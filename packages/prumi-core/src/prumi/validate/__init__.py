"""The validator: static rules first, then a dry run of every block, then the page check."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Collection, Mapping
from typing import Any

from prumi.adapters import LOCAL_VIEWER, Viewer
from prumi.engine import BlockRequestError, Engine
from prumi.issues import Issue, Report, error, warning
from prumi.source import Source
from prumi.spec import DashboardSpec
from prumi.validate._parse import parse_spec
from prumi.validate.html import DEFAULT_HOSTS, AllowedHosts, check_html
from prumi.validate.rules import check_spec

__all__ = ["AllowedHosts", "PageCheck", "parse_spec", "validate"]

_MAX_REASON = 500

# Renders the page with the dry run's block results and reports what it shows.
PageCheck = Callable[[DashboardSpec, str, Mapping[str, dict[str, Any]]], list[Issue]]


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
) -> Report:
    """Validate a spec (data or a DashboardSpec) and, when given, its HTML.

    With an engine, a spec without errors is dry-run: every block runs once with the filters'
    default values, as `viewer`. Failures are reported as query.failed, and the warnings in
    the block results (R3.window_snapped, R3.empty_window, R3.stale_data, result.truncated)
    at the block, so the agent sees them before publishing. With a page check too, HTML that
    passes all of that is rendered with the dry run's results, and its numbers are read.
    """
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
            issues += page_check(parsed, html, results)
    return Report.of(issues)


def _has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def _dry_run(
    spec: DashboardSpec, engine: Engine, viewer: Viewer
) -> tuple[list[Issue], dict[str, dict[str, Any]]]:
    """Issues from running every block once, and the block results (as the browser gets them)."""
    issues: list[Issue] = []
    results: dict[str, dict[str, Any]] = {}
    for i, block in enumerate(spec.data):
        try:
            origin = f"dry run {spec.dashboard.id}/{block.id}"
            result = engine.run_block(spec, block.id, {}, viewer, origin)
        except Exception as exc:  # any failure of the source counts; report it, don't crash
            if isinstance(exc, BlockRequestError):
                reason = " ".join(issue.message for issue in exc.issues)
            else:
                reason = " ".join(str(exc).split()) or type(exc).__name__
            if len(reason) > _MAX_REASON:
                reason = reason[:_MAX_REASON] + "..."
            issues.append(
                error(
                    "query.failed",
                    f"data[{i}]",
                    f"Running the block {block.id!r} with the filters' default values failed: "
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
