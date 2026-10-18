"""The engine: executes dashboard blocks, never free queries (spec section 9).

Every number comes from the source at the grain it's shown. The engine computes only two
things itself: "Others" for additive metrics (total minus the top N) and comparison deltas.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from prumi.adapters import METRIC_TIME, Filter, OrderBy, Result, SemanticQuery, Viewer
from prumi.issues import Issue, error, warning
from prumi.source import Source
from prumi.spec import Block, DashboardSpec, DimensionFilter, FilterScalar, TimeFilter
from prumi.time import (
    LastWindow,
    RangeWindow,
    ResolvedWindow,
    ToDateWindow,
    Window,
    WindowError,
    check_grain,
    comparison_window,
    period_end,
    resolve_window,
    today,
)

MAX_ROWS = 5000

_WINDOW_HINTS = {
    "R3.window_snapped": "Use dates on period boundaries, or a form like {last: 6, grain: month}.",
    "R3.empty_window": "Today is the first day of the period; the period to date has no days yet.",
}

WindowForm = LastWindow | ToDateWindow | RangeWindow
_WINDOW = TypeAdapter(Window)
_DIMENSION_VALUES = TypeAdapter(tuple[FilterScalar, ...])


class UnknownBlockError(KeyError):
    pass


class BlockRequestError(ValueError):
    """The engine refuses the request: wrong filter values or an unusable window (HTTP 400)."""

    def __init__(self, issues: Sequence[Issue]) -> None:
        super().__init__("; ".join(i.message for i in issues))
        self.issues = tuple(issues)


class ColumnInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: str  # dimension, time or metric
    label: str
    format: dict[str, Any] | None = None  # metrics only
    additive: bool | None = None  # metrics only


class BlockResult(BaseModel):
    """A block's data, as the browser and agents receive it (spec section 9)."""

    block: str
    columns: list[ColumnInfo]
    rows: list[dict[str, Any]]  # keyed by column name; `_` keys are Prumi's
    others: dict[str, Any] | None = None
    total: dict[str, Any] | None = None
    window: dict[str, Any] | None = None
    comparison_window: dict[str, Any] | None = None
    freshness: dict[str, str | None] = {}
    warnings: list[Issue] = []
    truncated: bool = False

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["columns"] = [c.model_dump(mode="json", exclude_none=True) for c in self.columns]
        return data


@dataclass(frozen=True)
class BlockInputs:
    """What a block runs with: its window form and the filters on its dimensions."""

    window: WindowForm
    filters: tuple[Filter, ...]


