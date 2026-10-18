"""DuckDB: a read-only connection to the database file, opened for each query."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from prumi.adapters import AdapterStartupError, IdentityMode, Viewer

try:
    import duckdb
except ImportError as exc:  # the `duckdb` extra isn't installed
    raise AdapterStartupError(
        "The duckdb connection needs the duckdb package: install prumi-adapter-dbt[duckdb]."
    ) from exc


class DuckDBConnection:
    # One file, one account: every viewer reads the same data.
    identity_mode: IdentityMode = "shared_account"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.description = f"the DuckDB file {path}"

    @classmethod
    def from_config(cls, config: Mapping[str, Any], config_dir: Path) -> DuckDBConnection:
        if not isinstance(config.get("path"), str):
            raise AdapterStartupError(
                "[source.dbt.connection] needs `path`, the DuckDB file dbt built."
            )
        path = (config_dir / config["path"]).resolve()
        if not path.is_file():
            raise AdapterStartupError(
                f"The DuckDB file {path} doesn't exist. Check `path` under "
                "[source.dbt.connection] in prumi.toml (relative paths start at its folder)."
            )
        return cls(path)

    def query(self, sql: str, viewer: Viewer) -> tuple[list[str], list[tuple[Any, ...]]]:
        with duckdb.connect(str(self.path), read_only=True) as conn:
            cursor = conn.execute(sql)
            names = [column[0] for column in cursor.description]
            return names, cursor.fetchall()

    def missing_relations(self, relations: Iterable[str]) -> list[str]:
        missing = []
        with duckdb.connect(str(self.path), read_only=True) as conn:
            for relation in relations:
                try:
                    conn.execute(f"SELECT 1 FROM {relation} LIMIT 0")
                except (duckdb.CatalogException, duckdb.BinderException):
                    # No such table, or no such database (a renamed file).
                    missing.append(relation)
        return missing
