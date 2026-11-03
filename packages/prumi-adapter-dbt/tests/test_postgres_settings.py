import pytest
from prumi.adapters import AdapterStartupError
from prumi_adapter_dbt.connections.postgres import PostgresSettings

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
        {"host": ""},
        {"schema": "x"},
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
    "mode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]
)
def test_every_sslmode_is_accepted(mode):
    s = PostgresSettings.from_config({**BASE, "sslmode": mode})
    assert s.connect_kwargs()["sslmode"] == mode


def test_sessions_are_read_only():
    options = PostgresSettings.from_config(BASE).connect_kwargs()["options"]
    assert "default_transaction_read_only=on" in options
    assert "TimeZone" not in options


def test_timezone_goes_into_the_session_options_escaped():
    s = PostgresSettings.from_config({**BASE, "timezone": "America/Sao_Paulo"})
    assert "-c TimeZone=America/Sao_Paulo" in s.connect_kwargs()["options"]
    odd = PostgresSettings.from_config({**BASE, "timezone": "A B\\C"})
    assert r"-c TimeZone=A\ B\\C" in odd.connect_kwargs()["options"]
