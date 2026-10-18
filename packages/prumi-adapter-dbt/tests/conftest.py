import tomllib

import pytest
from prumi.adapters import load_adapter_factory
from prumi_adapter_dbt._metricflow import MetricFlowCompiler


@pytest.fixture(scope="session")
def compiler(demo_project) -> MetricFlowCompiler:
    return MetricFlowCompiler(demo_project.manifest_path, "duckdb")


@pytest.fixture(scope="session")
def dbt_config(demo_project) -> dict:
    """The `[source.dbt]` table of the demo's prumi.toml."""
    with demo_project.config_path.open("rb") as f:
        return tomllib.load(f)["source"]["dbt"]


@pytest.fixture(scope="session")
def adapter(demo_project, dbt_config):
    """The dbt adapter, loaded through its entry point like the server does."""
    return load_adapter_factory("dbt")(dbt_config, demo_project.root)
