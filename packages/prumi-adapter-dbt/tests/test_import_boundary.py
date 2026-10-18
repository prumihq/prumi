"""Only `_metricflow.py` imports MetricFlow (spec section 4, AC 2)."""

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "prumi_adapter_dbt"
ALLOWED = SRC / "_metricflow.py"


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def test_only_the_metricflow_module_imports_metricflow():
    # The prefix also covers metricflow_semantics and metricflow_semantic_interfaces.
    importers = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        for module in imported_modules(path)
        if module.split(".")[0].startswith("metricflow")
    }
    assert importers == {ALLOWED.relative_to(SRC).as_posix()}
