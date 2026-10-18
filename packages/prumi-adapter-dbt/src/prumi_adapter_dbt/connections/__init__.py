"""Prumi's own connections to the warehouse. MetricFlow never sees them.

A connection opens for each query and closes afterwards, so a later per-viewer connection
only replaces this factory.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from prumi.adapters import AdapterStartupError, IdentityMode, Viewer


class Connection(Protocol):
    identity_mode: IdentityMode
    description: str  # names the warehouse in messages, e.g. "the DuckDB file demo.duckdb"

    def query(self, sql: str, viewer: Viewer) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run `sql` as `viewer`: the column names and the rows."""
        ...

    def missing_relations(self, relations: Iterable[str]) -> list[str]:
        """The relations that don't resolve on this connection."""
        ...


def open_connection(config: Mapping[str, Any], config_dir: Path) -> Connection:
    """The connection for `[source.dbt.connection]`.

    Its `type` was checked against the MetricFlow dialects (_metricflow.py), which list exactly
    the connection types opened here.
    """
    if config["type"] == "duckdb":
        from prumi_adapter_dbt.connections.duckdb import DuckDBConnection

        return DuckDBConnection.from_config(config, config_dir)
    raise AdapterStartupError(f"Unsupported connection type {config['type']!r}")
