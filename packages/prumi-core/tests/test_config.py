"""Loading prumi.toml."""

import datetime as dt
from pathlib import Path

import pytest
from prumi.config import DEFAULT_SCRIPT_HOSTS, ConfigError, load_config

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
    assert server.data_dir == DEMO_CONFIG.parent / ".prumi"
    assert server.font_hosts[0] == "fonts.gstatic.com"
    [(name, source)] = config.sources.items()
    assert (name, source.name, source.adapter) == ("default", "default", "dbt")
    assert source.settings["connection"] == {"type": "duckdb", "path": "demo.duckdb"}
    assert source.config_dir == DEMO_CONFIG.parent


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
    assert dict(config.sources["default"].settings) == {}


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
        ("[server]\n", "[source] needs `adapter`"),
        (MINIMAL + "[source.dbtt]\nx = 1\n", "unknown key(s) in [source]: dbtt"),
        (MINIMAL + "[sources]\n", "unknown section(s) sources"),
        ("[server\n", "isn't valid TOML"),
    ],
)
def test_invalid_configs(tmp_path, text, expected):
    with pytest.raises(ConfigError, match=None) as error:
        load_config(write(tmp_path, text))
    assert expected in str(error.value)


def test_a_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="doesn't exist"):
        load_config(tmp_path / "prumi.toml")
