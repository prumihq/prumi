"""SemanticQuery -> MetricFlow request, and MetricFlow's rows -> Result (spec section 4)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from prumi.adapters import METRIC_TIME, Column, Result, ResultValue, SemanticQuery

from prumi_adapter_dbt._metricflow import CompileRequest

_METRIC_TIME_DAY = "{{ TimeDimension('metric_time', 'day') }}"


def compile_request(query: SemanticQuery) -> CompileRequest:
    if query.filters:
        raise NotImplementedError("filters")
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
    return CompileRequest(
        metrics=tuple(query.metrics),
        group_by=group_by,
        where=where,
        order_by=tuple(_order_by_name(query, o.field, o.descending) for o in query.order_by),
        limit=query.limit,
    )


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
