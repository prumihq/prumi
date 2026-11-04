"""What a Postgres registry checks when it starts, and what it says when it can't be used.

Every message names the setting to change and never the password."""

import sys
import uuid

import pytest
from prumi.config import RegistryPostgresConfig
from prumi.registry import Registry, RegistryError
from prumi.registry.stores import PostgresStore

SENTINEL = "not-the-password-4d1e9b"


@pytest.fixture
def fresh_db(postgres_server):
    """An empty database of its own, dropped afterwards."""
    from psycopg import sql

    dbname = f"prumi_registry_{uuid.uuid4().hex}"
    with postgres_server.admin_connect() as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    yield dbname
    with postgres_server.admin_connect() as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))


@pytest.fixture
def connect_only_role(postgres_server, fresh_db):
    """A role that may log in and connect to `fresh_db`, and nothing more."""
    from psycopg import sql

    role, password = f"prumi_registry_{uuid.uuid4().hex[:12]}", "connect-only-test"
    with postgres_server.admin_connect() as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(fresh_db), sql.Identifier(role)
            )
        )
    yield role, password
    with postgres_server.admin_connect() as conn:
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _store(server, dbname, user=None, password=None, port=None) -> PostgresStore:
    settings = RegistryPostgresConfig(
        server.host, port or server.port, dbname, user or server.admin_user
    )
    return PostgresStore(settings, server.admin_password if password is None else password)


def _refusal(store: PostgresStore) -> RegistryError:
    with pytest.raises(RegistryError) as raised:
        Registry(store)
    return raised.value


def test_a_database_that_holds_other_tables_is_refused(postgres_server, fresh_db):
    with postgres_server.admin_connect(fresh_db) as conn:
        conn.execute("CREATE TABLE public.orders (id integer)")

    error = _refusal(_store(postgres_server, fresh_db))

    assert "its own database" in str(error)
    assert fresh_db in str(error)
    assert "public.orders" in str(error)


def test_the_refused_database_is_left_as_it_was(postgres_server, fresh_db):
    with postgres_server.admin_connect(fresh_db) as conn:
        conn.execute("CREATE TABLE public.orders (id integer)")

    _refusal(_store(postgres_server, fresh_db))

    with postgres_server.admin_connect(fresh_db) as conn:
        assert conn.execute("SELECT to_regclass('schema_version')").fetchone()[0] is None


@pytest.fixture
def sql_ascii_db(postgres_server):
    """An empty database in SQL_ASCII, dropped afterwards."""
    from psycopg import sql

    dbname = f"prumi_registry_{uuid.uuid4().hex}"
    with postgres_server.admin_connect() as conn:
        conn.execute(
            sql.SQL(
                "CREATE DATABASE {} ENCODING 'SQL_ASCII' TEMPLATE template0"
                " LC_COLLATE 'C' LC_CTYPE 'C'"
            ).format(sql.Identifier(dbname))
        )
    yield dbname
    with postgres_server.admin_connect() as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))


def test_a_database_not_in_utf8_is_refused(postgres_server, sql_ascii_db):
    error = _refusal(_store(postgres_server, sql_ascii_db))

    assert sql_ascii_db in str(error)
    assert "UTF8" in str(error)
    with postgres_server.admin_connect(sql_ascii_db) as conn:
        assert conn.execute("SELECT to_regclass('schema_version')").fetchone()[0] is None


def test_once_the_registry_exists_the_check_does_not_run_again(postgres_server, fresh_db):
    Registry(_store(postgres_server, fresh_db))
    with postgres_server.admin_connect(fresh_db) as conn:
        conn.execute("CREATE TABLE public.notes (id integer)")

    Registry(_store(postgres_server, fresh_db)).dashboards()


def test_a_wrong_password_is_refused_without_showing_it(postgres_server, fresh_db):
    error = _refusal(_store(postgres_server, fresh_db, password=SENTINEL))

    assert "PRUMI_REGISTRY_PASSWORD" in str(error)
    assert postgres_server.admin_user in str(error)
    assert SENTINEL not in str(error)
    assert error.__context__ is None and error.__cause__ is None


