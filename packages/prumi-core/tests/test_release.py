"""Release: one version everywhere, the builds of both packages, the release workflow."""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from importlib.metadata import version
from pathlib import Path

import prumi
import pytest
import yaml

PACKAGES = Path(__file__).parents[2]
VERSION = "0.1.0"
# Wheel and sdist names without their suffix, and the wheels' .dist-info folders.
CORE = f"prumi-{VERSION}"
ADAPTER = f"prumi_adapter_dbt-{VERSION}"


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
        f"{CORE}-py3-none-any.whl",
        f"{CORE}.tar.gz",
        f"{ADAPTER}-py3-none-any.whl",
        f"{ADAPTER}.tar.gz",
    ]


def test_the_core_wheel_carries_the_runtime(wheels):
    with zipfile.ZipFile(wheels / f"{CORE}-py3-none-any.whl") as wheel:
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
        entry_points = wheel.read(f"{CORE}.dist-info/entry_points.txt").decode()
        assert "prumi = prumi.cli:main" in entry_points


def test_the_prumi_wheel_offers_the_dbt_adapter_as_an_extra(wheels):
    with zipfile.ZipFile(wheels / f"{CORE}-py3-none-any.whl") as wheel:
        metadata = wheel.read(f"{CORE}.dist-info/METADATA").decode()
    assert "Name: prumi\n" in metadata
    assert "Provides-Extra: dbt" in metadata
    assert "Requires-Dist: prumi-adapter-dbt[duckdb]<0.2,>=0.1.0; extra == 'dbt'" in metadata


@pytest.mark.parametrize("wheel_name", [CORE, ADAPTER])
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
    with zipfile.ZipFile(wheels / f"{ADAPTER}-py3-none-any.whl") as wheel:
        entry_points = wheel.read(f"{ADAPTER}.dist-info/entry_points.txt").decode()
        metadata = wheel.read(f"{ADAPTER}.dist-info/METADATA").decode()
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


# The release workflow (.github/workflows/release.yml) and its tag check

REPO = PACKAGES.parent
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


@pytest.fixture
def release_tree(tmp_path):
    """A copy of what the tag check reads: itself, both pyproject files and a CHANGELOG."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "check_release.py", tmp_path / "scripts")
    for folder in ("prumi-core", "prumi-adapter-dbt"):
        (tmp_path / "packages" / folder).mkdir(parents=True)
        shutil.copy(PACKAGES / folder / "pyproject.toml", tmp_path / "packages" / folder)
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## {VERSION} — 2026-10-20\n\n- New.\n")
    return tmp_path


def check_release(tree: Path, *args: str) -> tuple[int, str]:
    script = tree / "scripts" / "check_release.py"
    result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def test_the_tag_check_accepts_the_tag_of_both_versions(release_tree):
    assert check_release(release_tree, f"v{VERSION}")[0] == 0
    assert check_release(release_tree)[0] == 0  # a manual run: no tag


def test_the_tag_check_refuses_a_tag_that_doesnt_match_both_versions(release_tree):
    code, out = check_release(release_tree, "v0.1.99")
    assert code == 1
    assert f"prumi is version {VERSION} in packages/prumi-core/pyproject.toml, not 0.1.99" in out
    assert f"prumi-adapter-dbt is version {VERSION}" in out
    code, out = check_release(release_tree, VERSION)
    assert code == 1 and "isn't v<version>" in out


def test_the_tag_check_refuses_packages_with_different_versions(release_tree):
    adapter = release_tree / "packages" / "prumi-adapter-dbt" / "pyproject.toml"
    adapter.write_text(adapter.read_text().replace(f'version = "{VERSION}"', 'version = "0.1.99"'))
    for args in ([f"v{VERSION}"], []):
        code, out = check_release(release_tree, *args)
        assert code == 1 and "prumi-adapter-dbt is version 0.1.99" in out, args


@pytest.mark.parametrize(
    "heading",
    [
        f"## {VERSION} — unreleased",
        f"## {VERSION}",
        f"## {VERSION} — 2026-13-01",
        "## 0.0.9 — 2026-10-01",
        f"### {VERSION} — 2026-10-20",
    ],
)
def test_the_tag_check_refuses_a_changelog_without_a_dated_heading(release_tree, heading):
    (release_tree / "CHANGELOG.md").write_text(f"# Changelog\n\n{heading}\n\n- New.\n")
    code, out = check_release(release_tree, f"v{VERSION}")
    assert code == 1
    assert f"CHANGELOG.md has no dated heading for {VERSION}" in out


@pytest.fixture(scope="module")
def release_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_release_runs_on_version_tags_and_by_hand(release_workflow):
    triggers = release_workflow.get("on", release_workflow.get(True))  # YAML 1.1: `on` is true
    assert triggers["push"] == {"tags": ["v*"]}
    assert "workflow_dispatch" in triggers


def test_only_the_upload_job_gets_an_id_token_in_the_pypi_environment(release_workflow):
    jobs = release_workflow["jobs"]
    assert "id-token" not in release_workflow.get("permissions", {})
    with_id_token = [n for n, job in jobs.items() if "id-token" in job.get("permissions", {})]
    assert with_id_token == ["upload"]
    assert jobs["upload"]["permissions"] == {"id-token": "write"}
    assert jobs["upload"]["environment"] == "pypi"
    publishing = [
        name
        for name, job in jobs.items()
        for step in job["steps"]
        if "publish" in step.get("run", "") or "publish" in step.get("uses", "")
    ]
    assert publishing == ["upload"]


def test_no_step_of_the_release_reads_a_token_secret():
    text = WORKFLOW.read_text()
    assert "secrets." not in text and "secrets[" not in text
    assert not re.search(r"(?i)password|api.?token|PUBLISH_TOKEN|TWINE_", text)


def test_a_manual_run_of_the_release_skips_the_upload(release_workflow):
    assert release_workflow["jobs"]["upload"]["if"] == "github.event_name == 'push'"


def test_the_release_uploads_only_after_the_tag_check_the_checks_and_the_install_check(
    release_workflow,
):
    jobs = release_workflow["jobs"]

    def needs(name: str) -> set[str]:
        direct = jobs[name].get("needs", [])
        direct = [direct] if isinstance(direct, str) else direct
        return set(direct).union(*(needs(n) for n in direct))

    def runs(name: str) -> list[str]:
        return [step.get("run", "") for step in jobs[name]["steps"]]

    assert needs("checks") == {"tag"} and needs("upload") == {"tag", "checks", "build"}
    assert any("scripts/check_release.py" in run for run in runs("tag"))
    for command in ("uv run ruff check", "uv run ruff format --check", "uv run pytest"):
        assert command in runs("checks"), command
    pytest_step = next(s for s in jobs["checks"]["steps"] if s.get("run") == "uv run pytest")
    assert pytest_step["env"]["PRUMI_REQUIRE_BROWSER"] == "1"
    builds = [run for run in runs("build") if run.startswith("uv build")]
    assert [b.split()[3] for b in builds] == ["prumi", "prumi-adapter-dbt"]
    assert "uv run python scripts/check_install.py --dist dist --with-deps" in runs("build")
