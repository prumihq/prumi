"""Connecting to Postgres: startup checks name what to fix, and never show the password."""

import datetime as dt
import logging

import psycopg
import pytest
from prumi.adapters import LOCAL_VIEWER, AdapterStartupError, SemanticQuery
from prumi.source import Source
from prumi_adapter_dbt.adapter import DbtAdapter
from prumi_adapter_dbt.connections import ConnectionFailed
from prumi_adapter_dbt.connections.postgres import PostgresConnection
from psycopg import sql

SENTINEL = "SENTINEL-7f3a9c"


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG)  # every logger, every level
    return caplog


def all_text(caplog) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(repr(record.args))
        if record.exc_info:
            parts.append(logging.Formatter().formatException(record.exc_info))
    return "\n".join(parts)


def config_for(postgres_demo, **overrides) -> dict:
    return {
        "semantic_manifest": str(postgres_demo.manifest_path),
        "connection": postgres_demo.connection_config(**overrides),
    }


def start(config, config_dir) -> AdapterStartupError:
    with pytest.raises(AdapterStartupError) as error:
        DbtAdapter.from_config(config, config_dir)
    assert error.value.__cause__ is None and error.value.__context__ is None
    return error.value


def test_the_demo_starts_on_postgres(postgres_demo, tmp_path):
    adapter = DbtAdapter.from_config(config_for(postgres_demo), tmp_path)
    assert adapter.capabilities().identity_mode == "shared_account"
    assert adapter.describe().connection == "postgres"


def test_wrong_password_never_shows_it(postgres_demo, tmp_path, monkeypatch, logs):
    monkeypatch.setenv("PGPASSWORD", SENTINEL)
    message = str(start(config_for(postgres_demo), tmp_path))
    assert "prumi_reader" in message and "PGPASSWORD" in message
    assert SENTINEL not in message
    assert SENTINEL not in all_text(logs)


def test_unknown_database(postgres_demo, tmp_path, logs):
    message = str(start(config_for(postgres_demo, dbname="prumi_nowhere"), tmp_path))
    assert "`dbname`" in message and "prumi_nowhere" in message
    assert "prumi-reader-test" not in message + all_text(logs)


def test_unreachable_port(postgres_demo, tmp_path, logs):
    message = str(start(config_for(postgres_demo, port=1), tmp_path))
    assert "`port`" in message and f"{postgres_demo.server.host}:1" in message
    assert "prumi-reader-test" not in message + all_text(logs)


def test_unknown_time_zone(postgres_demo, tmp_path):
    message = str(start(config_for(postgres_demo, timezone="Mars/Olympus"), tmp_path))
    assert "`timezone`" in message and "'Mars/Olympus'" in message


def test_sslmode_the_server_cannot_honor(postgres_demo, tmp_path):
    message = str(start(config_for(postgres_demo, sslmode="require"), tmp_path))
    assert "`sslmode`" in message


def test_the_password_can_come_from_a_pgpass_file(postgres_demo, tmp_path, monkeypatch):
    monkeypatch.delenv("PGPASSWORD")
    server = postgres_demo.server
    pgpass = tmp_path / "pgpass"
    pgpass.write_text(f"{server.host}:{server.port}:prumi_demo:prumi_reader:prumi-reader-test\n")
    pgpass.chmod(0o600)
    monkeypatch.setenv("PGPASSFILE", str(pgpass))
    assert DbtAdapter.from_config(config_for(postgres_demo), tmp_path).name == "dbt"


def test_prumi_cannot_write_even_as_a_writer(postgres_demo, tmp_path, monkeypatch):
    monkeypatch.setenv("PGPASSWORD", postgres_demo.server.admin_password)
    connection = PostgresConnection.from_config(
        postgres_demo.connection_config(user=postgres_demo.server.admin_user), tmp_path
    )
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        connection.query("CREATE TABLE main.x (a int)", LOCAL_VIEWER)