class Engine:
    def __init__(self, source: Source, as_of: dt.date | None = None) -> None:
        self.source = source
        self.as_of = as_of

    def today(self) -> dt.date:
        return today(self.as_of)

    # Blocks of a dashboard

    def run_block(
        self,
        spec: DashboardSpec,
        block_id: str,
        filter_values: Mapping[str, Any],
        viewer: Viewer,
    ) -> BlockResult:
        """Run one block of a stored spec with the viewer's filter values.

        Only the filters the block lists apply (R5); other values are ignored here.
        """
        block = spec.block(block_id)
        if block is None:
            raise UnknownBlockError(block_id)
        return self.run(block, self.inputs(spec, block, filter_values), viewer)

    def inputs(
        self, spec: DashboardSpec, block: Block, filter_values: Mapping[str, Any]
    ) -> BlockInputs:
        window: WindowForm | None = block.window
        filters: list[Filter] = []
        problems: list[Issue] = []
        for filter_id in block.filters:
            definition = spec.filter(filter_id)
            value = filter_values.get(filter_id, definition.default if definition else None)
            if isinstance(definition, TimeFilter):
                try:
                    window = _WINDOW.validate_python(value)
                except ValidationError:
                    problems.append(
                        _invalid_value(filter_id, "a window such as {last: 12, grain: week}")
                    )
            elif isinstance(definition, DimensionFilter):
                try:
                    values = _DIMENSION_VALUES.validate_python(value)
                except ValidationError:
                    problems.append(_invalid_value(filter_id, "a list of values, [] for all"))
                    continue
                if any(isinstance(v, str) and ("{" in v or "}" in v) for v in values):
                    problems.append(
                        error(
                            "filter.invalid_value",
                            f"filters.{filter_id}",
                            f"Values of the filter {filter_id!r} can't contain '{{' or '}}'.",
                        )
                    )
                elif values:
                    filters.append(Filter(definition.dimension, "in", values))
        if problems:
            raise BlockRequestError(problems)
        if window is None:  # the validator rejects such specs (R3.no_window)
            raise BlockRequestError([error("R3.no_window", "window", "The block has no window.")])
        return BlockInputs(window, tuple(filters))

    # Any block, including the agent's block-shaped queries

    def run(self, block: Block, inputs: BlockInputs, viewer: Viewer) -> BlockResult:
        resolved, previous = self._windows(block, inputs.window)
        warnings = [
            warning(w.code, "window", w.message, _WINDOW_HINTS.get(w.code, ""))
            for w in resolved.warnings
        ]
        freshness = {m: self.data_through(m, viewer) for m in block.metrics}
        warnings += self._stale(block, resolved, freshness)
        result = BlockResult(
            block=block.id,
            columns=self._columns(block),
            rows=[],
            window=resolved.to_json(),
            comparison_window=previous.to_json() if previous else None,
            freshness={m: d.isoformat() if d else None for m, d in freshness.items()},
            warnings=warnings,
        )
        if resolved.empty:
            return result

        main = self._execute(block, resolved, inputs.filters, viewer, main=True)
        rows = _rows(main)
        if block.top_n is not None:
            has_others, rows = len(rows) > block.top_n, rows[: block.top_n]
        else:
            has_others = False
            if len(rows) > MAX_ROWS:
                rows, result.truncated = rows[:MAX_ROWS], True
                result.warnings.append(
                    warning(
                        "result.truncated",
                        "rows",
                        f"The block has more than {MAX_ROWS} rows; only the first {MAX_ROWS} "
                        "are shown.",
                        "Use a coarser time_grain, fewer dimensions, a shorter window or top_n.",
                    )
                )

        additive = [m for m in block.metrics if self._additive(m)]
        total = None
        if block.total or (block.others and has_others and additive):
            total = self.total(block, resolved, inputs.filters, viewer)
        if block.total:
            result.total = total
        if block.others and has_others:
            result.others = self._others(block, rows, total, resolved, inputs.filters, viewer)
        if previous is not None:
            self._compare(block, rows, previous, inputs.filters, viewer)
        if block.time_grain is not None and resolved.partial:
            as_of = self.today()
            for row in rows:
                start = dt.date.fromisoformat(row[METRIC_TIME])
                if start <= as_of <= period_end(start, block.time_grain):
                    row["_partial"] = True
        result.rows = rows
        return result

    def resolve(self, block: Block, window: WindowForm) -> ResolvedWindow:
        return self._windows(block, window)[0]

    def total_for(self, block: Block, inputs: BlockInputs, viewer: Viewer) -> dict[str, Any] | None:
        """The block's total with these inputs, e.g. for an agent result past its row cap."""
        return self.total(block, self.resolve(block, inputs.window), inputs.filters, viewer)

    def total(
        self,
        block: Block,
        resolved: ResolvedWindow,
        filters: tuple[Filter, ...],
        viewer: Viewer,
    ) -> dict[str, Any] | None:
        """The block's metrics over the whole window: a separate query, never a sum of rows."""
        if resolved.empty:
            return None
        query = SemanticQuery(metrics=block.metrics, window=resolved.range, filters=filters)
        [row] = _rows(self.source.execute(query, viewer)) or [{}]
        return {m: row.get(m) for m in block.metrics}

    def dimension_values(self, dimension: str, viewer: Viewer) -> list[Any]:
        """A dimension's distinct values, as `viewer` (filter pickers). NULL comes last."""
        result = self.source.execute(SemanticQuery(metrics=(), dimensions=(dimension,)), viewer)
        values = [v.isoformat() if isinstance(v, dt.date) else v for (v,) in result.rows]
        return sorted(values, key=lambda v: (v is None, str(v)))

    def data_through(self, metric: str, viewer: Viewer) -> dt.date | None:
        """The latest metric_time day with data, with no filters."""
        query = SemanticQuery(
            metrics=(metric,),
            time_grain="day",
            order_by=(OrderBy(METRIC_TIME, descending=True),),
            limit=1,
        )
        result = self.source.execute(query, viewer)
        return result.rows[0][0] if result.rows else None

    # Steps

    def _windows(
        self, block: Block, window: WindowForm
    ) -> tuple[ResolvedWindow, ResolvedWindow | None]:
        try:
            resolved = resolve_window(window, self.today(), block.time_grain)
            if block.time_grain is not None:
                check_grain(block.time_grain, resolved)
            previous = None
            if block.compare is not None:
                if block.time_grain is not None:
                    raise WindowError(
                        "R4.compare_with_time_grain",
                        "compare only works on blocks without time_grain.",
                    )
                previous = comparison_window(window, resolved, block.compare)
        except WindowError as exc:
            raise BlockRequestError([error(exc.code, "window", exc.message)]) from None
        return resolved, previous

    def _stale(
        self, block: Block, resolved: ResolvedWindow, freshness: Mapping[str, dt.date | None]
    ) -> list[Issue]:
        if resolved.empty:
            return []
        stale = []
        for j, metric in enumerate(block.metrics):
            through = freshness[metric]
            if through is None or through < resolved.end:
                shown = through.isoformat() if through else "no day"
                stale.append(
                    warning(
                        "R3.stale_data",
                        f"metrics[{j}]",
                        f"{metric}: data through {shown}; the window ends {resolved.end}, so the "
                        "last period may be incomplete.",
                        "The source hasn't loaded data up to the window's end yet. Tell the "
                        "viewer, or use a window that ends earlier.",
                    )
                )
        return stale

    def _execute(
        self,
        block: Block,
        resolved: ResolvedWindow,
        filters: tuple[Filter, ...],
        viewer: Viewer,
        *,
        main: bool = False,
    ) -> Result:
        order_by: tuple[OrderBy, ...] = ()
        if main and block.sort is not None:
            order_by = (OrderBy(block.sort.by, block.sort.descending),)
        if main:
            # Deterministic rows (and a deterministic cut at the cap): time, then dimensions.
            keys = ((METRIC_TIME,) if block.time_grain else ()) + block.dimensions
            order_by += tuple(OrderBy(k) for k in keys if k not in {o.field for o in order_by})
        if main and block.top_n is not None:
            limit = block.top_n + 1  # one more tells whether there are Others
        else:
            limit = MAX_ROWS + 1  # one more tells whether rows were cut
        query = SemanticQuery(
            metrics=block.metrics,
            dimensions=block.dimensions,
            time_grain=block.time_grain,
            window=resolved.range,
            filters=filters,
            order_by=order_by,
            limit=limit,
        )
        return self.source.execute(query, viewer)

    def _others(
        self,
        block: Block,
        rows: list[dict[str, Any]],
        total: dict[str, Any] | None,
        resolved: ResolvedWindow,
        filters: tuple[Filter, ...],
        viewer: Viewer,
    ) -> dict[str, Any]:
        """R6: additive metrics as total minus the top rows; others by a not_in query."""
        dimension = block.dimensions[0]
        others: dict[str, Any] = {}
        non_additive = []
        for metric in block.metrics:
            if not self._additive(metric):
                non_additive.append(metric)
            elif total is None or total.get(metric) is None:
                others[metric] = None
            else:
                others[metric] = total[metric] - sum(r[metric] or 0 for r in rows)
        if non_additive:
            top = tuple(dict.fromkeys(r[dimension] for r in rows))
            query = SemanticQuery(
                metrics=tuple(non_additive),
                window=resolved.range,
                filters=(*filters, Filter(dimension, "not_in", top)),
            )
            [row] = _rows(self.source.execute(query, viewer)) or [{}]
            others.update({m: row.get(m) for m in non_additive})
        return {m: others[m] for m in block.metrics}

    def _compare(
        self,
        block: Block,
        rows: list[dict[str, Any]],
        previous: ResolvedWindow,
        filters: tuple[Filter, ...],
        viewer: Viewer,
    ) -> None:
        """Adds _previous, _delta and _delta_pct to each row, matched on dimension values."""
        matched: dict[tuple, dict[str, Any]] = {}
        if not previous.empty:
            for row in _rows(self._execute(block, previous, filters, viewer)):
                matched[tuple(row[d] for d in block.dimensions)] = row
        for row in rows:
            before = matched.get(tuple(row[d] for d in block.dimensions), {})
            row["_previous"], row["_delta"], row["_delta_pct"] = {}, {}, {}
            for metric in block.metrics:
                value, prior = row.get(metric), before.get(metric)
                delta = value - prior if value is not None and prior is not None else None
                row["_previous"][metric] = prior
                row["_delta"][metric] = delta
                row["_delta_pct"][metric] = (
                    delta / abs(prior) if delta is not None and prior else None
                )

    def _additive(self, metric: str) -> bool:
        found = self.source.metric(metric)
        return found is not None and found.additive

    def _columns(self, block: Block) -> list[ColumnInfo]:
        columns = []
        if block.time_grain is not None:
            time = self.source.dimension(METRIC_TIME)
            columns.append(
                ColumnInfo(name=METRIC_TIME, kind="time", label=time.label if time else "Time")
            )
        for name in block.dimensions:
            dimension = self.source.dimension(name)
            columns.append(
                ColumnInfo(
                    name=name, kind="dimension", label=dimension.label if dimension else name
                )
            )
        for name in block.metrics:
            metric = self.source.metric(name)
            columns.append(
                ColumnInfo(
                    name=name,
                    kind="metric",
                    label=metric.label if metric else name,
                    format=_format(metric) if metric else None,
                    additive=metric.additive if metric else None,
                )
            )
        return columns


def _format(metric) -> dict[str, Any]:
    f = metric.format
    return {"type": f.type, "unit": f.unit, "decimals": f.decimals}


def _rows(result: Result) -> list[dict[str, Any]]:
    names = [c.name for c in result.columns]
    return [
        {n: v.isoformat() if isinstance(v, dt.date) else v for n, v in zip(names, row, strict=True)}
        for row in result.rows
    ]


def _invalid_value(filter_id: str, expected: str) -> Issue:
    return error(
        "filter.invalid_value",
        f"filters.{filter_id}",
        f"The value of the filter {filter_id!r} has the wrong shape: it must be {expected}.",
    )
