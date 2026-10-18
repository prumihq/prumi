"""Release: versions are 0.1.0 and `uv build` makes wheels with the runtime in them."""

import json
import os
import shutil
import subprocess
import tomllib
import zipfile
from importlib.metadata import version
from pathlib import Path

import prumi
import pytest

PACKAGES = Path(__file__).parents[2]
VERSION = "0.1.0"


def test_versions():
    for package in ("prumi-core", "prumi-adapter-dbt"):
        with (PACKAGES / package / "pyproject.toml").open("rb") as f:
            assert tomllib.load(f)["project"]["version"] == VERSION, package
    assert prumi.__version__ == VERSION
    assert version("prumi-core") == version("prumi-adapter-dbt") == VERSION
    adapter_init = PACKAGES / "prumi-adapter-dbt" / "src" / "prumi_adapter_dbt" / "__init__.py"
    assert f'__version__ = "{VERSION}"' in adapter_init.read_text()
    runtime = json.loads((PACKAGES / "prumi-runtime" / "package.json").read_text())
    assert runtime["version"] == VERSION


@pytest.fixture(scope="module")
def wheels(tmp_path_factory):
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("Building the wheels needs `uv` on PATH.")
    out = tmp_path_factory.mktemp("dist")
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    for package in ("prumi-core", "prumi-adapter-dbt"):
        # Like a release: the sdist first, then the wheel built from it.
        subprocess.run(
            [uv, "build", "--package", package, "--out-dir", str(out), "--quiet"],
            cwd=PACKAGES.parent,
            env=env,
            check=True,
        )
    return out


def test_uv_build_makes_both_wheels_and_sdists(wheels):
    assert sorted(p.name for p in wheels.iterdir() if p.suffix in (".whl", ".gz")) == [
        "prumi_adapter_dbt-0.1.0-py3-none-any.whl",
        "prumi_adapter_dbt-0.1.0.tar.gz",
        "prumi_core-0.1.0-py3-none-any.whl",
        "prumi_core-0.1.0.tar.gz",
    ]


def test_the_core_wheel_carries_the_runtime(wheels):
    with zipfile.ZipFile(wheels / "prumi_core-0.1.0-py3-none-any.whl") as wheel:
        names = set(wheel.namelist())
        for asset in ("runtime.js", "shell.js", "provenance.js", "theme.css", "home.css"):
            bundled = wheel.read(f"prumi/_runtime/{asset}")
            assert bundled == (PACKAGES / "prumi-runtime" / "src" / asset).read_bytes()
        assert "prumi/registry/migrations/0001_initial.sql" in names
        assert "prumi/spec/dashboard-spec.v0.schema.json" in names
        assert "prumi/mcp/authoring_guide.md" in names
        assert "prumi/validate/layout.js" in names
        entry_points = wheel.read("prumi_core-0.1.0.dist-info/entry_points.txt").decode()
        assert "prumi = prumi.cli:main" in entry_points


def test_the_adapter_wheel_registers_itself(wheels):
    with zipfile.ZipFile(wheels / "prumi_adapter_dbt-0.1.0-py3-none-any.whl") as wheel:
        entry_points = wheel.read("prumi_adapter_dbt-0.1.0.dist-info/entry_points.txt").decode()
        metadata = wheel.read("prumi_adapter_dbt-0.1.0.dist-info/METADATA").decode()
    assert "[prumi.adapters]\ndbt = prumi_adapter_dbt.adapter:create_adapter" in entry_points
    assert "Requires-Dist: metricflow==0.213.0" in metadata
    assert "Requires-Dist: prumi-core<0.2,>=0.1.0" in metadata
    assert "Requires-Dist: duckdb; extra == 'duckdb'" in metadata
