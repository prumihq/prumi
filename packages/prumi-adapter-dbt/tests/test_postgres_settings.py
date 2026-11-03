import psycopg
import pytest
from prumi.adapters import AdapterStartupError
from prumi_adapter_dbt.connections.postgres import PostgresSettings, connect_error_message

BASE = {"type": "postgres", "host": "db.internal", "dbname": "analytics", "user": "prumi_reader"}


def test_defaults():
    s = PostgresSettings.from_config(BASE)
    assert (s.port, s.sslmode, s.timezone) == (5432, None, None)
    kwargs = s.connect_kwargs()
    assert kwargs["connect_timeout"] == 10 and "password" not in kwargs
    assert kwargs["host"] == "db.internal" and kwargs["dbname"] == "analytics"


def test_password_key_is_refused_without_its_value():
    with pytest.raises(AdapterStartupError) as e:
        PostgresSettings.from_config({**BASE, "password": "SENTINEL-7f3a9c"})
    assert "PGPASSWORD" in str(e.value) and "pgpass" in str(e.value)
    assert "SENTINEL" not in str(e.value) and e.value.__cause__ is None


@pytest.mark.parametrize("missing", ["host", "dbname", "user"])
def test_required_settings(missing):
    config = {k: v for k, v in BASE.items() if k != missing}
    with pytest.raises(AdapterStartupError, match=f"`{missing}`"):
        PostgresSettings.from_config(config)


@pytest.mark.parametrize(
    "change",
    [
        {"port": "5432"},
        {"port": True},
        {"sslmode": "always"},
        {"sslmode": 1},
        {"timezone": 3},
        {"timezone": "UTC\t-c default_transaction_read_only=off"},
        {"timezone": "UTC\n-c default_transaction_read_only=off"},
        {"timezone": "UTC\x00"},
        {"host": ""},
        {"schema": "SENTINEL-schema-9d2"},
    ],
)
def test_bad_settings_are_refused_by_name(change):
    (key,) = change
    value = change[key]
    with pytest.raises(AdapterStartupError) as e:
        PostgresSettings.from_config({**BASE, **change})
    assert key in str(e.value)
    if isinstance(value, str) and value:
        assert value not in str(e.value)


@pytest.mark.parametrize(
    "change",
    [
        {"host": "postgresql://postgres:SENTINEL-pw@localhost/postgres"},
        {"host": "postgres:SENTINEL-pw@localhost"},
        {"host": "localhost password=SENTINEL-pw"},
        {"dbname": "host=localhost password=SENTINEL-pw"},
        {"dbname": "postgres://u:SENTINEL-pw@h/analytics"},
        {"dbname": "analytics\tSENTINEL-pw"},
    ],
)
def test_a_connection_string_in_host_or_dbname_is_refused_unechoed(change):
    (key,) = change
    with pytest.raises(AdapterStartupError) as e:
        PostgresSettings.from_config({**BASE, **change})
    message = str(e.value)
    assert f"`{key}`" in message and "connection string" in message
    assert "PGPASSWORD" in message
    assert "SENTINEL" not in message and e.value.__cause__ is None


def test_a_user_with_an_at_sign_is_accepted():
    # Some hosted servers want logins like user@server.
    assert PostgresSettings.from_config({**BASE, "user": "reader@server"}).user == "reader@server"


@pytest.mark.parametrize(
    "mode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
)
def test_every_sslmode_is_accepted(mode):
    s = PostgresSettings.from_config({**BASE, "sslmode": mode})
    assert s.connect_kwargs()["sslmode"] == mode


def test_sessions_are_read_only():
    options = PostgresSettings.from_config(BASE).connect_kwargs()["options"]
    assert "default_transaction_read_only=on" in options
    assert "TimeZone" not in options


def test_sessions_print_dates_in_iso_format():
    # psycopg reads timestamps only in ISO format, whatever DateStyle the role or database has.
    options = PostgresSettings.from_config(BASE).connect_kwargs()["options"]
    assert "-c DateStyle=ISO" in options


@pytest.mark.parametrize(
    "text",
    [
        'connection failed: FATAL:  no pg_hba.conf entry for host "10.0.0.7", '
        'user "prumi_reader", database "analytics", no encryption',
        'connection failed: FATAL:  pg_hba.conf rejects connection for host "10.0.0.7", '
        'user "prumi_reader", database "analytics", no encryption',
    ],
)
def test_a_pg_hba_rejection_gets_the_general_advice_not_login_advice(text):
    settings = PostgresSettings.from_config(BASE)
    exc = psycopg.OperationalError(text)
    message = connect_error_message(exc, settings)
    assert "`sslmode`" in message and "accepts connections from this machine" in message
    assert "refused" not in message and "10.0.0.7" not in message


def test_timezone_goes_into_the_session_options_escaped():
    s = PostgresSettings.from_config({**BASE, "timezone": "America/Sao_Paulo"})
    assert "-c TimeZone=America/Sao_Paulo" in s.connect_kwargs()["options"]
    odd = PostgresSettings.from_config({**BASE, "timezone": "A B\\C"})
    assert r"-c TimeZone=A\ B\\C" in odd.connect_kwargs()["options"]
