"""The dbt adapter: MetricFlow compiles, Prumi executes (ADR 0002)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from prumi.adapters import (
    AdapterStartupError,
    Capabilities,
    Catalog,
    Result,
    SemanticQuery,
    Viewer,
)

from prumi_adapter_dbt import _query
from prumi_adapter_dbt._metricflow import MetricFlowCompiler
from prumi_adapter_dbt.connections import Connection, open_connection


class DbtAdapter:
    name = "dbt"

    def __init__(self, compiler: MetricFlowCompiler, connection: Connection) -> None:
        self._compiler = compiler
        self._connection = connection

    @classmethod
    def from_config(cls, config: Mapping[str, Any], config_dir: Path) -> DbtAdapter:
        """Load the manifest, open the connection and run the startup checks (spec section 4)."""
        manifest = config.get("semantic_manifest")
        if not isinstance(manifest, str):
            raise AdapterStartupError(
                "[source.dbt] needs `semantic_manifest`, the path to the dbt project's "
                "target/semantic_manifest.json."
            )
        connection_config = config.get("connection")
        if not isinstance(connection_config, Mapping) or not isinstance(
            connection_config.get("type"), str
        ):
            raise AdapterStartupError(
                '[source.dbt.connection] needs `type` (e.g. type = "duckdb") and its settings.'
            )
        connection_type = connection_config["type"]

        # 1. The manifest exists and MetricFlow can parse it. 2. The type has a dialect.
        compiler = MetricFlowCompiler((config_dir / manifest).resolve(), connection_type)
        connection = open_connection(connection_config, config_dir)

        # 3. Every semantic model's relation resolves: dbt baked the names into the SQL.
        relations = compiler.relations()
        missing = set(connection.missing_relations(relations.values()))
        if missing:
            listed = ", ".join(
                f"{relation} (semantic model {model})"
                for model, relation in relations.items()
                if relation in missing
            )
            raise AdapterStartupError(
                f"These tables from the semantic manifest don't exist on "
                f"{connection.description}: {listed}. dbt writes the database, schema and table "
                "names into the manifest when it parses the project; for DuckDB the database "
                "name is the file name without its extension. Point the connection at the "
                "database dbt built, or run `dbt parse` against this one."
            )
        return cls(compiler, connection)

    def capabilities(self) -> Capabilities:
        return Capabilities(identity_mode=self._connection.identity_mode)

    def discover(self) -> Catalog:
        raise NotImplementedError

    def relations(self, metrics: Sequence[str]) -> frozenset[str]:
        raise NotImplementedError

    def execute(self, query: SemanticQuery, viewer: Viewer) -> Result:
        sql = self._compiler.compile(_query.compile_request(query))
        names, rows = self._connection.query(sql, viewer)
        return _query.to_result(query, names, rows)


def create_adapter(config: Mapping[str, Any], config_dir: Path) -> DbtAdapter:
    """The `dbt` entry point in the `prumi.adapters` group."""
    return DbtAdapter.from_config(config, config_dir)
