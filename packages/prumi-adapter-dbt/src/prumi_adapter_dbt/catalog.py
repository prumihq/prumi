"""Catalog extraction: MetricFlow's metrics and dimensions -> Prumi's catalog (spec section 4)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, get_args

from prumi.adapters import (
    METRIC_TIME,
    AdapterStartupError,
    Catalog,
    CatalogWarning,
    Dimension,
    Format,
    FormatType,
    Metric,
    MetricKind,
)

from prumi_adapter_dbt._metricflow import SourceDimension, SourceMetric

# Being wrong about "additive" gives wrong totals; being wrong about "non-additive" only costs
# an extra query. So only these aggregations, on simple metrics without a
# non_additive_dimension, are additive.
ADDITIVE_AGGREGATIONS = frozenset({"sum", "count", "sum_boolean"})

METRIC_TIME_DIMENSION = Dimension(
    name=METRIC_TIME,
    label="Time",
    description="Each metric's own time dimension (its aggregation time).",
)

_CURRENCY_CODE = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True)
class SourceCatalog:
    catalog: Catalog
    # Metric -> the categorical dimension names valid for it, after the shortest-path rule.
    dimensions_by_metric: Mapping[str, frozenset[str]]

    def relations(self, metrics: Sequence[str]) -> frozenset[str]:
        """The categorical dimensions valid for all of `metrics` (every one, for no metrics)."""
        unknown = [m for m in metrics if m not in self.dimensions_by_metric]
        if unknown:
            raise ValueError(f"Unknown metric(s): {', '.join(unknown)}")
        if not metrics:
            return frozenset(d.name for d in self.catalog.dimensions if d.name != METRIC_TIME)
        return frozenset.intersection(*(self.dimensions_by_metric[m] for m in metrics))


def build_catalog(source_metrics: Sequence[SourceMetric]) -> SourceCatalog:
    metrics, warnings = [], []
    dimensions: dict[str, Dimension] = {}
    dimensions_by_metric = {}
    for source in source_metrics:
        metric_format, warning = parse_format(source.name, source.meta)
        if warning is not None:
            warnings.append(warning)
        metrics.append(_metric(source, metric_format))
        kept = shortest_paths(d for d in source.dimensions if d.categorical)
        dimensions_by_metric[source.name] = frozenset(d.name for d in kept)
        for d in kept:
            dimensions.setdefault(
                d.name, Dimension(d.name, d.label or d.element, d.description or "")
            )
    catalog = Catalog(
        metrics=tuple(sorted(metrics, key=lambda m: m.name)),
        dimensions=(*sorted(dimensions.values(), key=lambda d: d.name), METRIC_TIME_DIMENSION),
        warnings=tuple(warnings),
    )
    return SourceCatalog(catalog, dimensions_by_metric)


def shortest_paths(dimensions: Iterable[SourceDimension]) -> list[SourceDimension]:
    """One name per attribute: for each final entity and dimension, the fewest entity links.

    MetricFlow lists a dimension once per valid join path (`customer__region`, and
    `order__customer__region` through orders). Names of equal length both stay, because
    Prumi can't tell which route is meant.
    """
    by_attribute: dict[tuple[str, str], list[SourceDimension]] = {}
    for d in dimensions:
        key = (d.entity_path[-1] if d.entity_path else "", d.element)
        by_attribute.setdefault(key, []).append(d)
    kept = []
    for candidates in by_attribute.values():
        shortest = min(len(d.entity_path) for d in candidates)
        kept += [d for d in candidates if len(d.entity_path) == shortest]
    return sorted(kept, key=lambda d: d.name)


def _metric(source: SourceMetric, metric_format: Format) -> Metric:
    if source.type not in get_args(MetricKind):
        raise AdapterStartupError(
            f"Metric {source.name!r} has the type {source.type!r}, which this Prumi release "
            "doesn't know."
        )
    simple = source.type == "simple"
    owner = source.meta.get("owner")
    return Metric(
        name=source.name,
        label=source.label or source.name,
        description=source.description or "",
        kind=source.type,
        aggregation=source.aggregation if simple else None,
        additive=(
            simple
            and source.aggregation in ADDITIVE_AGGREGATIONS
            and source.non_additive_dimension is None
        ),
        format=metric_format,
        owner=owner if isinstance(owner, str) else None,
        filter=source.filter,
    )


def parse_format(metric: str, meta: Mapping[str, Any]) -> tuple[Format, CatalogWarning | None]:
    """The metric's format from `config.meta.prumi` (R10), or `number` and a warning."""
    declared = meta.get("prumi")
    if not isinstance(declared, Mapping) or "format" not in declared:
        return Format(), CatalogWarning(
            code="R10.no_format",
            subject=metric,
            message=(
                f"Metric {metric!r} declares no format, so its values show as plain numbers. "
                "Add config.meta.prumi.format (number, integer, percent or currency) to it "
                "in the dbt project."
            ),
        )
    problem = _format_problem(declared)
    if problem:
        return Format(), CatalogWarning(
            code="R10.no_format",
            subject=metric,
            message=(
                f"Metric {metric!r} has an invalid format in config.meta.prumi ({problem}), "
                "so its values show as plain numbers."
            ),
        )
    return Format(
        type=declared["format"], unit=declared.get("unit"), decimals=declared.get("decimals")
    ), None


def _format_problem(declared: Mapping[str, Any]) -> str | None:
    kind, unit, decimals = declared["format"], declared.get("unit"), declared.get("decimals")
    if kind not in get_args(FormatType):
        return f"format {kind!r} isn't one of number, integer, percent, currency"
    if unit is not None and not isinstance(unit, str):
        return "unit must be text"
    if kind == "currency" and not (isinstance(unit, str) and _CURRENCY_CODE.fullmatch(unit)):
        return "currency needs a 3-letter ISO 4217 unit such as USD"
    if decimals is not None and (
        isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 20
    ):
        return "decimals must be a whole number from 0 to 20"
    return None
