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
