"""Startup fails with a message naming the problem."""

import shutil

import duckdb
import pytest
from prumi.adapters import AdapterStartupError
from prumi_adapter_dbt.adapter import DbtAdapter


@pytest.fixture
def config(demo_project):
    """The demo's [source.dbt] table with absolute paths, so tests can move one thing."""
    return {
        "semantic_manifest": str(demo_project.manifest_path),
        "connection": {"type": "duckdb", "path": str(demo_project.db_path)},
    }


def start(config, config_dir):
    with pytest.raises(AdapterStartupError) as error:
        DbtAdapter.from_config(config, config_dir)
    return str(error.value)


def test_relative_paths_resolve_against_the_config_folder(demo_project):
    config = {
        "semantic_manifest": "target/semantic_manifest.json",
        "connection": {"type": "duckdb", "path": "demo.duckdb"},
    }
    assert DbtAdapter.from_config(config, demo_project.root).name == "dbt"


def test_missing_manifest(config, tmp_path):
    config["semantic_manifest"] = "target/semantic_manifest.json"
    message = start(config, tmp_path)
    assert str(tmp_path / "target" / "semantic_manifest.json") in message
    assert "dbt parse" in message


@pytest.mark.parametrize("content", ['{"semantic_models": "nope"}', '{"x": ' + "1" * 5000])
def test_manifest_metricflow_cannot_parse(config, tmp_path, content):
    (tmp_path / "semantic_manifest.json").write_text(content)
    config["semantic_manifest"] = "semantic_manifest.json"
    message = start(config, tmp_path)
    assert f"can't read the semantic manifest {tmp_path / 'semantic_manifest.json'}" in message
    assert len(message) < 1000  # MetricFlow's error can quote the whole file


def test_unsupported_connection_type(config, tmp_path):
    config["connection"] = {"type": "oracle", "host": "db"}
    message = start(config, tmp_path)
    assert "'oracle'" in message and "duckdb" in message


def test_renamed_duckdb_file(config, demo_project, tmp_path):
    shutil.copy(demo_project.db_path, tmp_path / "warehouse.duckdb")
    config["connection"]["path"] = "warehouse.duckdb"
    message = start(config, tmp_path)
    assert '"demo"."main"."orders" (semantic model orders)' in message
    assert str(tmp_path / "warehouse.duckdb") in message
    assert "file name without its extension" in message


def test_duckdb_file_without_the_tables(config, tmp_path):
    duckdb.connect(str(tmp_path / "demo.duckdb")).close()  # right name, empty
    config["connection"]["path"] = "demo.duckdb"
    message = start(config, tmp_path)
    assert '"demo"."main"."customers" (semantic model customers)' in message
    assert '"demo"."main"."inventory_snapshots"' in message


def test_missing_duckdb_file(config, tmp_path):
    config["connection"]["path"] = "nowhere.duckdb"
    message = start(config, tmp_path)
    assert str(tmp_path / "nowhere.duckdb") in message and "doesn't exist" in message


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda c: c.pop("semantic_manifest"), "semantic_manifest"),
        (lambda c: c.pop("connection"), "[source.dbt.connection] needs `type`"),
        (lambda c: c["connection"].pop("path"), "needs `path`"),
    ],
)
def test_missing_settings(config, tmp_path, change, expected):
    change(config)
    assert expected in start(config, tmp_path)
