"""Every import from MetricFlow lives in this module (ADR 0002).

MetricFlow's engine isn't a stable public API, so the rest of the adapter only talks to it
through what's defined here. MetricFlow compiles; it never gets a connection. The SQL client it
receives provides the dialect and refuses to run anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.engine.models import Dimension as MfDimension
from metricflow.engine.models import Metric as MfMetric
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow.sql.render.sql_plan_renderer import SqlPlanRenderer
from metricflow_semantic_interfaces.implementations.semantic_manifest import (
    PydanticSemanticManifest,
)
from metricflow_semantics.model.dbt_manifest_parser import transform_dbt_generated_manifest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup
from prumi.adapters import AdapterStartupError

# Connection type -> MetricFlow's dialect for it.
_DIALECTS: dict[str, tuple[SqlEngine, type[SqlPlanRenderer]]] = {
    "duckdb": (SqlEngine.DUCKDB, DuckDbSqlPlanRenderer),
}


class ManifestError(AdapterStartupError):
    """The semantic manifest is missing or MetricFlow can't read it."""


class UnsupportedDialectError(AdapterStartupError):
    """No MetricFlow dialect for the connection type."""


class CompileError(Exception):
    """MetricFlow produced SQL the adapter won't run."""


def dialects() -> frozenset[str]:
    """Connection types the adapter can compile for."""
    return frozenset(_DIALECTS)


class CompileOnlySqlClient:
    """MetricFlow's SqlClient protocol, reduced to the dialect.

    `query`, `execute` and `dry_run` record the call and raise, so a MetricFlow version that
    starts running SQL while compiling fails loudly instead of silently.
    """

    def __init__(self, dialect: str) -> None:
        if dialect not in _DIALECTS:
            supported = ", ".join(sorted(_DIALECTS))
            raise UnsupportedDialectError(
                f"Prumi doesn't support the connection type {dialect!r} (supported: {supported}). "
                "Check `type` under [source.dbt.connection] in prumi.toml."
            )
        engine, renderer = _DIALECTS[dialect]
        self._engine = engine
        self._renderer = renderer()
        self.calls: list[str] = []

    @property
    def sql_engine_type(self) -> SqlEngine:
        return self._engine

    @property
    def sql_plan_renderer(self) -> SqlPlanRenderer:
        return self._renderer

    def _refuse(self, method: str) -> None:
        self.calls.append(method)
        raise RuntimeError(
            f"MetricFlow called SqlClient.{method}; Prumi's MetricFlow client only compiles."
        )

    def query(self, stmt: str, sql_bind_parameter_set: object = None) -> None:
        self._refuse("query")

    def execute(self, stmt: str, sql_bind_parameter_set: object = None) -> None:
        self._refuse("execute")

    def dry_run(self, stmt: str, sql_bind_parameter_set: object = None) -> None:
        self._refuse("dry_run")

    def close(self) -> None:
        pass

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        return f"${bind_parameter_key}"


@dataclass(frozen=True)
class CompileRequest:
    """A query in MetricFlow's terms: query names and Jinja where clauses."""

    metrics: tuple[str, ...] = ()
    group_by: tuple[str, ...] = ()  # e.g. "customer__region", "metric_time__month"
    where: tuple[str, ...] = ()  # e.g. "{{ Dimension('customer__region') }} = 'North'"
    order_by: tuple[str, ...] = ()  # names; a leading "-" sorts descending
    limit: int | None = None


@dataclass(frozen=True)
class SourceDimension:
    """A dimension as MetricFlow lists it for a metric: one entry per join path."""

    name: str  # the query name, e.g. "order__customer__region"
    element: str  # e.g. "region"
    entity_path: tuple[str, ...]  # e.g. ("order", "customer")
    categorical: bool
    label: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class SourceMetric:
    """A metric as MetricFlow describes it, in plain types."""

    name: str
    type: str  # simple, ratio, derived, cumulative or conversion
    label: str | None = None
    description: str | None = None
    aggregation: str | None = None  # simple metrics: e.g. "sum", "count_distinct"
    non_additive_dimension: str | None = None  # semi-additive simple metrics
    meta: Mapping[str, Any] = field(default_factory=dict)  # config.meta
    filter: str | None = None
    dimensions: tuple[SourceDimension, ...] = ()


