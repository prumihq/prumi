"""Build the demo warehouse and semantic manifest.

    uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py

Writes `demo.duckdb` (raw tables from generate.py, then dbt models) and `target/`, including
`target/semantic_manifest.json`. Idempotent: every run rebuilds from scratch in a temporary
folder and only replaces the outputs once `dbt build` and `dbt parse` have succeeded.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from generate import generate

HERE = Path(__file__).resolve().parent
DB_NAME = "demo.duckdb"  # DuckDB names the database after the file; the manifest bakes it in


def run_dbt(command: str, db_path: Path, target_path: Path) -> None:
    # A separate process: dbt-duckdb keeps the database file open in-process, which would block
    # moving it and any read-only connection (spike caveat 8).
    dbt = Path(sys.executable).parent / "dbt"
    env = {
        **os.environ,
        "DEMO_DUCKDB_PATH": str(db_path),
        "DO_NOT_TRACK": "1",
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    args = [
        str(dbt),
        command,
        "--project-dir",
        str(HERE),
        "--profiles-dir",
        str(HERE),
        "--target-path",
        str(target_path),
        "--quiet",
    ]
    subprocess.run(args, env=env, check=True)


def build() -> None:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=HERE, prefix=".build-") as tmp:
        work = Path(tmp)
        db_path = work / DB_NAME
        target_path = work / "target"

        counts = generate(db_path)
        print("generated:", ", ".join(f"{t} {n}" for t, n in counts.items()))
        run_dbt("build", db_path, target_path)
        run_dbt("parse", db_path, target_path)
        if not (target_path / "semantic_manifest.json").is_file():
            raise SystemExit("dbt parse did not write target/semantic_manifest.json")

        for name in (DB_NAME, f"{DB_NAME}.wal"):
            (HERE / name).unlink(missing_ok=True)
        shutil.rmtree(HERE / "target", ignore_errors=True)
        db_path.replace(HERE / DB_NAME)
        if (work / f"{DB_NAME}.wal").exists():
            (work / f"{DB_NAME}.wal").replace(HERE / f"{DB_NAME}.wal")
        target_path.replace(HERE / "target")
    print(f"built {HERE / DB_NAME} and {HERE / 'target'} in {time.perf_counter() - started:.1f} s")


if __name__ == "__main__":
    build()