def test_a_missing_database_is_named(postgres_server):
    dbname = f"prumi_registry_missing_{uuid.uuid4().hex[:8]}"

    error = _refusal(_store(postgres_server, dbname))

    assert dbname in str(error)
    assert "`dbname`" in str(error)
    assert error.__context__ is None


def test_an_unreachable_server_names_host_and_port(postgres_server):
    settings = RegistryPostgresConfig("127.0.0.1", 1, "prumi", "prumi")

    error = _refusal(PostgresStore(settings, SENTINEL))

    assert "127.0.0.1:1" in str(error)
    assert SENTINEL not in str(error)
    assert error.__context__ is None


def test_a_role_that_cannot_create_tables_is_refused(postgres_server, fresh_db, connect_only_role):
    role, password = connect_only_role

    error = _refusal(_store(postgres_server, fresh_db, user=role, password=password))

    assert role in str(error)
    assert "create tables" in str(error)
    assert fresh_db in str(error)
    assert password not in str(error)
    assert error.__context__ is None


def test_postgres_without_psycopg_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)  # `import psycopg` now fails
    settings = RegistryPostgresConfig("127.0.0.1", 5432, "prumi", "prumi")

    error = _refusal(PostgresStore(settings, None))

    assert "prumi[postgres]" in str(error)


# The server and the CLI on a Postgres registry


def _with_registry(path, host, port, dbname, user) -> None:
    """Add a Postgres [registry] to the prumi.toml at `path`."""
    path.write_text(
        path.read_text()
        + f"""
[registry]
backend = "postgres"

[registry.postgres]
host = "{host}"
port = {port}
dbname = "{dbname}"
user = "{user}"
"""
    )


def test_the_server_and_the_cli_share_the_postgres_registry(
    tmp_path, monkeypatch, capsys, postgres_server, fresh_db, demo_project, demo_source
):
    import asyncio

    import httpx2
    from mcp import Client
    from prumi.cli import OK, main
    from prumi.config import load_config
    from prumi.spec import load_spec_file

    from .conftest import DEMO_DASHBOARD, LiveServer, SuiteServices, free_port, write_demo_config

    server = postgres_server
    monkeypatch.setenv("PRUMI_REGISTRY_PASSWORD", server.admin_password)
    path = write_demo_config(tmp_path, demo_project)
    path.write_text(path.read_text().replace("port = 8484", f"port = {free_port()}"))
    _with_registry(path, server.host, server.port, fresh_db, server.admin_user)
    services = SuiteServices(load_config(path))
    services.source = demo_source
    spec = load_spec_file(DEMO_DASHBOARD / "sales.yaml")
    html = (DEMO_DASHBOARD / "sales.html").read_text()

    async def publish(url):
        async with Client(f"{url}/mcp") as client:
            return await client.call_tool("publish_dashboard", {"spec": spec, "html": html})

    with LiveServer(services) as live:
        published = asyncio.run(publish(live.url))
        assert not published.is_error, published.content
        assert published.structured_content["version"] == 1

        code = main(["certify", "sales", "1", "--by", "Ana", "--config", str(path)])
        assert code == OK, capsys.readouterr()

        status = httpx2.get(f"{live.url}/api/dashboards/sales/versions/1/status").json()
    assert status["status"] == "certified"
    assert status["certified_by"] == "Ana"
    with server.admin_connect(fresh_db) as conn:
        assert conn.execute("SELECT certified_by FROM certifications").fetchall() == [("Ana",)]
    assert not (tmp_path / "data" / "registry.sqlite3").exists()


def test_serve_stops_before_binding_when_the_registry_is_down(
    tmp_path, monkeypatch, capsys, make_demo_config
):
    from prumi.cli import REFUSED, main

    started = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: started.append("uvicorn"))
    monkeypatch.setattr(
        "prumi.services.shared_checker", lambda: pytest.fail("Chromium started before the check")
    )
    monkeypatch.setenv("PRUMI_REGISTRY_PASSWORD", SENTINEL)
    path = make_demo_config(tmp_path)
    _with_registry(path, "127.0.0.1", 1, "prumi", "prumi")

    assert main(["serve", "--config", str(path)]) == REFUSED

    err = capsys.readouterr().err
    assert err.startswith("prumi: ")
    assert "127.0.0.1:1" in err
    assert SENTINEL not in err
    assert "Traceback" not in err
    assert started == []
