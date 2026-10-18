"""Core never imports an adapter package or MetricFlow (AGENTS.md)."""

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "prumi"
FORBIDDEN = ("prumi_adapter", "metricflow", "metricflow_semantics")


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def is_forbidden(module: str) -> bool:
    return module.split(".")[0].startswith(FORBIDDEN)


def test_core_source_imports_no_adapter():
    offenders = {
        f"{path.relative_to(SRC.parent)}: {module}"
        for path in SRC.rglob("*.py")
        for module in imported_modules(path)
        if is_forbidden(module)
    }
    assert not offenders


def test_importing_all_of_core_loads_no_adapter():
    # Catches dynamic imports too: import every module of `prumi` in a fresh interpreter.
    code = (
        "import pkgutil, sys, prumi\n"
        "for m in pkgutil.walk_packages(prumi.__path__, 'prumi.'): __import__(m.name)\n"
        f"print([m for m in sys.modules if m.split('.')[0].startswith({FORBIDDEN!r})])\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
