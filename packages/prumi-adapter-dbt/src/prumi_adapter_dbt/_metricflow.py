"""Every import from MetricFlow lives in this module (ADR 0002).

MetricFlow's engine isn't a stable public API, so the rest of the adapter only talks to it
through what's defined here. MetricFlow compiles; it never gets a connection. The SQL client it
receives provides the dialect and refuses to run anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.duckdb_renderer import DuckDbSqlPlanRenderer
from metricflow.sql.render.sql_plan_renderer import SqlPlanRenderer
from metricflow_semantics.model.dbt_manifest_parser import (
    parse_manifest_from_dbt_generated_manifest,
)
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

# Connection type -> MetricFlow's dialect for it.
_DIALECTS: dict[str, tuple[SqlEngine, type[SqlPlanRenderer]]] = {
    "duckdb": (SqlEngine.DUCKDB, DuckDbSqlPlanRenderer),
}


class ManifestError(Exception):
    """The semantic manifest is missing or MetricFlow can't read it."""


class UnsupportedDialectError(Exception):
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
                f"Connection type {dialect!r} has no MetricFlow dialect in Prumi "
                f"(supported: {supported})."
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


def load_manifest(path: Path) -> SemanticManifestLookup:
    if not path.is_file():
        raise ManifestError(
            f"The semantic manifest {path} doesn't exist. Run `dbt parse` in the dbt project "
            "to write target/semantic_manifest.json, and check `semantic_manifest` under "
            "[source.dbt] in prumi.toml."
        )
    try:
        manifest = parse_manifest_from_dbt_generated_manifest(path.read_text(encoding="utf-8"))
        return SemanticManifestLookup(manifest)
    except Exception as exc:  # MetricFlow raises many types here; all mean "can't use it"
        raise ManifestError(
            f"MetricFlow can't read the semantic manifest {path}: {exc}. Check that it was "
            "written by a dbt version this Prumi release supports, and run `dbt parse` again."
        ) from exc


class MetricFlowCompiler:
    """Compiles queries to SQL for one manifest and dialect, without running anything."""

    def __init__(self, manifest_path: Path, dialect: str) -> None:
        self.client = CompileOnlySqlClient(dialect)
        self._engine = MetricFlowEngine(
            semantic_manifest_lookup=load_manifest(manifest_path), sql_client=self.client
        )

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
