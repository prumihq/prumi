"""Loading prumi.toml."""

import datetime as dt
from pathlib import Path

import pytest
from prumi.config import (
    DEFAULT_SCRIPT_HOSTS,
    ConfigError,
    RegistryConfig,
    RegistryPostgresConfig,
    load_config,
)

DEMO_CONFIG = Path(__file__).parents[3] / "examples" / "demo-dbt-project" / "prumi.toml"

MINIMAL = """
[source]
adapter = "dbt"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "prumi.toml"
    path.write_text(text)
    return path


def test_the_demo_config():
    config = load_config(DEMO_CONFIG)
    server = config.server
    assert (server.host, server.port) == ("127.0.0.1", 8484)
    assert server.as_of == dt.date(2026, 10, 15)
    assert server.locale == "en-US"
    assert server.data_dir == DEMO_CONFIG.parent / ".prumi"
    assert server.font_hosts[0] == "fonts.gstatic.com"
    [(name, source)] = config.sources.items()
    assert (name, source.name, source.adapter) == ("default", "default", "dbt")
    assert source.settings["connection"] == {"type": "duckdb", "path": "demo.duckdb"}
    assert source.config_dir == DEMO_CONFIG.parent
    assert source.project == "prumi_demo"


def test_defaults_and_relative_paths(tmp_path):
    (tmp_path / "conf").mkdir()
    config = load_config(write(tmp_path / "conf", MINIMAL + '[server]\ndata_dir = "../data"\n'))
    assert config.server.data_dir == tmp_path / "data"
    assert (config.server.host, config.server.port, config.server.as_of) == (
        "127.0.0.1",
        8484,
        None,
    )
    assert config.server.script_hosts == DEFAULT_SCRIPT_HOSTS
    assert config.server.locale == "en-US"
    assert dict(config.sources["default"].settings) == {}
    assert config.sources["default"].project is None


@pytest.mark.parametrize("as_of", ['"2026-10-15"', "2026-10-15"])
def test_as_of_as_a_string_or_a_toml_date(tmp_path, as_of):
    config = load_config(write(tmp_path, f"[server]\nas_of = {as_of}\n" + MINIMAL))
    assert config.server.as_of == dt.date(2026, 10, 15)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.0.0.2"])
def test_loopback_hosts(tmp_path, host):
    assert (
        load_config(write(tmp_path, f'[server]\nhost = "{host}"\n' + MINIMAL)).server.host == host
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com", ""])
def test_a_non_loopback_host_fails_with_a_clear_message(tmp_path, host):
    with pytest.raises(ConfigError) as error:
        load_config(write(tmp_path, f'[server]\nhost = "{host}"\n' + MINIMAL))
    message = str(error.value)
    assert str(tmp_path / "prumi.toml") in message
    assert "server.host" in message and "isn't a loopback address" in message
    assert "no login" in message


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[server]\nport = 70000\n" + MINIMAL, "server.port"),
        ("[server]\nprot = 8484\n" + MINIMAL, "server.prot: unknown key"),
        ('[server]\nas_of = "soon"\n' + MINIMAL, "server.as_of"),
        ('[server]\nlocale = "en_US"\n' + MINIMAL, "server.locale: 'en_US' isn't a locale tag"),
        ('[server]\nlocale = ""\n' + MINIMAL, "server.locale"),
        ("[server]\n", "[source] needs `adapter`"),
        (MINIMAL + "[source.dbtt]\nx = 1\n", "unknown key(s) in [source]: dbtt"),
        (MINIMAL + "[sources]\n", "unknown section(s) sources"),
        (MINIMAL + "project = 1\n", "`project` must be a string"),
        ("[server\n", "isn't valid TOML"),
    ],
)
def test_invalid_configs(tmp_path, text, expected):
    with pytest.raises(ConfigError, match=None) as error:
        load_config(write(tmp_path, text))
    assert expected in str(error.value)


def test_the_project_name_is_optional_and_stays_out_of_the_adapters_settings(tmp_path):
    config = load_config(write(tmp_path, MINIMAL + 'project = "jaffle_shop"\n'))
    source = config.sources["default"]
    assert source.project == "jaffle_shop" and dict(source.settings) == {}


@pytest.mark.parametrize("locale", ["pt-BR", "de-DE", "en", "zh-Hant-TW"])
def test_locale_tags(tmp_path, locale):
    config = load_config(write(tmp_path, f'[server]\nlocale = "{locale}"\n' + MINIMAL))
    assert config.server.locale == locale


def test_a_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="doesn't exist"):
        load_config(tmp_path / "prumi.toml")


PG = '[registry]\nbackend = "postgres"\n[registry.postgres]\n'


def test_without_a_registry_section_it_is_sqlite(tmp_path):
    assert load_config(write(tmp_path, MINIMAL)).registry == RegistryConfig("sqlite", None)


def test_a_postgres_registry(tmp_path):
    text = MINIMAL + PG + 'host = "db"\ndbname = "prumi"\nuser = "prumi"\n'
    r = load_config(write(tmp_path, text)).registry
    assert r.backend == "postgres" and r.postgres == RegistryPostgresConfig(
        "db", 5432, "prumi", "prumi", None
    )


def test_postgres_settings_come_from_variables_too(tmp_path):
    env = {
        "PRUMI__SOURCE__ADAPTER": "dbt",
        "PRUMI__REGISTRY__BACKEND": "postgres",
        "PRUMI__REGISTRY__POSTGRES__HOST": "db",
        "PRUMI__REGISTRY__POSTGRES__DBNAME": "prumi",
        "PRUMI__REGISTRY__POSTGRES__USER": "prumi",
        "PRUMI__REGISTRY__POSTGRES__PORT": "6543",
        "PRUMI__REGISTRY__POSTGRES__SSLMODE": "require",
    }
    r = load_config(write(tmp_path, MINIMAL), env).registry
    assert r.postgres == RegistryPostgresConfig("db", 6543, "prumi", "prumi", "require")


def test_a_bad_value_from_a_variable_is_named_not_echoed(tmp_path):
    env = {
        "PRUMI__REGISTRY__BACKEND": "postgres",
        "PRUMI__REGISTRY__POSTGRES__HOST": "postgres://u:hunter2@h/db",
        "PRUMI__REGISTRY__POSTGRES__DBNAME": "p",
        "PRUMI__REGISTRY__POSTGRES__USER": "p",
    }
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, MINIMAL), env)
    assert "PRUMI__REGISTRY__POSTGRES__HOST" in str(caught.value)
    assert "hunter2" not in str(caught.value)


@pytest.mark.parametrize("port", ['"6543x"', "0", "70000", "true", '"123456"', "5432.0"])
def test_a_bad_registry_port_is_refused(tmp_path, port):
    text = MINIMAL + PG + f'host = "d"\ndbname = "p"\nuser = "p"\nport = {port}\n'
    with pytest.raises(ConfigError, match="port"):
        load_config(write(tmp_path, text))


@pytest.mark.parametrize(
    "text, words",
    [
        ('[registry]\nbackend = "postgres"\n', "[registry.postgres]"),
        ('[registry]\nbackend = "sqlite"\n[registry.postgres]\nhost = "x"\n', "backend"),
        ('[registry]\nbackend = "mysql"\n', "backend"),
        (PG + 'host="d"\ndbname="p"\nuser="p"\npassword="x"\n', "PRUMI_REGISTRY_PASSWORD"),
        (PG + 'host="d"\ndbname="p"\nuser="p"\nschema="x"\n', "schema"),
        (PG + 'host="d"\ndbname="p"\nuser="p"\nsslmode="maybe"\n', "sslmode"),
        (PG + 'host="d=1"\ndbname="p"\nuser="p"\n', "connection string"),
        (PG + 'host="d"\ndbname="p"\n', "user"),
    ],
)
def test_bad_registry_settings_are_refused(tmp_path, text, words):
    with pytest.raises(ConfigError, match=words.replace("[", r"\[").replace("]", r"\]")):
        load_config(write(tmp_path, MINIMAL + text))


def test_the_registry_password_variable_is_ignored_with_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("PRUMI_REGISTRY_PASSWORD", "secret")
    assert load_config(write(tmp_path, MINIMAL)).registry == RegistryConfig("sqlite", None)
