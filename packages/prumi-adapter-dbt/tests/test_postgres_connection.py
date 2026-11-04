"""Connecting to Postgres: startup checks name what to fix, and never show the password."""

import dataclasses
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
    monkeypatch.setenv(postgres_demo.reader_password_env, SENTINEL)
    message = str(start(config_for(postgres_demo), tmp_path))
    assert "prumi_reader" in message
    assert SENTINEL not in message
    assert SENTINEL not in all_text(logs)


def test_login_failure_points_to_password_env(postgres_demo, tmp_path, monkeypatch):
    monkeypatch.setenv(postgres_demo.reader_password_env, SENTINEL)
    message = str(start(config_for(postgres_demo), tmp_path))
    assert "refused" in message and "password_env" in message
    assert "PGPASSWORD" not in message and "pgpass" not in message


def test_a_named_variable_that_is_missing_stops_startup(postgres_demo, tmp_path, monkeypatch):
    monkeypatch.delenv(postgres_demo.reader_password_env)
    message = str(start(config_for(postgres_demo), tmp_path))
    assert postgres_demo.reader_password_env in message and "password_env" in message
    assert "prumi-reader-test" not in message


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


def without_password_env(config: dict) -> dict:
    del config["connection"]["password_env"]
    return config


def test_no_password_anywhere_gets_login_advice(postgres_demo, tmp_path, monkeypatch, logs):
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("PGPASSFILE", str(tmp_path / "no-such-pgpass"))
    message = str(start(without_password_env(config_for(postgres_demo)), tmp_path))
    assert "prumi_reader" in message and "password_env" in message and "refused" in message
    assert "prumi-reader-test" not in message + all_text(logs)


def test_pgtz_cannot_override_the_configured_timezone(postgres_demo, tmp_path, monkeypatch):
    monkeypatch.setenv("PGTZ", "Pacific/Kiritimati")
    message = str(start(config_for(postgres_demo, timezone="UTC"), tmp_path))
    assert "`timezone`" in message and "PGTZ" in message


def test_a_timezone_spelled_differently_from_the_server_is_not_an_override(postgres_demo, tmp_path):
    # The server reports `utc` back as `UTC`; that's the same zone.
    connection = PostgresConnection.from_config(
        postgres_demo.connection_config(timezone="utc"), tmp_path
    )
    assert connection.query("SHOW TimeZone", LOCAL_VIEWER)[1] == [("UTC",)]


def test_without_password_env_libpq_still_finds_a_pgpass_file(postgres_demo, tmp_path, monkeypatch):
    # Nothing blocks libpq's own lookup for a server set up that way on purpose.
    monkeypatch.delenv("PGPASSWORD", raising=False)
    server = postgres_demo.server
    pgpass = tmp_path / "pgpass"
    pgpass.write_text(f"{server.host}:{server.port}:prumi_demo:prumi_reader:prumi-reader-test\n")
    pgpass.chmod(0o600)
    monkeypatch.setenv("PGPASSFILE", str(pgpass))
    config = without_password_env(config_for(postgres_demo))
    assert DbtAdapter.from_config(config, tmp_path).name == "dbt"


def test_password_env_wins_over_pgpassword(postgres_demo, tmp_path, monkeypatch):
    monkeypatch.setenv("PGPASSWORD", SENTINEL)
    assert DbtAdapter.from_config(config_for(postgres_demo), tmp_path).name == "dbt"


def test_prumi_cannot_write_even_as_a_writer(postgres_demo, tmp_path, monkeypatch):
    monkeypatch.setenv("PRUMI_TEST_ADMIN_PASSWORD", postgres_demo.server.admin_password)
    connection = PostgresConnection.from_config(
        postgres_demo.connection_config(
            user=postgres_demo.server.admin_user, password_env="PRUMI_TEST_ADMIN_PASSWORD"
        ),
        tmp_path,
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
    adapter = DbtAdapter.from_config(config_for(postgres_demo), tmp_path)
    source = Source("dbt", adapter)
    # The password was read at startup; a password that stops working since, as when it's
    # rotated on the server, is the same failure as this wrong one.
    connection = adapter._connection
    wrong = dataclasses.replace(connection.settings, _password=SENTINEL)
    monkeypatch.setattr(connection, "settings", wrong)
    with pytest.raises(ConnectionFailed) as error:
        source.execute(SemanticQuery(metrics=("revenue",)), LOCAL_VIEWER)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert "prumi_reader" in str(error.value) and "password_env" in str(error.value)
    assert SENTINEL not in str(error.value)
    assert SENTINEL not in all_text(logs)
    assert any("error=ConnectionFailed" in r.getMessage() for r in logs.records)


TZ_READER = "prumi_tz_reader"
TZ_READER_PASSWORD = "prumi-tz-reader-test"
TZ_READER_PASSWORD_ENV = "PRUMI_TEST_TZ_READER_PASSWORD"


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
    monkeypatch.setenv(TZ_READER_PASSWORD_ENV, TZ_READER_PASSWORD)
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
    overrides = {"user": tz_reader, "password_env": TZ_READER_PASSWORD_ENV}
    overrides |= {"timezone": timezone} if timezone else {}
    connection = PostgresConnection.from_config(
        postgres_demo.connection_config(**overrides), tmp_path
    )
    names, rows = connection.query(
        "SELECT date_trunc('day', at)::date AS day FROM tz_check.events", LOCAL_VIEWER
    )
    assert names == ["day"] and rows == [(day,)]


DATESTYLE_READER = "prumi_datestyle_reader"


@pytest.fixture
def german_datestyle_reader(postgres_demo, monkeypatch):
    """A role whose own DateStyle prints timestamps the German way, which psycopg can't read."""
    role = sql.Identifier(DATESTYLE_READER)

    def drop(conn) -> None:
        if conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (DATESTYLE_READER,)
        ).fetchone():
            conn.execute(sql.SQL("DROP OWNED BY {}").format(role))
            conn.execute(sql.SQL("DROP ROLE {}").format(role))

    with postgres_demo.admin_connect() as conn:
        drop(conn)
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                role, sql.Literal(TZ_READER_PASSWORD)
            )
        )
        conn.execute(sql.SQL("ALTER ROLE {} SET DateStyle = 'German'").format(role))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE prumi_demo TO {}").format(role))
    monkeypatch.setenv(TZ_READER_PASSWORD_ENV, TZ_READER_PASSWORD)
    try:
        yield DATESTYLE_READER
    finally:
        with postgres_demo.admin_connect() as conn:
            drop(conn)


def test_a_role_with_another_datestyle_still_reads_timestamps(
    postgres_demo, tmp_path, german_datestyle_reader
):
    connection = PostgresConnection.from_config(
        postgres_demo.connection_config(
            user=german_datestyle_reader, password_env=TZ_READER_PASSWORD_ENV, timezone="UTC"
        ),
        tmp_path,
    )
    _, rows = connection.query("SELECT TIMESTAMPTZ '2026-10-14 23:30:00+00' AS at", LOCAL_VIEWER)
    assert rows == [(dt.datetime(2026, 10, 14, 23, 30, tzinfo=dt.UTC),)]
