"""Settings from PRUMI__ environment variables, merged over prumi.toml."""

import datetime as dt
from pathlib import Path

import pytest
from prumi.config import ConfigError, load_config
from prumi.config_env import from_environment, merge

MINIMAL = """
[source]
adapter = "dbt"
"""

ADAPTER = {"PRUMI__SOURCE__ADAPTER": "dbt"}


def write(folder: Path, text: str) -> Path:
    path = folder / "prumi.toml"
    path.write_text(text)
    return path


@pytest.fixture
def empty_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no prumi.toml here
    return tmp_path


def test_a_variable_sets_a_nested_key(empty_cwd):
    env = {**ADAPTER, "PRUMI__SOURCE__DBT__SEMANTIC_MANIFEST": "m.json"}
    config = load_config(None, env)
    assert config.sources["default"].settings["semantic_manifest"] == "m.json"
    assert config.path is None
    assert config.source_label == "the environment"


def test_a_variable_name_maps_to_a_key_path():
    env = {"PRUMI__SOURCE__DBT__CONNECTION__HOST": "db.internal"}
    assert from_environment(env) == {
        ("source", "dbt", "connection", "host"): (
            "PRUMI__SOURCE__DBT__CONNECTION__HOST",
            "db.internal",
        )
    }


def test_merging_creates_sections_and_reports_where_each_key_came_from():
    table = {"source": {"adapter": "dbt"}}
    settings = {
        ("source", "dbt", "connection", "host"): (
            "PRUMI__SOURCE__DBT__CONNECTION__HOST",
            "db.internal",
        )
    }
    assert merge(table, settings) == {
        ("source", "dbt", "connection", "host"): "PRUMI__SOURCE__DBT__CONNECTION__HOST"
    }
    assert table == {"source": {"adapter": "dbt", "dbt": {"connection": {"host": "db.internal"}}}}


def test_the_environment_wins_over_the_file(tmp_path):
    path = write(tmp_path, "[server]\nport = 8000\n" + MINIMAL)
    config = load_config(path, {"PRUMI__SERVER__PORT": "9000"})
    assert config.server.port == 9000
    assert config.path == path
    assert config.source_label == str(path)


def test_an_empty_variable_counts_as_unset(tmp_path):
    path = write(tmp_path, "[server]\nport = 8000\n" + MINIMAL)
    assert load_config(path, {"PRUMI__SERVER__PORT": ""}).server.port == 8000


def test_other_prumi_variables_are_not_settings(tmp_path):
    env = {
        "PRUMI_CONTAINER": "0",
        "PRUMI_REGISTRY_PASSWORD": "secret",
        "PRUMI_TEST_POSTGRES_HOST": "localhost",
        "PRUMI_REQUIRE_POSTGRES": "1",
    }
    config = load_config(write(tmp_path, MINIMAL), env)
    assert config.sources["default"].adapter == "dbt"


@pytest.mark.parametrize("name", ["PRUMI__server__port", "PRUMI__SERVER__", "PRUMI____PORT"])
def test_a_malformed_variable_is_refused_by_name(tmp_path, name):
    with pytest.raises(ConfigError, match=name):
        load_config(write(tmp_path, MINIMAL), {name: "1"})


def test_an_unknown_section_from_a_variable_is_refused_by_name(tmp_path):
    with pytest.raises(ConfigError, match="PRUMI__NOPE__X"):
        load_config(write(tmp_path, MINIMAL), {"PRUMI__NOPE__X": "1"})


def test_a_variable_through_a_scalar_is_refused_by_name(tmp_path):
    with pytest.raises(ConfigError, match="PRUMI__SOURCE__ADAPTER__X"):
        load_config(write(tmp_path, MINIMAL), {"PRUMI__SOURCE__ADAPTER__X": "1"})


def test_a_variable_over_a_section_is_refused_by_name(tmp_path):
    path = write(tmp_path, MINIMAL + "[source.dbt]\nsemantic_manifest = 'm.json'\n")
    with pytest.raises(ConfigError, match="PRUMI__SOURCE__DBT"):
        load_config(path, {"PRUMI__SOURCE__DBT": "x"})


def test_without_a_file_or_variables_the_message_names_both(empty_cwd):
    with pytest.raises(ConfigError) as caught:
        load_config(None, {})
    message = str(caught.value)
    assert str(empty_cwd / "prumi.toml") in message
    assert "PRUMI__" in message


def test_a_default_file_is_read_when_it_exists(empty_cwd):
    path = write(empty_cwd, "[server]\nport = 8000\n" + MINIMAL)
    config = load_config(None, {})
    assert config.path == path
    assert config.server.port == 8000


def test_an_explicit_missing_file_is_still_refused(tmp_path):
    with pytest.raises(ConfigError, match="doesn't exist"):
        load_config(tmp_path / "x.toml", ADAPTER)


