"""Static rules: spec.*, R2, R3, R4, R5, R6, R7 and R10."""

from __future__ import annotations

import datetime as dt
import difflib
from collections.abc import Collection, Iterator

from prumi.issues import Issue, error, warning
from prumi.source import Source
from prumi.spec import Block, DashboardSpec, DimensionFilter, TimeFilter
from prumi.time import WindowError, check_grain, comparison_window, resolve_window

REQUEST_METRIC_HINT = (
    "If the metric you need doesn't exist, call request_metric and tell the user. Never "
    "compute or approximate a metric yourself."
)


def check_spec(
    spec: DashboardSpec, source: Source, metric_request_ids: Collection[int], as_of: dt.date
) -> Iterator[Issue]:
    yield from _ids(spec)
    yield from _calendars(spec)
    yield from _filter_defaults(spec)
    yield from _references(spec)
    yield from _metric_requests(spec, metric_request_ids)
    for index, block in enumerate(spec.data):
        yield from _block(spec, index, block, source, as_of)
    yield from _interactions(spec)


def _ids(spec: DashboardSpec) -> Iterator[Issue]:
    seen: dict[str, str] = {}
    items = [(f"filters[{i}]", f.id) for i, f in enumerate(spec.filters)]
    items += [(f"data[{i}]", b.id) for i, b in enumerate(spec.data)]
    for path, item_id in items:
        if item_id in seen:
            yield error(
                "spec.duplicate_id",
                f"{path}.id",
                f"The id {item_id!r} is already used by {seen[item_id]}.",
                "Filter and block ids must be unique across both lists. Rename one of them.",
            )
        else:
            seen[item_id] = path


def _filter_defaults(spec: DashboardSpec) -> Iterator[Issue]:
    for i, f in enumerate(spec.filters):
        if not isinstance(f, DimensionFilter):
            continue
        for j, value in enumerate(f.default):
            if isinstance(value, str) and ("{" in value or "}" in value):
                yield error(
                    "filter.invalid_value",
                    f"filters[{i}].default[{j}]",
                    f"The default value {value!r} contains '{{' or '}}', which filter values "
                    "can't contain.",
                    "Use another default; the source can't be filtered on such values.",
                )


def _calendars(spec: DashboardSpec) -> Iterator[Issue]:
    for i, f in enumerate(spec.filters):
        if isinstance(f, TimeFilter) and f.calendar != "standard":
            yield error(
                "spec.unsupported_calendar",
                f"filters[{i}].calendar",
                f"The calendar {f.calendar!r} isn't supported.",
                "Only the standard calendar is available: remove `calendar` or set it to "
                '"standard".',
            )


def _references(spec: DashboardSpec) -> Iterator[Issue]:
    filters = {f.id for f in spec.filters}
    blocks = {b.id for b in spec.data}
    for i, block in enumerate(spec.data):
        for j, filter_id in enumerate(block.filters):
            if filter_id not in filters:
                yield _unknown("spec.unknown_filter", f"data[{i}].filters[{j}]", filter_id, filters)
    for i, interaction in enumerate(spec.interactions):
        if interaction.from_ not in blocks:
            yield _unknown(
                "spec.unknown_block", f"interactions[{i}].from", interaction.from_, blocks
            )
        if interaction.sets_filter not in filters:
            yield _unknown(
                "spec.unknown_filter",
                f"interactions[{i}].sets_filter",
                interaction.sets_filter,
                filters,
            )
    for selector, bound in spec.presentation.bindings.items():
        path = f"presentation.bindings[{selector!r}]"
        for j, block_id in enumerate((bound,) if isinstance(bound, str) else bound):
            if block_id not in blocks:
                where = path if isinstance(bound, str) else f"{path}[{j}]"
                yield _unknown("spec.unknown_block", where, block_id, blocks)


def _unknown(code: str, path: str, name: str, known: Collection[str]) -> Issue:
    kind = "filter" if code == "spec.unknown_filter" else "block"
    listed = ", ".join(sorted(known)) or "none"
    return error(
        code,
        path,
        f"There's no {kind} with the id {name!r} (the {kind}s are: {listed}).",
        f"Use the id of a {kind} defined in the spec, or define it.",
    )


def _metric_requests(spec: DashboardSpec, known: Collection[int]) -> Iterator[Issue]:
    for i, request_id in enumerate(spec.metric_requests):
        if request_id not in known:
            yield error(
                "R7.unknown_metric_request",
                f"metric_requests[{i}]",
                f"There's no metric request with the id {request_id}.",
                "List only ids that request_metric returned.",
            )


