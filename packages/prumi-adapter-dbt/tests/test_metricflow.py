"""Loading the manifest and compiling with MetricFlow, which never runs anything."""

import pytest
from prumi_adapter_dbt._metricflow import (
    CompileOnlySqlClient,
    CompileRequest,
    ManifestError,
    MetricFlowCompiler,
    UnsupportedDialectError,
)


def test_the_demo_manifest_loads_and_a_query_compiles_without_client_calls(compiler):
    sql = compiler.compile(CompileRequest(metrics=("revenue",), group_by=("metric_time__month",)))
    assert '"demo"."main"."order_items"' in sql
    assert "metric_time__month" in sql
    assert compiler.client.calls == []


@pytest.mark.parametrize("method", ["query", "execute", "dry_run"])
def test_the_client_refuses_to_run_sql(method):
    client = CompileOnlySqlClient("duckdb")
    with pytest.raises(RuntimeError, match="only compiles"):
        getattr(client, method)("SELECT 1")
    assert client.calls == [method]


def test_an_unknown_connection_type_has_no_dialect():
    with pytest.raises(UnsupportedDialectError, match="'oracle'.*duckdb"):
        CompileOnlySqlClient("oracle")


def test_a_missing_manifest_names_the_file(tmp_path):
    path = tmp_path / "target" / "semantic_manifest.json"
    with pytest.raises(ManifestError, match=f"{path}.*doesn't exist.*dbt parse"):
        MetricFlowCompiler(path, "duckdb")


@pytest.mark.parametrize("content", ["not json", '{"semantic_models": 3}'])
def test_an_unreadable_manifest_names_the_file(tmp_path, content):
    path = tmp_path / "semantic_manifest.json"
    path.write_text(content)
    with pytest.raises(ManifestError, match=f"can't read the semantic manifest {path}"):
        MetricFlowCompiler(path, "duckdb")