def test_relative_paths_from_variables_resolve_against_the_files_folder(tmp_path, monkeypatch):
    (tmp_path / "conf").mkdir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    path = write(tmp_path / "conf", MINIMAL)
    config = load_config(path, {"PRUMI__SERVER__DATA_DIR": "../data"})
    assert config.server.data_dir == tmp_path / "data"
    assert config.sources["default"].config_dir == tmp_path / "conf"


def test_relative_paths_without_a_file_resolve_against_the_current_folder(empty_cwd):
    config = load_config(None, {**ADAPTER, "PRUMI__SERVER__DATA_DIR": "data"})
    assert config.server.data_dir == empty_cwd / "data"
    assert config.sources["default"].config_dir == empty_cwd


def test_text_values_become_core_types(empty_cwd):
    env = {
        **ADAPTER,
        "PRUMI__SERVER__PORT": "9000",
        "PRUMI__SERVER__AS_OF": "2026-10-15",
        "PRUMI__SERVER__SCRIPT_HOSTS": "cdn.jsdelivr.net, unpkg.com",
        "PRUMI__SERVER__FONT_HOSTS": "fonts.gstatic.com,,",
    }
    server = load_config(None, env).server
    assert server.port == 9000 and server.as_of == dt.date(2026, 10, 15)
    assert server.script_hosts == ("cdn.jsdelivr.net", "unpkg.com")
    assert server.font_hosts == ("fonts.gstatic.com",)


def test_a_bad_value_from_a_variable_is_reported_by_its_name(empty_cwd):
    with pytest.raises(ConfigError) as caught:
        load_config(None, {**ADAPTER, "PRUMI__SERVER__PORT": "eighty"})
    assert str(caught.value).startswith("the environment: PRUMI__SERVER__PORT: ")


def test_a_variable_that_replaced_the_server_section_is_named(empty_cwd):
    with pytest.raises(ConfigError) as caught:
        load_config(None, {**ADAPTER, "PRUMI__SERVER": "x"})
    assert str(caught.value).startswith("the environment: PRUMI__SERVER: ")


def test_a_bad_value_from_the_file_is_still_reported_by_its_key(tmp_path):
    path = write(tmp_path, '[server]\nport = "eighty"\n' + MINIMAL)
    with pytest.raises(ConfigError) as caught:
        load_config(path, {"PRUMI__SERVER__AS_OF": "2026-10-15"})
    assert str(caught.value).startswith(f"{path}: server.port: ")


def test_a_non_loopback_host_from_a_variable_is_refused_outside_a_container(empty_cwd, monkeypatch):
    monkeypatch.delenv("PRUMI_CONTAINER", raising=False)
    with pytest.raises(ConfigError) as caught:
        load_config(None, {**ADAPTER, "PRUMI__SERVER__HOST": "0.0.0.0"})
    message = str(caught.value)
    assert "PRUMI__SERVER__HOST: " in message and "isn't a loopback address" in message


def test_a_missing_adapter_names_the_variable_that_replaced_the_source(empty_cwd):
    with pytest.raises(ConfigError, match=r"\[source\] \(set by PRUMI__SOURCE\) needs `adapter`"):
        load_config(None, {"PRUMI__SOURCE": "dbt"})


def test_an_unknown_source_key_from_a_variable_is_refused_by_name(tmp_path):
    with pytest.raises(
        ConfigError, match=r"unknown key\(s\) in \[source\]: dbtt.*PRUMI__SOURCE__DBTT__X"
    ):
        load_config(write(tmp_path, MINIMAL), {"PRUMI__SOURCE__DBTT__X": "1"})


def test_adapter_settings_over_a_value_name_the_variable(empty_cwd):
    with pytest.raises(ConfigError, match=r"\[source.dbt\] must be a table.*PRUMI__SOURCE__DBT"):
        load_config(None, {**ADAPTER, "PRUMI__SOURCE__DBT": "m.json"})


SECRET = "s3cret-value"


@pytest.mark.parametrize(
    "name",
    [
        "PRUMI__server__password",  # a malformed name
        "PRUMI__SOURCE__ADAPTER__PASSWORD",  # through a scalar
        "PRUMI__SERVER__PORT",
        "PRUMI__SERVER__AS_OF",
        "PRUMI__SERVER__HOST",
        "PRUMI__SERVER__LOCALE",
        "PRUMI__SERVER__PASSWORD",  # an unknown key
        "PRUMI__SERVER",  # over a whole section
    ],
)
def test_a_refused_variable_is_named_without_its_value(empty_cwd, monkeypatch, name):
    monkeypatch.delenv("PRUMI_CONTAINER", raising=False)
    with pytest.raises(ConfigError) as caught:
        load_config(None, {**ADAPTER, name: SECRET})
    message = str(caught.value)
    assert name in message
    assert SECRET not in message