def _source_dimension(dimension: MfDimension) -> SourceDimension:
    return SourceDimension(
        name=dimension.dunder_name,
        element=dimension.name,
        entity_path=tuple(link.element_name for link in dimension.entity_links),
        categorical=dimension.type.value == "categorical",
        label=dimension.label,
        description=dimension.description,
    )


def _source_metric(metric: MfMetric, declared_aggregations: Mapping[str, str]) -> SourceMetric:
    aggregation = non_additive = None
    params = metric.type_params.metric_aggregation_params
    if params is not None:
        measure = metric.type_params.measure
        aggregation = declared_aggregations.get(measure.name if measure else "", params.agg.value)
        if params.non_additive_dimension is not None:
            non_additive = params.non_additive_dimension.name
    where = metric.filter.where_filters if metric.filter is not None else []
    return SourceMetric(
        name=metric.name,
        type=metric.type.value,
        label=metric.label,
        description=metric.description,
        aggregation=aggregation,
        non_additive_dimension=non_additive,
        meta=dict(metric.config.meta) if metric.config is not None else {},
        filter=" AND ".join(w.where_sql_template for w in where) or None,
        dimensions=tuple(_source_dimension(d) for d in metric.dimensions),
    )


_MAX_REASON = 300


@dataclass(frozen=True)
class LoadedManifest:
    lookup: SemanticManifestLookup
    # Measure name -> its aggregation as declared. MetricFlow rewrites some while parsing
    # (count becomes a sum of 1s, median a percentile); the catalog shows the declared one.
    declared_aggregations: Mapping[str, str]


def load_manifest(path: Path) -> LoadedManifest:
    if not path.is_file():
        raise ManifestError(
            f"The semantic manifest {path} doesn't exist. Run `dbt parse` in the dbt project "
            "to write target/semantic_manifest.json, and check `semantic_manifest` under "
            "[source.dbt] in prumi.toml."
        )
    try:
        text = path.read_text(encoding="utf-8")
        declared = {
            measure.name: measure.agg.value
            for model in PydanticSemanticManifest.parse_raw(text).semantic_models
            for measure in model.measures
        }
        # What parse_manifest_from_dbt_generated_manifest does, on a fresh copy.
        manifest = transform_dbt_generated_manifest(PydanticSemanticManifest.parse_raw(text))
        return LoadedManifest(SemanticManifestLookup(manifest), declared)
    except Exception as exc:  # MetricFlow raises many types here; all mean "can't use it"
        # Some errors quote the whole document; keep the message short.
        reason = " ".join(str(exc).split())
        if len(reason) > _MAX_REASON:
            reason = reason[:_MAX_REASON] + "..."
        raise ManifestError(
            f"MetricFlow can't read the semantic manifest {path}: {reason} Check that it was "
            "written by a dbt version this Prumi release supports, and run `dbt parse` again."
        ) from exc


class MetricFlowCompiler:
    """Compiles queries to SQL for one manifest and dialect, without running anything."""

    def __init__(self, manifest_path: Path, dialect: str) -> None:
        self._manifest = load_manifest(manifest_path)
        self._lookup = self._manifest.lookup
        self.client = CompileOnlySqlClient(dialect)
        self._engine = MetricFlowEngine(
            semantic_manifest_lookup=self._lookup, sql_client=self.client
        )

    def metrics(self) -> tuple[SourceMetric, ...]:
        """The manifest's public metrics, each with every dimension MetricFlow lists for it."""
        declared = self._manifest.declared_aggregations
        metrics = self._engine.list_metrics(include_dimensions=True)
        return tuple(_source_metric(m, declared) for m in metrics)

    def relations(self) -> dict[str, str]:
        """Semantic model name -> its relation, as dbt resolved it ("db"."schema"."table")."""
        return {
            model.name: model.node_relation.relation_name
            for model in self._lookup.semantic_manifest.semantic_models
        }

    def compile(self, request: CompileRequest) -> str:
        explained = self._engine.explain(
            MetricFlowQueryRequest.create(
                metric_names=list(request.metrics),
                group_by_names=list(request.group_by),
                where_constraints=list(request.where),
                order_by_names=list(request.order_by),
                limit=request.limit,
            )
        )
        statement = explained.sql_statement
        if statement.bind_parameter_set.param_dict:
            raise CompileError(
                "MetricFlow produced SQL with bind parameters, which Prumi doesn't run: "
                f"{dict(statement.bind_parameter_set.param_dict)}"
            )
        return statement.without_descriptions.sql
