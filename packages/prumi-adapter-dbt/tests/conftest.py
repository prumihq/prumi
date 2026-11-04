from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from prumi.adapters import load_adapter_factory
from prumi_adapter_dbt._metricflow import MetricFlowCompiler


def _plain(value: object) -> object:
    return float(value) if isinstance(value, Decimal) else value


@dataclass(frozen=True)
class Warehouse:
    """A demo warehouse the adapter tests run against: DuckDB or Postgres."""

    name: str
    dbt_config: dict
    manifest_path: Path
    config_dir: Path
    _open: object  # () -> context manager yielding a DB-API-like connection

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Column names and rows of `sql`, run read-only, with Decimal turned into float."""
        with self._open() as conn:
            cursor = conn.execute(sql)
            names = [d[0] for d in cursor.description]
            rows = [tuple(_plain(v) for v in row) for row in cursor.fetchall()]
        return names, rows

    def hand(self, sql: str) -> list[tuple]:
        return self.query(sql)[1]


def _duckdb_warehouse(demo_project) -> Warehouse:
    import tomllib

    with demo_project.config_path.open("rb") as f:
        dbt_config = tomllib.load(f)["source"]["dbt"]
    return Warehouse(
        name="duckdb",
        dbt_config=dbt_config,
        manifest_path=demo_project.manifest_path,
        config_dir=demo_project.root,
        _open=lambda: duckdb.connect(str(demo_project.db_path), read_only=True),
    )


def _postgres_warehouse(demo) -> Warehouse:
    import psycopg

    def open_reader():
        return psycopg.connect(
            host=demo.server.host,
            port=demo.server.port,
            dbname=demo.dbname,
            user=demo.reader,
            password=demo.reader_password,
            options="-c default_transaction_read_only=on",
        )

    return Warehouse(
        name="postgres",
        dbt_config={
            "semantic_manifest": str(demo.manifest_path),
            "connection": demo.connection_config(),
        },
        manifest_path=demo.manifest_path,
        config_dir=demo.manifest_path.parent,
        _open=open_reader,
    )


@pytest.fixture(
    scope="session",
    params=["duckdb", pytest.param("postgres", marks=pytest.mark.postgres)],
)
def warehouse(request) -> Warehouse:
    if request.param == "duckdb":
        return _duckdb_warehouse(request.getfixturevalue("demo_project"))
    return _postgres_warehouse(request.getfixturevalue("postgres_demo"))


@pytest.fixture(scope="session")
def compiler(warehouse) -> MetricFlowCompiler:
    return MetricFlowCompiler(warehouse.manifest_path, warehouse.name)


@pytest.fixture(scope="session")
def adapter(warehouse):
    """The dbt adapter, loaded through its entry point like the server does."""
    return load_adapter_factory("dbt")(warehouse.dbt_config, warehouse.config_dir)
