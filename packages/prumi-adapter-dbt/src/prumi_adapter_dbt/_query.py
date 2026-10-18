"""SemanticQuery -> MetricFlow request, and MetricFlow's rows -> Result."""

from __future__ import annotations

import datetime as dt
import math
import re
from decimal import Decimal
from typing import Any

from prumi.adapters import (
    METRIC_TIME,
    Column,
    Filter,
    FilterValue,
    Result,
    ResultValue,
    SemanticQuery,
)

from prumi_adapter_dbt._metricflow import CompileRequest

_METRIC_TIME_DAY = "{{ TimeDimension('metric_time', 'day') }}"


def compile_request(query: SemanticQuery) -> CompileRequest:
    group_by = tuple(query.dimensions)
    if query.time_grain is not None:
        group_by += (f"{METRIC_TIME}__{query.time_grain}",)
    where: tuple[str, ...] = ()
    if query.window is not None:
        # Works with and without a time group-by (contract test).
        where += (
            f"{_METRIC_TIME_DAY} >= '{query.window.start.isoformat()}'",
            f"{_METRIC_TIME_DAY} <= '{query.window.end.isoformat()}'",
        )
    where += tuple(render_filter(f) for f in query.filters)
    return CompileRequest(
        metrics=tuple(query.metrics),
        group_by=group_by,
        where=where,
        order_by=tuple(_order_by_name(query, o.field, o.descending) for o in query.order_by),
        limit=query.limit,
    )


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COMPARISONS = {"lt": "<", "le": "<=", "gt": ">", "ge": ">="}


def render_filter(f: Filter) -> str:
    """A where clause for MetricFlow, with the filter semantics of the adapter protocol.

    `eq` / `in` with None also match NULL; `ne` / `not_in` keep NULL rows unless None is among
    the values. MetricFlow renders where clauses as Jinja, so values can't contain `{` or `}`.
    """
    if not _NAME.fullmatch(f.dimension):
        raise ValueError(f"Not a dimension name: {f.dimension!r}")
    column = f"{{{{ Dimension('{f.dimension}') }}}}"
    literals = [_literal(v) for v in f.values if v is not None]
    has_null = None in f.values

    if f.op in _COMPARISONS:
        if has_null:
            raise ValueError(f"Filter {f.op!r} on {f.dimension!r} can't compare with NULL")
        return f"{column} {_COMPARISONS[f.op]} {literals[0]}"
    if f.op in ("eq", "in"):
        if not literals:
            return f"{column} IS NULL"
        match = (
            f"{column} = {literals[0]}" if f.op == "eq" else f"{column} IN ({', '.join(literals)})"
        )
        return f"({match} OR {column} IS NULL)" if has_null else match
    if f.op in ("ne", "not_in"):
        if not literals:
            return f"{column} IS NOT NULL"
        if f.op == "ne":
            exclude = f"{column} <> {literals[0]}"
        else:
            exclude = f"{column} NOT IN ({', '.join(literals)})"
        return (
            f"({exclude} AND {column} IS NOT NULL)"
            if has_null
            else f"({exclude} OR {column} IS NULL)"
        )
    raise ValueError(f"Unknown filter op {f.op!r}")


def _literal(value: FilterValue) -> str:
    if isinstance(value, bool):  # before int: bool is an int
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return repr(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Filter values must be finite numbers, got {value!r}")
        return repr(value)
    if isinstance(value, str):
        if "{" in value or "}" in value:
            # MetricFlow renders where clauses as Jinja: the value would become a template.
            raise ValueError("Filter values can't contain '{' or '}'")
        return "'" + value.replace("'", "''") + "'"
    raise ValueError(f"Unsupported filter value type: {type(value).__name__}")


def _order_by_name(query: SemanticQuery, field: str, descending: bool) -> str:
    if field == METRIC_TIME:
        if query.time_grain is None:
            raise ValueError("order_by metric_time needs a time_grain")
        name = f"{METRIC_TIME}__{query.time_grain}"
    elif field in query.metrics or field in query.dimensions:
        name = field
    else:
        raise ValueError(f"order_by {field!r} isn't one of the query's metrics or dimensions")
    return f"-{name}" if descending else name


def columns(query: SemanticQuery) -> tuple[Column, ...]:
    """The result's columns: metric_time, then dimensions, then metrics, in query order."""
    time = (Column(METRIC_TIME, "time"),) if query.time_grain is not None else ()
    return (
        *time,
        *(Column(name, "dimension") for name in query.dimensions),
        *(Column(name, "metric") for name in query.metrics),
    )


def to_result(query: SemanticQuery, names: list[str], rows: list[tuple[Any, ...]]) -> Result:
    """Read MetricFlow's rows by column name (it reorders columns) into semantic names."""
    result_columns = columns(query)
    positions = []
    for column in result_columns:
        name = f"{METRIC_TIME}__{query.time_grain}" if column.kind == "time" else column.name
        if name not in names:
            raise RuntimeError(f"The compiled query returned no column {name!r} (got {names})")
        positions.append(names.index(name))
    converted = tuple(
        tuple(_value(row[i], c.kind) for i, c in zip(positions, result_columns, strict=True))
        for row in rows
    )
    return Result(columns=result_columns, rows=converted, row_count=len(converted))


def _value(value: Any, kind: str) -> ResultValue:
    if isinstance(value, Decimal):
        return float(value)
    if kind == "time" and isinstance(value, dt.datetime):
        return value.date()  # MetricFlow's DATE_TRUNC returns timestamps
    return value
