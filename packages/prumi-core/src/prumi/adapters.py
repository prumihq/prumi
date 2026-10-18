"""The adapter protocol.

An adapter connects Prumi to one kind of source. It describes the source's metrics, compiles
and runs semantic queries as a viewer, and leaves metric definitions and access rules to the
source. Adapters are separate packages that register a factory under the `prumi.adapters`
entry point group; core never imports them directly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from prumi.time import GRAINS, DateRange, Grain

__all__ = [
    "ENTRY_POINT_GROUP",
    "LOCAL_VIEWER",
    "METRIC_TIME",
    "Adapter",
    "AdapterFactory",
    "AdapterNotFoundError",
    "AdapterStartupError",
    "Capabilities",
    "Catalog",
    "CatalogWarning",
    "Column",
    "DateRange",
    "Dimension",
    "Filter",
    "Format",
    "Grain",
    "Metric",
    "OrderBy",
    "Result",
    "SemanticQuery",
    "Viewer",
    "adapter_names",
    "load_adapter_factory",
]

IdentityMode = Literal["per_viewer", "shared_account"]
MetricKind = Literal["simple", "ratio", "derived", "cumulative", "conversion"]
FormatType = Literal["number", "integer", "percent", "currency"]
FilterOp = Literal["eq", "ne", "in", "not_in", "lt", "le", "gt", "ge"]
FilterValue = str | int | float | bool | None
ColumnKind = Literal["dimension", "time", "metric"]
ResultValue = str | int | float | bool | dt.date | None

METRIC_TIME = "metric_time"


@dataclass(frozen=True)
class Capabilities:
    identity_mode: IdentityMode
    time_grains: tuple[Grain, ...] = GRAINS
    calendars: tuple[str, ...] = ("standard",)


# Catalog


@dataclass(frozen=True)
class Format:
    """How a metric's values are shown (R10). `percent` values are fractions (0.25 is 25%)."""

    type: FormatType = "number"
    unit: str | None = None
    decimals: int | None = None


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    description: str
    kind: MetricKind
    aggregation: str | None  # the source's aggregation for simple metrics, else None
    additive: bool
    format: Format
    owner: str | None
    filter: str | None  # the metric's own filter, for display only


@dataclass(frozen=True)
class Dimension:
    name: str  # the query name, e.g. "customer__region"
    label: str
    description: str


@dataclass(frozen=True)
class CatalogWarning:
    code: str  # e.g. "R10.no_format"
    subject: str  # the metric or dimension it's about
    message: str


@dataclass(frozen=True)
class Catalog:
    metrics: tuple[Metric, ...]
    dimensions: tuple[Dimension, ...]  # categorical dimensions, plus metric_time
    warnings: tuple[CatalogWarning, ...] = ()


# Queries


@dataclass(frozen=True)
class Filter:
    """A filter on a categorical dimension.

    `eq` / `in` with None among the values also match NULL. `ne` / `not_in` keep NULL rows
    unless None is among the values. `in` / `not_in` need at least one value; the other
    operators need exactly one.
    """

    dimension: str
    op: FilterOp
    values: tuple[FilterValue, ...]

    def __post_init__(self) -> None:
        if self.op in ("in", "not_in"):
            if not self.values:
                raise ValueError(f"Filter {self.op!r} on {self.dimension!r} needs a value")
        elif len(self.values) != 1:
            raise ValueError(
                f"Filter {self.op!r} on {self.dimension!r} needs exactly one value, "
                f"got {len(self.values)}"
            )


@dataclass(frozen=True)
class OrderBy:
    field: str  # a metric, a dimension or "metric_time"
    descending: bool = False


@dataclass(frozen=True)
class SemanticQuery:
    """One query for an adapter to compile and run.

    With `metrics=()`, exactly one dimension and no `time_grain`, it asks for that
    dimension's distinct values (filter pickers).
    """

    metrics: tuple[str, ...]
    dimensions: tuple[str, ...] = ()  # categorical dimension names
    time_grain: Grain | None = None  # group by metric_time at this grain
    window: DateRange | None = None  # inclusive dates, already aligned by the engine
    filters: tuple[Filter, ...] = ()
    order_by: tuple[OrderBy, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.metrics and (len(self.dimensions) != 1 or self.time_grain is not None):
            raise ValueError(
                "A query without metrics asks for one dimension's values: it needs exactly "
                "one dimension and no time_grain"
            )

    @property
    def is_dimension_values(self) -> bool:
        return not self.metrics


@dataclass(frozen=True)
class Viewer:
    id: str
    security_context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "security_context", MappingProxyType(dict(self.security_context)))


LOCAL_VIEWER = Viewer(id="local", security_context={})


@dataclass(frozen=True)
class Column:
    name: str  # a dimension name, "metric_time" or a metric name
    kind: ColumnKind


@dataclass(frozen=True)
class Result:
    """Rows in column order. Dates are `date`, numbers `int` or `float`, NULL is None."""

    columns: tuple[Column, ...]
    rows: tuple[tuple[ResultValue, ...], ...]
    row_count: int


# The protocol


@runtime_checkable
class Adapter(Protocol):
    name: str

    def capabilities(self) -> Capabilities: ...

    def discover(self) -> Catalog:
        """The catalog, read once at startup."""
        ...

    def relations(self, metrics: Sequence[str]) -> frozenset[str]:
        """The categorical dimensions valid for all the given metrics together."""
        ...

    def execute(self, query: SemanticQuery, viewer: Viewer) -> Result:
        """Compile and run one query as `viewer`. Synchronous."""
        ...


# Discovery

ENTRY_POINT_GROUP = "prumi.adapters"

# What an adapter package registers: `factory(config, config_dir)`, called with the adapter's
# own config table (`[source.<name>]`) and the folder that relative paths in it resolve against.
AdapterFactory = Callable[[Mapping[str, Any], Path], Adapter]


class AdapterNotFoundError(LookupError):
    pass


class AdapterStartupError(Exception):
    """An adapter can't start. The message names the file or relation and what to do."""


def adapter_names() -> list[str]:
    """Names of the installed adapters."""
    return sorted({ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)})


def load_adapter_factory(name: str) -> AdapterFactory:
    """The factory of the installed adapter registered as `name`."""
    matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
    if not matches:
        installed = ", ".join(adapter_names()) or "none"
        raise AdapterNotFoundError(
            f"No Prumi adapter named {name!r} is installed (installed: {installed}). "
            f"Install the package that provides it, e.g. prumi-adapter-{name}."
        )
    if len({ep.value for ep in matches}) > 1:
        sources = ", ".join(sorted(ep.value for ep in matches))
        raise AdapterNotFoundError(
            f"More than one installed package registers the adapter {name!r}: {sources}"
        )
    return matches[0].load()
