"""Shared pytest fixtures for the workspace.

`demo_project` builds the demo dbt project once per session when its outputs are missing or
older than the generator and models, and reuses them otherwise. Every test that uses it is
marked `demo`, so `pytest -m "not demo"` skips them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).parent
DEMO_DIR = REPO / "examples" / "demo-dbt-project"
DEMO_OUTPUTS = [DEMO_DIR / "demo.duckdb", DEMO_DIR / "target" / "semantic_manifest.json"]


@dataclass(frozen=True)
class DemoProject:
    root: Path
    db_path: Path
    manifest_path: Path
    config_path: Path


def _demo_inputs() -> list[Path]:
    inputs = [DEMO_DIR / name for name in ("generate.py", "build.py", "dbt_project.yml")]
    inputs += [DEMO_DIR / "profiles.yml", DEMO_DIR / "pyproject.toml"]
    return inputs + sorted((DEMO_DIR / "models").rglob("*"))


def demo_is_stale() -> bool:
    if not all(path.is_file() for path in DEMO_OUTPUTS):
        return True
    oldest_output = min(path.stat().st_mtime for path in DEMO_OUTPUTS)
    newest_input = max(path.stat().st_mtime for path in _demo_inputs())
    return newest_input > oldest_output


def build_demo() -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("Building the demo dbt project needs `uv` on PATH.")
    # The demo has its own environment; don't let the workspace's venv leak into it.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    command = [uv, "run", "--project", str(DEMO_DIR), "python", str(DEMO_DIR / "build.py")]
    result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(
            f"Building the demo dbt project failed ({' '.join(command)}):\n"
            f"{result.stdout}\n{result.stderr}"
        )


@pytest.fixture(scope="session")
def demo_project() -> DemoProject:
    if demo_is_stale():
        build_demo()
    return DemoProject(
        root=DEMO_DIR,
        db_path=DEMO_OUTPUTS[0],
        manifest_path=DEMO_OUTPUTS[1],
        config_path=DEMO_DIR / "prumi.toml",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "demo_project" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.demo)
