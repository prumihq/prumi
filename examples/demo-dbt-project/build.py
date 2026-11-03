"""Build the demo warehouse and semantic manifest.

    uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py

Writes `demo.duckdb` (raw tables from generate.py, then dbt models) and `target/`, including
`target/semantic_manifest.json`. Idempotent: every run rebuilds from scratch in a temporary
folder and only replaces the outputs once `dbt build` and `dbt parse` have succeeded.

    ... build.py --target postgres

Builds the same project in a Postgres database instead (schemas `raw` and `main`) and writes
`target-postgres/`. The server comes from `DEMO_POSTGRES_HOST` (default `localhost`),
`DEMO_POSTGRES_PORT` (`5432`), `DEMO_POSTGRES_USER` (`postgres`), `DEMO_POSTGRES_PASSWORD`
(required) and `DEMO_POSTGRES_DBNAME` (`prumi_demo`, created when missing). The DuckDB outputs
are left alone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from generate import generate, generate_postgres

HERE = Path(__file__).resolve().parent
DB_NAME = "demo.duckdb"  # DuckDB names the database after the file; the manifest bakes it in


def run_dbt(
    command: str, target_path: Path, extra_env: dict[str, str], extra_args: list[str] = ()
) -> None:
    # A separate process: dbt-duckdb keeps the database file open in-process, which would block
    # moving it and any read-only connection (spike caveat 8).
    dbt = Path(sys.executable).parent / "dbt"
    env = {
        **os.environ,
        **extra_env,
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
        *extra_args,
    ]
    subprocess.run(args, env=env, check=True)


def build_duckdb() -> None:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=HERE, prefix=".build-") as tmp:
        work = Path(tmp)
        db_path = work / DB_NAME
        target_path = work / "target"
        env = {"DEMO_DUCKDB_PATH": str(db_path)}

        counts = generate(db_path)
        print("generated:", ", ".join(f"{t} {n}" for t, n in counts.items()))
        run_dbt("build", target_path, env)
        run_dbt("parse", target_path, env)
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


def build_postgres() -> None:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    started = time.perf_counter()
    password = os.environ.get("DEMO_POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("Set DEMO_POSTGRES_PASSWORD to build the demo on Postgres.")
    settings = {
        "host": os.environ.get("DEMO_POSTGRES_HOST", "localhost"),
        "port": os.environ.get("DEMO_POSTGRES_PORT", "5432"),
        "user": os.environ.get("DEMO_POSTGRES_USER", "postgres"),
        "password": password,
    }
    dbname = os.environ.get("DEMO_POSTGRES_DBNAME", "prumi_demo")

    with psycopg.connect(make_conninfo(**settings, dbname="postgres"), autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if exists.fetchone() is None:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    conninfo = make_conninfo(**settings, dbname=dbname)
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS main CASCADE")
        conn.execute("CREATE SCHEMA main")

    with tempfile.TemporaryDirectory(dir=HERE, prefix=".build-") as tmp:
        target_path = Path(tmp) / "target"
        env = {"DEMO_POSTGRES_DBNAME": dbname}
        flags = ["--target", "postgres"]

        counts = generate_postgres(conninfo)
        print("generated:", ", ".join(f"{t} {n}" for t, n in counts.items()))
        run_dbt("build", target_path, env, flags)
        run_dbt("parse", target_path, env, flags)
        if not (target_path / "semantic_manifest.json").is_file():
            raise SystemExit("dbt parse did not write semantic_manifest.json")

        shutil.rmtree(HERE / "target-postgres", ignore_errors=True)
        target_path.replace(HERE / "target-postgres")
    print(
        f"built {dbname} on {settings['host']} and {HERE / 'target-postgres'} "
        f"in {time.perf_counter() - started:.1f} s"
    )


def build(target: str = "duckdb") -> None:
    if target == "postgres":
        build_postgres()
    else:
        build_duckdb()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=["duckdb", "postgres"], default="duckdb")
    build(parser.parse_args().target)