def _block(
    spec: DashboardSpec, i: int, block: Block, source: Source, as_of: dt.date
) -> Iterator[Issue]:
    path = f"data[{i}]"

    # R7: metrics come from the catalog only.
    unknown = [m for m in block.metrics if source.metric(m) is None]
    for j, name in enumerate(block.metrics):
        if name in unknown:
            close = difflib.get_close_matches(name, source.metric_names, n=3)
            suggestion = f" Did you mean {', '.join(close)}?" if close else ""
            yield error(
                "R7.unknown_metric",
                f"{path}.metrics[{j}]",
                f"The catalog has no metric {name!r}.{suggestion}",
                REQUEST_METRIC_HINT,
            )

    # R2: dimensions and dimension filters valid for all of the block's metrics.
    known_filters = [(j, spec.filter(f)) for j, f in enumerate(block.filters)]
    if not unknown:
        valid = source.relations(block.metrics)
        listed = ", ".join(sorted(valid)) or "none"
        for j, dimension in enumerate(block.dimensions):
            if dimension not in valid:
                yield error(
                    "R2.invalid_dimension",
                    f"{path}.dimensions[{j}]",
                    f"{dimension!r} isn't a dimension of {', '.join(block.metrics)} together "
                    f"(valid: {listed}).",
                    "Use a dimension describe_metric lists for every metric in the block, or "
                    "split the block.",
                )
        for j, f in known_filters:
            if isinstance(f, DimensionFilter) and f.dimension not in valid:
                yield error(
                    "R2.filter_dimension_invalid",
                    f"{path}.filters[{j}]",
                    f"The filter {f.id!r} is on {f.dimension!r}, which isn't a dimension of "
                    f"{', '.join(block.metrics)} together (valid: {listed}).",
                    "Remove the filter from this block's filters, or use metrics it applies to.",
                )

    # R3 / R5: exactly one window.
    time_filters = [(j, f) for j, f in known_filters if isinstance(f, TimeFilter)]
    if not time_filters and block.window is None:
        yield error(
            "R3.no_window",
            f"{path}.filters",
            "The block has no time window: neither a time filter in `filters` nor a fixed "
            "`window`. All time would silently include the current, partial period.",
            "Add a time filter to the block's filters, or a window such as "
            "{last: 12, grain: week}.",
        )
    elif len(time_filters) + (block.window is not None) > 1:
        yield error(
            "R5.multiple_windows",
            f"{path}.window" if block.window is not None else f"{path}.filters",
            "The block has more than one time window (time filters or a fixed window).",
            "Keep exactly one: one time filter in `filters`, or a fixed `window`.",
        )
    else:
        if block.window is not None:
            window, window_path = block.window, f"{path}.window"
        else:
            j, f = time_filters[0]
            window, window_path = f.default, f"filters[{spec.filters.index(f)}].default"
        yield from _window(path, block, window, window_path, as_of)

    # R4: no comparison on time series.
    if block.compare is not None and block.time_grain is not None:
        yield error(
            "R4.compare_with_time_grain",
            f"{path}.compare",
            "`compare` only works on blocks without `time_grain` (KPIs and tables by "
            "dimension). Time series with a comparison series aren't supported.",
            "Remove `compare`, or remove `time_grain` to compare totals.",
        )

    # R6: top N, Others and totals.
    if block.top_n is not None and (
        len(block.dimensions) != 1
        or block.time_grain is not None
        or block.sort is None
        or block.sort.by not in block.metrics
    ):
        yield error(
            "R6.top_n_shape",
            f"{path}.top_n",
            "`top_n` needs exactly one dimension, no `time_grain`, and `sort` by one of the "
            "block's metrics.",
            "E.g. dimensions: [customer__region], sort: {by: revenue, descending: true}.",
        )
    if block.others and block.top_n is None:
        yield error(
            "R6.others_requires_top_n",
            f"{path}.others",
            "`others` sums up the rows outside the top N, so it needs `top_n`.",
            "Add `top_n` (and `sort` by a metric), or remove `others`.",
        )
    if block.total and not block.dimensions and block.time_grain is None:
        yield error(
            "R6.total_requires_grouping",
            f"{path}.total",
            "`total` needs rows to total: the block has no dimensions and no `time_grain`.",
            "Remove `total`: without grouping, the block's values already are the totals.",
        )

    # R10: formats.
    metrics = [(j, source.metric(m)) for j, m in enumerate(block.metrics)]
    for j, metric in metrics:
        if metric is None:
            continue
        for w in source.warnings_for(metric.name):
            if w.code == "R10.no_format":
                yield warning(
                    "R10.no_format",
                    f"{path}.metrics[{j}]",
                    w.message,
                    "Values show as plain numbers; prumi.format can't add units. Tell the "
                    "data team if the metric needs a format.",
                )
    units = {m.format.unit for _, m in metrics if m is not None and m.format.unit}
    if len(units) > 1:
        yield warning(
            "R10.mixed_units",
            f"{path}.metrics",
            f"The block's metrics have different units ({', '.join(sorted(units))}).",
            "Charts with one axis can't show them together; split the block or use one axis "
            "per unit.",
        )


def _window(path: str, block: Block, window, window_path: str, as_of: dt.date) -> Iterator[Issue]:
    """R3.window_too_short, R4.grain_mismatch and R3.partial_in_comparison, on the default."""
    try:
        resolved = resolve_window(window, as_of, block.time_grain)
    except WindowError as exc:
        yield error(exc.code, window_path, exc.message, "Use a window that covers whole periods.")
        return
    if block.time_grain is not None:
        try:
            check_grain(block.time_grain, resolved)
        except WindowError as exc:
            yield error(
                exc.code,
                f"{path}.time_grain",
                exc.message,
                "Week time grains need week windows; months fit month, quarter and year windows.",
            )
    if block.compare is not None and block.time_grain is None:
        try:
            comparison_window(window, resolved, block.compare)
        except WindowError as exc:
            yield error(exc.code, f"{path}.compare", exc.message, "Remove include_current.")


def _interactions(spec: DashboardSpec) -> Iterator[Issue]:
    for i, interaction in enumerate(spec.interactions):
        block, target = spec.block(interaction.from_), spec.filter(interaction.sets_filter)
        if block is None or target is None:
            continue  # already reported as unknown
        if not isinstance(target, DimensionFilter):
            yield error(
                "spec.invalid_interaction",
                f"interactions[{i}].sets_filter",
                f"A click sets a dimension filter to the clicked row's value, but "
                f"{target.id!r} is a time filter.",
                "Point sets_filter at a dimension filter.",
            )
        elif target.dimension not in block.dimensions:
            yield error(
                "spec.invalid_interaction",
                f"interactions[{i}].from",
                f"A click on {block.id!r} sets {target.id!r} to the row's {target.dimension!r}, "
                "but the block has no such dimension.",
                f"Add {target.dimension!r} to the block's dimensions, or click another block.",
            )
