"""Release: versions are 0.1.0 and `uv build` makes the `prumi` and `prumi-adapter-dbt` wheels."""

import json
import os
import re
import shutil
import subprocess
import tomllib
import zipfile
from importlib.metadata import version
from pathlib import Path

import prumi
import pytest
import yaml

PACKAGES = Path(__file__).parents[2]
VERSION = "0.1.0"


def test_versions():
    for folder in ("prumi-core", "prumi-adapter-dbt"):
        with (PACKAGES / folder / "pyproject.toml").open("rb") as f:
            assert tomllib.load(f)["project"]["version"] == VERSION, folder
    assert prumi.__version__ == VERSION
    assert version("prumi") == version("prumi-adapter-dbt") == VERSION
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
    for package in ("prumi", "prumi-adapter-dbt"):
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
        "prumi-0.1.0-py3-none-any.whl",
        "prumi-0.1.0.tar.gz",
        "prumi_adapter_dbt-0.1.0-py3-none-any.whl",
        "prumi_adapter_dbt-0.1.0.tar.gz",
    ]


def test_the_core_wheel_carries_the_runtime(wheels):
    with zipfile.ZipFile(wheels / "prumi-0.1.0-py3-none-any.whl") as wheel:
        names = set(wheel.namelist())
        for asset in (
            "runtime.js",
            "shell.js",
            "provenance.js",
            "theme.css",
            "home.css",
            "home.js",
        ):
            bundled = wheel.read(f"prumi/_runtime/{asset}")
            assert bundled == (PACKAGES / "prumi-runtime" / "src" / asset).read_bytes()
        assert "prumi/registry/migrations/0001_initial.sql" in names
        assert "prumi/spec/dashboard-spec.v0.schema.json" in names
        assert "prumi/mcp/authoring_guide.md" in names
        assert "prumi/validate/layout.js" in names
        entry_points = wheel.read("prumi-0.1.0.dist-info/entry_points.txt").decode()
        assert "prumi = prumi.cli:main" in entry_points


def test_the_prumi_wheel_offers_the_dbt_adapter_as_an_extra(wheels):
    with zipfile.ZipFile(wheels / "prumi-0.1.0-py3-none-any.whl") as wheel:
        metadata = wheel.read("prumi-0.1.0.dist-info/METADATA").decode()
    assert "Name: prumi\n" in metadata
    assert "Provides-Extra: dbt" in metadata
    assert "Requires-Dist: prumi-adapter-dbt[duckdb]<0.2,>=0.1.0; extra == 'dbt'" in metadata


@pytest.mark.parametrize("wheel_name", ["prumi-0.1.0", "prumi_adapter_dbt-0.1.0"])
def test_the_wheels_carry_what_the_pypi_page_shows(wheels, wheel_name):
    with zipfile.ZipFile(wheels / f"{wheel_name}-py3-none-any.whl") as wheel:
        metadata = wheel.read(f"{wheel_name}.dist-info/METADATA").decode()
    headers = metadata.split("\n\n", 1)[0].splitlines()
    assert "Author: Denner Rodrigues Campelo" in headers
    assert not [h for h in headers if h.startswith("Author-email:")]
    assert "Project-URL: Repository, https://github.com/prumihq/prumi" in headers
    assert "Project-URL: Issues, https://github.com/prumihq/prumi/issues" in headers
    assert (
        "Project-URL: Changelog, https://github.com/prumihq/prumi/blob/main/CHANGELOG.md" in headers
    )
    assert "License-Expression: Apache-2.0" in headers
    classifiers = [h.removeprefix("Classifier: ") for h in headers if h.startswith("Classifier:")]
    assert "Development Status :: 3 - Alpha" in classifiers
    assert "Programming Language :: Python :: 3.12" in classifiers
    assert [c for c in classifiers if c.startswith("Topic ::")]
    assert not [c for c in classifiers if c.startswith("License ::")]
    assert [h for h in headers if h.startswith("Keywords: ")]
    assert "Description-Content-Type: text/markdown" in headers


# Markdown links and images, reference definitions, and HTML href/src attributes.
LINK = re.compile(
    r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)|^\s*\[[^\]]+\]:\s*<?(\S+?)>?\s*$|(?:href|src)=[\"']([^\"']+)",
    re.M,
)
FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
CODE_SPAN = re.compile(r"(`+).+?\1", re.S)


def test_package_readmes_link_only_to_absolute_urls():
    # Each README is its package's page on PyPI, which can't follow a relative link.
    links = {readme: _links(readme.read_text()) for readme in PACKAGES.glob("*/README.md")}
    assert len(links) == 3 and sum(map(len, links.values())) > 5
    for readme, targets in links.items():
        relative = [t for t in targets if not re.match(r"(https?|mailto):", t)]
        assert not relative, f"{readme.relative_to(PACKAGES.parent)} has relative links: {relative}"


def test_the_link_check_finds_relative_links():
    markdown = (
        '[a](../x.md) and ![i](img.png), <a href="#top">t</a> but not `[c](code.md)`\n'
        "[ref]: docs/y.md\n```\n[d](block.md)\n```\n[e](https://example.com/z)\n"
    )
    assert _links(markdown) == ["../x.md", "img.png", "#top", "docs/y.md", "https://example.com/z"]


def _links(markdown: str) -> list[str]:
    text = CODE_SPAN.sub("", FENCE.sub("", markdown))
    return [next(g for g in m.groups() if g) for m in LINK.finditer(text)]


def test_the_adapter_wheel_registers_itself(wheels):
    with zipfile.ZipFile(wheels / "prumi_adapter_dbt-0.1.0-py3-none-any.whl") as wheel:
        entry_points = wheel.read("prumi_adapter_dbt-0.1.0.dist-info/entry_points.txt").decode()
        metadata = wheel.read("prumi_adapter_dbt-0.1.0.dist-info/METADATA").decode()
    assert "[prumi.adapters]\ndbt = prumi_adapter_dbt.adapter:create_adapter" in entry_points
    assert "Requires-Dist: metricflow==0.213.0" in metadata
    assert "Requires-Dist: prumi<0.2,>=0.1.0" in metadata
    assert "Requires-Dist: duckdb; extra == 'duckdb'" in metadata


def test_ci_runs_the_install_check_in_a_job_of_its_own():
    ci = yaml.safe_load((PACKAGES.parent / ".github" / "workflows" / "ci.yml").read_text())
    runs = {name: [s.get("run", "") for s in job["steps"]] for name, job in ci["jobs"].items()}
    jobs = [
        n for n, commands in runs.items() if any("scripts/check_install.py" in c for c in commands)
    ]
    assert jobs == ["install-check"]
    assert not [c for c in runs["install-check"] if "pytest" in c]