@pytest.fixture
def other_database(postgres_demo):
    with postgres_demo.admin_connect("postgres") as conn:
        conn.execute("DROP DATABASE IF EXISTS prumi_other WITH (FORCE)")
        conn.execute("CREATE DATABASE prumi_other")
        conn.execute("GRANT CONNECT ON DATABASE prumi_other TO prumi_reader")
    yield "prumi_other"
    with postgres_demo.admin_connect("postgres") as conn:
        conn.execute("DROP DATABASE IF EXISTS prumi_other WITH (FORCE)")


def test_database_mismatch_is_named(postgres_demo, tmp_path, other_database):
    message = str(start(config_for(postgres_demo, dbname=other_database), tmp_path))
    assert "prumi_demo" in message and "prumi_other" in message
    assert "across databases" in message


@pytest.fixture
def orders_unreadable(postgres_demo):
    with postgres_demo.admin_connect() as conn:
        conn.execute("REVOKE SELECT ON main.orders FROM prumi_reader")
    try:
        yield
    finally:
        with postgres_demo.admin_connect() as conn:
            conn.execute("GRANT SELECT ON main.orders TO prumi_reader")


def test_unreadable_table_is_named(postgres_demo, tmp_path, orders_unreadable):
    message = str(start(config_for(postgres_demo), tmp_path))
    assert '"prumi_demo"."main"."orders"' in message
    assert '"prumi_demo"."main"."customers"' not in message
    assert "SELECT" in message and "prumi_reader" in message


def test_a_failed_connection_at_query_time_is_clean(postgres_demo, tmp_path, monkeypatch, logs):
    source = Source("dbt", DbtAdapter.from_config(config_for(postgres_demo), tmp_path))
    monkeypatch.setenv("PGPASSWORD", SENTINEL)
    with pytest.raises(ConnectionFailed) as error:
        source.execute(SemanticQuery(metrics=("revenue",)), LOCAL_VIEWER)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert "prumi_reader" in str(error.value) and "PGPASSWORD" in str(error.value)
    assert SENTINEL not in str(error.value)
    assert SENTINEL not in all_text(logs)
    assert any("error=ConnectionFailed" in r.getMessage() for r in logs.records)


TZ_READER = "prumi_tz_reader"
TZ_READER_PASSWORD = "prumi-tz-reader-test"


def _drop_tz_check(conn) -> None:
    conn.execute("DROP SCHEMA IF EXISTS tz_check CASCADE")
    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (TZ_READER,)).fetchone():
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(TZ_READER)))
        conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(TZ_READER)))


@pytest.fixture
def tz_reader(postgres_demo, monkeypatch):
    """A role whose own time zone is Pacific/Kiritimati (UTC+14), reading one timestamptz."""
    role = sql.Identifier(TZ_READER)
    with postgres_demo.admin_connect() as conn:
        _drop_tz_check(conn)
        conn.execute("CREATE SCHEMA tz_check")
        conn.execute("CREATE TABLE tz_check.events (at timestamptz)")
        conn.execute("INSERT INTO tz_check.events VALUES ('2026-10-14 23:30:00+00')")
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                role, sql.Literal(TZ_READER_PASSWORD)
            )
        )
        conn.execute(sql.SQL("ALTER ROLE {} SET timezone = 'Pacific/Kiritimati'").format(role))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE prumi_demo TO {}").format(role))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA tz_check TO {}").format(role))
        conn.execute(sql.SQL("GRANT SELECT ON tz_check.events TO {}").format(role))
    monkeypatch.setenv("PGPASSWORD", TZ_READER_PASSWORD)
    try:
        yield TZ_READER
    finally:
        with postgres_demo.admin_connect() as conn:
            _drop_tz_check(conn)


@pytest.mark.parametrize(
    ("timezone", "day"), [(None, dt.date(2026, 10, 15)), ("UTC", dt.date(2026, 10, 14))]
)
def test_timezone_decides_the_day_of_a_timestamptz(
    postgres_demo, tmp_path, tz_reader, timezone, day
):
    overrides = {"user": tz_reader} | ({"timezone": timezone} if timezone else {})
    connection = PostgresConnection.from_config(
        postgres_demo.connection_config(**overrides), tmp_path
    )
    names, rows = connection.query(
        "SELECT date_trunc('day', at)::date AS day FROM tz_check.events", LOCAL_VIEWER
    )
    assert names == ["day"] and rows == [(day,)]
