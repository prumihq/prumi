import pytest
from prumi_adapter_dbt._metricflow import MetricFlowCompiler


@pytest.fixture(scope="session")
def compiler(demo_project) -> MetricFlowCompiler:
    return MetricFlowCompiler(demo_project.manifest_path, "duckdb")
