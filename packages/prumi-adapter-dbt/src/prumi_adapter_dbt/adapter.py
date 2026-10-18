"""The dbt adapter: MetricFlow compiles, Prumi executes (ADR 0002)."""

from __future__ import annotations

import logging
import re
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
from prumi_adapter_dbt.catalog import SourceCatalog, build_catalog
from prumi_adapter_dbt.connections import Connection, open_connection

log = logging.getLogger("prumi_adapter_dbt")

# Literals in compiled SQL: quoted strings (with '' inside) and numbers outside identifiers.
_LITERAL = re.compile(r"'(?:[^']|'')*'|(?<![\w.])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")


def redact(sql: str) -> str:
    """The SQL with every literal replaced by ?: filter values and window dates are literals,
    and logs never hold row or filter values."""
    return _LITERAL.sub("?", sql)


class DbtAdapter:
    name = "dbt"

    def __init__(self, compiler: MetricFlowCompiler, connection: Connection) -> None:
        self._compiler = compiler
        self._connection = connection
        self._catalog: SourceCatalog = build_catalog(compiler.metrics())  # read once

    @classmethod
    def from_config(cls, config: Mapping[str, Any], config_dir: Path) -> DbtAdapter:
        """Load the manifest, open the connection and run the startup checks: anything wrong
        stops the server before it binds, with a message naming the problem."""
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
        return self._catalog.catalog

    def relations(self, metrics: Sequence[str]) -> frozenset[str]:
        return self._catalog.relations(metrics)

    def execute(self, query: SemanticQuery, viewer: Viewer) -> Result:
        # Only the catalog's names: MetricFlow would also accept the longer join paths that
        # the shortest-path rule drops.
        valid = self.relations(query.metrics)
        used = {*query.dimensions, *(f.dimension for f in query.filters)}
        unknown = sorted(used - valid)
        if unknown:
            raise ValueError(
                f"Not a dimension of {', '.join(query.metrics) or 'the catalog'}: "
                f"{', '.join(unknown)}"
            )
        sql = self._compiler.compile(_query.compile_request(query))
        if log.isEnabledFor(logging.DEBUG):
            log.debug("compiled SQL (literals redacted):\n%s", redact(sql))
        names, rows = self._connection.query(sql, viewer)
        return _query.to_result(query, names, rows)


def create_adapter(config: Mapping[str, Any], config_dir: Path) -> DbtAdapter:
    """The `dbt` entry point in the `prumi.adapters` group."""
    return DbtAdapter.from_config(config, config_dir)
