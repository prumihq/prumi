"""Shared pytest fixtures for the workspace.

`demo_project` builds the demo dbt project once per session when its outputs are missing or
older than the generator and models, and reuses them otherwise. Every test that uses it is
marked `demo`, so `pytest -m "not demo"` skips them.

`postgres_demo` does the same on a Postgres server, which the tests reach through
`PRUMI_TEST_POSTGRES_HOST` (with `_PORT`, `_USER`, `_PASSWORD`); `postgres_server` is that
server. Without the host their tests skip, or fail when `PRUMI_REQUIRE_POSTGRES` is set. Tests
using either are marked `postgres`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import psycopg

REPO = Path(__file__).parent
DEMO_DIR = REPO / "examples" / "demo-dbt-project"
DEMO_OUTPUTS = [DEMO_DIR / "demo.duckdb", DEMO_DIR / "target" / "semantic_manifest.json"]
POSTGRES_MANIFEST = DEMO_DIR / "target-postgres" / "semantic_manifest.json"


@dataclass(frozen=True)
class DemoProject:
    root: Path
    db_path: Path
    manifest_path: Path
    config_path: Path


def _demo_inputs() -> list[Path]:
    inputs = [DEMO_DIR / name for name in ("generate.py", "build.py", "dbt_project.yml")]
    inputs += [DEMO_DIR / "profiles.yml", DEMO_DIR / "pyproject.toml"]
    return inputs + sorted((DEMO_DIR / "models").rglob("*"))


def demo_is_stale() -> bool:
    if not all(path.is_file() for path in DEMO_OUTPUTS):
        return True
    oldest_output = min(path.stat().st_mtime for path in DEMO_OUTPUTS)
    newest_input = max(path.stat().st_mtime for path in _demo_inputs())
    return newest_input > oldest_output


def build_demo(target: str = "duckdb", extra_env: dict[str, str] | None = None) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("Building the demo dbt project needs `uv` on PATH.")
    # The demo has its own environment; don't let the workspace's venv leak into it.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | (extra_env or {})
    command = [uv, "run", "--project", str(DEMO_DIR), "python", str(DEMO_DIR / "build.py")]
    command += ["--target", target]
    result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(
            f"Building the demo dbt project failed ({' '.join(command)}):\n"
            f"{result.stdout}\n{result.stderr}"
        )


@pytest.fixture(scope="session", autouse=True)
def no_ambient_settings() -> Iterator[None]:
    """Drop PRUMI__ settings from the developer's shell (a sourced .env, say), so they can't
    change what a test loads. Session-wide, because session fixtures load configs too. A test
    that wants some passes `environ` or sets them with monkeypatch."""
    with pytest.MonkeyPatch.context() as env:
        for name in list(os.environ):
            if name.startswith("PRUMI__"):
                env.delenv(name)
        yield


@pytest.fixture(scope="session")
def demo_project() -> DemoProject:
    if demo_is_stale():
        build_demo()
    return DemoProject(
        root=DEMO_DIR,
        db_path=DEMO_OUTPUTS[0],
        manifest_path=DEMO_OUTPUTS[1],
        config_path=DEMO_DIR / "prumi.toml",
    )


@dataclass(frozen=True)
class PostgresServer:
    host: str
    port: int
    admin_user: str
    admin_password: str

    @classmethod
    def from_env(cls) -> PostgresServer | None:
        host = os.environ.get("PRUMI_TEST_POSTGRES_HOST")
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("PRUMI_TEST_POSTGRES_PORT", "5432")),
            admin_user=os.environ.get("PRUMI_TEST_POSTGRES_USER", "postgres"),
            admin_password=os.environ.get("PRUMI_TEST_POSTGRES_PASSWORD", "postgres"),
        )

    def admin_connect(self, dbname: str = "postgres") -> psycopg.Connection:
        import psycopg

        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password,
            dbname=dbname,
            autocommit=True,
        )


@dataclass(frozen=True)
class PostgresDemo:
    server: PostgresServer
    manifest_path: Path
    dbname: str = "prumi_demo"
    reader: str = "prumi_reader"
    reader_password: str = "prumi-reader-test"
    reader_password_env: str = "PRUMI_TEST_READER_PASSWORD"

    def connection_config(self, **overrides: object) -> dict:
        """The `[source.dbt.connection]` table for the reader. The password is not part of it:
        it's in the variable `password_env` names, which the `postgres_demo` fixture sets."""
        config = {
            "type": "postgres",
            "host": self.server.host,
            "port": self.server.port,
            "dbname": self.dbname,
            "user": self.reader,
            "password_env": self.reader_password_env,
        }
        return config | overrides

    def admin_connect(self, dbname: str | None = None) -> psycopg.Connection:
        return self.server.admin_connect(dbname or self.dbname)


def _postgres_demo_is_stale(demo: PostgresDemo) -> bool:
    if not POSTGRES_MANIFEST.is_file():
        return True
    if max(path.stat().st_mtime for path in _demo_inputs()) > POSTGRES_MANIFEST.stat().st_mtime:
        return True
    try:
        with demo.admin_connect() as conn:
            return conn.execute("SELECT to_regclass('main.orders')").fetchone()[0] is None
    except Exception:  # the database doesn't exist yet
        return True


def _grant_reader(demo: PostgresDemo) -> None:
    from psycopg import sql

    reader = sql.Identifier(demo.reader)
    with demo.admin_connect("postgres") as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (demo.reader,))
        verb = "ALTER" if exists.fetchone() else "CREATE"
        conn.execute(
            sql.SQL(f"{verb} ROLE {{}} LOGIN PASSWORD {{}}").format(
                reader, sql.Literal(demo.reader_password)
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(demo.dbname), reader
            )
        )
    with demo.admin_connect() as conn:
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA main TO {}").format(reader))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA main TO {}").format(reader))


@pytest.fixture(scope="session")
def postgres_server() -> PostgresServer:
    server = PostgresServer.from_env()
    if server is None:
        message = (
            "Set PRUMI_TEST_POSTGRES_HOST to run the Postgres tests, for example: "
            "docker run -d --name prumi-postgres -e POSTGRES_PASSWORD=postgres -p 5432:5432 "
            "postgres:18, then PRUMI_TEST_POSTGRES_HOST=localhost uv run pytest"
        )
        if os.environ.get("PRUMI_REQUIRE_POSTGRES"):
            pytest.fail(message)
        pytest.skip(message)
    pytest.importorskip("psycopg")
    return server


@pytest.fixture(scope="session")
def postgres_demo(postgres_server: PostgresServer) -> Iterator[PostgresDemo]:
    server = postgres_server
    demo = PostgresDemo(server=server, manifest_path=POSTGRES_MANIFEST)
    if _postgres_demo_is_stale(demo):
        build_demo(
            "postgres",
            {
                "DEMO_POSTGRES_HOST": server.host,
                "DEMO_POSTGRES_PORT": str(server.port),
                "DEMO_POSTGRES_USER": server.admin_user,
                "DEMO_POSTGRES_PASSWORD": server.admin_password,
                "DEMO_POSTGRES_DBNAME": demo.dbname,
            },
        )
    _grant_reader(demo)
    with pytest.MonkeyPatch.context() as env:
        env.setenv(demo.reader_password_env, demo.reader_password)
        yield demo


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        fixtures = getattr(item, "fixturenames", ())
        if "demo_project" in fixtures:
            item.add_marker(pytest.mark.demo)
        if "postgres_server" in fixtures:  # postgres_demo uses it too
            item.add_marker(pytest.mark.postgres)
