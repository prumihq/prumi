"""Prumi's own connections to the warehouse. MetricFlow never sees them.

A connection opens for each query and closes afterwards, so a later per-viewer connection
only replaces this factory.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from prumi.adapters import AdapterStartupError, IdentityMode, Viewer


class Connection(Protocol):
    identity_mode: IdentityMode
    description: str  # names the warehouse in messages, e.g. "the DuckDB file demo.duckdb"
    missing_hint: str  # a sentence ending the missing-tables message, specific to this warehouse

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


def refuse_unknown_keys(
    config: Mapping[str, Any], allowed: Collection[str], connection_type: str
) -> None:
    """Stop the server on a key this connection type doesn't take.

    The message names the keys, never their values: a misspelled `password` must not echo.
    """
    unknown = sorted(set(config) - set(allowed))
    if unknown:
        raise AdapterStartupError(
            f"[source.dbt.connection] has {'a key' if len(unknown) == 1 else 'keys'} the "
            f"{connection_type} connection doesn't take: {', '.join(unknown)}. "
            f"It takes: {', '.join(sorted(allowed))}."
        )
