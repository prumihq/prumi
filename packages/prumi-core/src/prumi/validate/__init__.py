"""The validator (spec section 7): static rules first, then a dry run of every block."""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Iterator
from typing import Any

from prumi.adapters import LOCAL_VIEWER, Viewer
from prumi.engine import BlockRequestError, Engine
from prumi.issues import Issue, Report, error, warning
from prumi.source import Source
from prumi.spec import DashboardSpec
from prumi.validate._parse import parse_spec
from prumi.validate.html import DEFAULT_HOSTS, AllowedHosts, check_html
from prumi.validate.rules import check_spec

__all__ = ["AllowedHosts", "parse_spec", "validate"]

_MAX_REASON = 500


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
) -> Report:
    """Validate a spec (data or a DashboardSpec) and, when given, its HTML.

    With an engine, a spec without errors is dry-run: every block runs once with the filters'
    default values, as `viewer`. Failures are reported as query.failed, and the warnings in
    the block results (R3.window_snapped, R3.empty_window, R3.stale_data, result.truncated)
    at the block, so the agent sees them before publishing.
    """
    parsed, issues = parse_spec(spec)
    if parsed is None:
        return Report.of(issues)
    issues = list(check_spec(parsed, source, metric_request_ids, as_of))
    if html is not None:
        issues += check_html(parsed, html, hosts)
    if engine is not None and not any(i.severity == "error" for i in issues):
        issues += _dry_run(parsed, engine, viewer)
    return Report.of(issues)


def _dry_run(spec: DashboardSpec, engine: Engine, viewer: Viewer) -> Iterator[Issue]:
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
            yield error(
                "query.failed",
                f"data[{i}]",
                f"Running the block {block.id!r} with the filters' default values failed: {reason}",
                "Check the block's metrics, dimensions and filter defaults with "
                "describe_metric and query, then validate again.",
            )
            continue
        # What the viewer will see in the block's details: window and freshness warnings.
        for w in result.warnings:
            yield warning(w.code, f"data[{i}]", f"Block {block.id!r}: {w.message}", w.hint)
