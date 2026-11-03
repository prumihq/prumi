"""Times the "data through" cost on the Postgres demo: one connection, the home page's
freshness query cold, and each block of the demo dashboard.

    uv run python spikes/postgres-timing/measure.py path/to/prumi.toml

The password comes from PGPASSWORD, as for Prumi itself.
"""

import sys
import time
import tomllib
from pathlib import Path

import psycopg
from prumi.adapters import LOCAL_VIEWER
from prumi.config import load_config
from prumi.services import Services
from prumi.spec import DashboardSpec, load_spec_file

DASHBOARD = Path(__file__).parents[2] / "packages/prumi-core/tests/fixtures/demo/sales.yaml"


def timed(label, fn):
    start = time.perf_counter()
    result = fn()
    print(f"{label:<44} {(time.perf_counter() - start) * 1000:9.1f} ms")
    return result


def open_connection(config_path: Path) -> None:
    keys = tomllib.loads(config_path.read_text())["source"]["dbt"]["connection"]
    # A throwaway copy of the adapter's connection options; it can drift from them.
    with psycopg.connect(
        host=keys["host"],
        port=keys.get("port", 5432),
        dbname=keys["dbname"],
        user=keys["user"],
        connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        conn.execute("SELECT 1")


def main(config_path: Path) -> None:
    timed("open one connection (+ SELECT 1)", lambda: open_connection(config_path))
    services = Services(load_config(config_path))
    timed("start the adapter (source)", lambda: services.source)
    timed("data_through() cold", services.data_through)
    spec = DashboardSpec.model_validate(load_spec_file(DASHBOARD))
    for block in spec.data:
        timed(
            f"run_block {block.id}",
            lambda block=block: services.engine.run_block(spec, block.id, {}, LOCAL_VIEWER),
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
