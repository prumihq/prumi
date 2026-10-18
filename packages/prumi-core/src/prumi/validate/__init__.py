"""The validator (spec section 7): static rules first, then a dry run of every block."""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from typing import Any

from prumi.issues import Report
from prumi.source import Source
from prumi.validate._parse import parse_spec
from prumi.validate.html import DEFAULT_HOSTS, AllowedHosts, check_html
from prumi.validate.rules import check_spec

__all__ = ["AllowedHosts", "parse_spec", "validate"]


def validate(
    spec: Any,
    html: str | None,
    source: Source,
    metric_request_ids: Collection[int],
    *,
    as_of: dt.date,
    hosts: AllowedHosts = DEFAULT_HOSTS,
) -> Report:
    """Validate a spec (data or a DashboardSpec) and, when given, its HTML."""
    parsed, issues = parse_spec(spec)
    if parsed is None:
        return Report.of(issues)
    issues = list(check_spec(parsed, source, metric_request_ids, as_of))
    if html is not None:
        issues += check_html(parsed, html, hosts)
    return Report.of(issues)
