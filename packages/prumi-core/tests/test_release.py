"""Release: one version everywhere, the builds of both packages, the release workflow."""

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from importlib.metadata import version
from pathlib import Path

import prumi
import pytest
import yaml

PACKAGES = Path(__file__).parents[2]
REPO = PACKAGES.parent


def source_version(init: Path) -> str:
    """The `__version__ = "..."` value in an `__init__.py`, which is where a version lives."""
    found = re.search(r'^__version__ = "([^"]+)"', init.read_text(), re.MULTILINE)
    assert found, init
    return found.group(1)


VERSION = source_version(PACKAGES / "prumi-core" / "src" / "prumi" / "__init__.py")
# The range each package asks of the other: compatible releases of the same minor.
NEXT_RANGE = f"~={VERSION}"
# Wheel and sdist names without their suffix, and the wheels' .dist-info folders.
CORE = f"prumi-{VERSION}"
ADAPTER = f"prumi_adapter_dbt-{VERSION}"


def test_each_package_takes_its_version_from_its_init_file():
    for folder in ("prumi-core", "prumi-adapter-dbt"):
        with (PACKAGES / folder / "pyproject.toml").open("rb") as f:
            project = tomllib.load(f)
        assert "version" not in project["project"], folder
        assert project["project"]["dynamic"] == ["version"], folder
        assert project["tool"]["hatch"]["version"]["path"].endswith("/__init__.py"), folder


def test_all_three_packages_have_one_version():
    adapter_init = PACKAGES / "prumi-adapter-dbt" / "src" / "prumi_adapter_dbt" / "__init__.py"
    assert source_version(adapter_init) == VERSION
    assert prumi.__version__ == VERSION
    assert version("prumi") == version("prumi-adapter-dbt") == VERSION
    runtime = json.loads((PACKAGES / "prumi-runtime" / "package.json").read_text())
    assert runtime["version"] == VERSION


def test_the_packages_require_each_other_within_the_released_minor():
    with (PACKAGES / "prumi-adapter-dbt" / "pyproject.toml").open("rb") as f:
        assert f"prumi{NEXT_RANGE}" in tomllib.load(f)["project"]["dependencies"]
    with (PACKAGES / "prumi-core" / "pyproject.toml").open("rb") as f:
        extras = tomllib.load(f)["project"]["optional-dependencies"]
    assert extras["dbt"] == [f"prumi-adapter-dbt[duckdb]{NEXT_RANGE}"]


def test_uv_watches_the_init_file_so_a_bump_reaches_the_dev_environment():
    for folder, init in (
        ("prumi-core", "src/prumi/__init__.py"),
        ("prumi-adapter-dbt", "src/prumi_adapter_dbt/__init__.py"),
    ):
        with (PACKAGES / folder / "pyproject.toml").open("rb") as f:
            keys = tomllib.load(f)["tool"]["uv"]["cache-keys"]
        assert {"file": "pyproject.toml"} in keys and {"file": init} in keys, folder


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
    assert f"Requires-Dist: prumi-adapter-dbt[duckdb]{NEXT_RANGE}; extra == 'dbt'" in metadata


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


# Apache-2.0 asks every redistribution, PyPI included, to carry these. The repository's are the
# source; each package folder holds a copy so the build tools pick it up.
LICENSE_FILES = ("LICENSE", "NOTICE")


@pytest.mark.parametrize("folder", ["prumi-core", "prumi-adapter-dbt"])
def test_the_package_folders_copy_license_and_notice_from_the_repository(folder):
    for name in LICENSE_FILES:
        assert (PACKAGES / folder / name).read_bytes() == (REPO / name).read_bytes(), (
            f"packages/{folder}/{name} differs from {name}: copy the repository's {name} over it."
        )


@pytest.mark.parametrize("name", [CORE, ADAPTER])
def test_the_wheels_and_sdists_carry_license_and_notice(wheels, name):
    with zipfile.ZipFile(wheels / f"{name}-py3-none-any.whl") as wheel:
        for file in LICENSE_FILES:
            assert wheel.read(f"{name}.dist-info/licenses/{file}") == (REPO / file).read_bytes()
        metadata = wheel.read(f"{name}.dist-info/METADATA").decode()
    headers = metadata.split("\n\n", 1)[0].splitlines()
    assert [h for h in headers if h.startswith("License-File:")] == [
        "License-File: LICENSE",
        "License-File: NOTICE",
    ]
    with tarfile.open(wheels / f"{name}.tar.gz") as sdist:
        for file in LICENSE_FILES:
            assert sdist.extractfile(f"{name}/{file}").read() == (REPO / file).read_bytes()


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
    assert f"Requires-Dist: prumi{NEXT_RANGE}" in metadata
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

WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


INITS = {
    "prumi-core": "src/prumi/__init__.py",
    "prumi-adapter-dbt": "src/prumi_adapter_dbt/__init__.py",
}


@pytest.fixture
def release_tree(tmp_path):
    """A copy of what the tag check reads: itself, the three version sources and a CHANGELOG."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "check_release.py", tmp_path / "scripts")
    for folder, init in INITS.items():
        target = tmp_path / "packages" / folder / init
        target.parent.mkdir(parents=True)
        shutil.copy(PACKAGES / folder / init, target)
    (tmp_path / "packages" / "prumi-runtime").mkdir(parents=True)
    shutil.copy(
        PACKAGES / "prumi-runtime" / "package.json", tmp_path / "packages" / "prumi-runtime"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        f"## [{VERSION}](https://github.com/prumihq/prumi/compare/v0.0.9...v{VERSION})"
        " (2026-10-20)\n"
    )
    return tmp_path


def set_versions(tree: Path, version: str, *, runtime: str | None = None) -> None:
    """Rewrite the three version sources; `runtime` gives package.json another version."""
    for folder, init in INITS.items():
        path = tree / "packages" / folder / init
        path.write_text(
            re.sub(
                r'^__version__ = "[^"]+"',
                f'__version__ = "{version}"',
                path.read_text(),
                flags=re.M,
            )
        )
    package = tree / "packages" / "prumi-runtime" / "package.json"
    data = json.loads(package.read_text())
    data["version"] = runtime or version
    package.write_text(json.dumps(data))


def check_release(tree: Path, *args: str) -> tuple[int, str]:
    script = tree / "scripts" / "check_release.py"
    result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def test_the_tag_check_accepts_the_tag_of_both_versions(release_tree):
    assert check_release(release_tree, f"v{VERSION}")[0] == 0
    assert check_release(release_tree)[0] == 0  # a manual run: no tag


def test_the_tag_check_refuses_a_tag_that_doesnt_match_the_versions(release_tree):
    code, out = check_release(release_tree, "v0.1.99")
    assert code == 1
    assert (
        f"prumi is version {VERSION} in packages/prumi-core/src/prumi/__init__.py, not 0.1.99"
        in out
    )
    assert f"prumi-adapter-dbt is version {VERSION}" in out
    assert f"prumi-runtime is version {VERSION} in packages/prumi-runtime/package.json" in out
    code, out = check_release(release_tree, VERSION)
    assert code == 1 and "isn't v<version>" in out


def test_the_tag_check_refuses_packages_with_different_versions(release_tree):
    adapter = release_tree / "packages" / "prumi-adapter-dbt" / INITS["prumi-adapter-dbt"]
    adapter.write_text(adapter.read_text().replace(f'"{VERSION}"', '"0.1.99"'))
    for args in ([f"v{VERSION}"], []):
        code, out = check_release(release_tree, *args)
        assert code == 1 and "prumi-adapter-dbt is version 0.1.99" in out, args


@pytest.mark.parametrize(
    "heading",
    [
        f"## {VERSION} — 2026-10-20",
        f"## [{VERSION}](x) (2026-13-01)",
        "## [0.0.9](x) (2026-10-01)",
        f"### [{VERSION}](x) (2026-10-20)",
        f"## [{VERSION}](x)",
    ],
)
def test_the_tag_check_refuses_a_changelog_without_a_release_please_heading(release_tree, heading):
    (release_tree / "CHANGELOG.md").write_text(f"# Changelog\n\n{heading}\n\n- New.\n")
    code, out = check_release(release_tree, f"v{VERSION}")
    assert code == 1
    assert f"CHANGELOG.md has no heading for {VERSION}" in out


def test_the_tag_check_accepts_a_patch_version(release_tree):
    set_versions(release_tree, "0.2.1")
    (release_tree / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## [0.2.1](https://github.com/prumihq/prumi/compare/v0.2.0...v0.2.1) (2026-12-01)\n"
    )
    assert check_release(release_tree, "v0.2.1")[0] == 0


def test_the_tag_check_refuses_version_one_or_above(release_tree):
    set_versions(release_tree, "1.0.0")
    (release_tree / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0](x) (2026-12-01)\n")
    code, out = check_release(release_tree, "v1.0.0")
    assert code == 1 and "1.0 or above; Prumi releases stay 0.x" in out


def test_the_tag_check_refuses_a_runtime_with_another_version(release_tree):
    set_versions(release_tree, VERSION, runtime="0.1.99")
    code, out = check_release(release_tree, f"v{VERSION}")
    assert (
        code == 1
        and "prumi-runtime is version 0.1.99 in packages/prumi-runtime/package.json" in out
    )


@pytest.mark.xfail(strict=True, reason="CHANGELOG.md is rewritten in release-please's format")
def test_the_repositorys_changelog_has_a_heading_for_the_current_version():
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_release.py")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def release_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_release_runs_on_version_tags_and_by_hand(release_workflow):
    triggers = release_workflow.get("on", release_workflow.get(True))  # YAML 1.1: `on` is true
    assert triggers["push"] == {"tags": ["v*"]}
    assert "workflow_dispatch" in triggers


# Each package uploads from its own GitHub environment, where PyPI trusts this workflow for it.
UPLOADS = {"pypi": CORE, "pypi-adapter-dbt": ADAPTER}


def upload_jobs(workflow: dict) -> dict[str, str]:
    """The jobs that can get an id token, by their environment."""
    jobs = workflow["jobs"].items()
    return {
        job.get("environment"): n for n, job in jobs if "id-token" in job.get("permissions", {})
    }


def test_two_upload_jobs_get_an_id_token_each_in_its_packages_environment(release_workflow):
    jobs = release_workflow["jobs"]
    assert "id-token" not in release_workflow.get("permissions", {})
    with_id_token = [n for n, job in jobs.items() if "id-token" in job.get("permissions", {})]
    assert len(with_id_token) == 2
    uploads = upload_jobs(release_workflow)
    assert sorted(uploads) == sorted(UPLOADS)
    for name in uploads.values():
        assert jobs[name]["permissions"] == {"id-token": "write"}
    publishing = [
        name
        for name, job in jobs.items()
        for step in job["steps"]
        if "publish" in step.get("run", "") or "publish" in step.get("uses", "")
    ]
    assert sorted(publishing) == sorted(uploads.values())


@pytest.mark.parametrize(("environment", "package"), UPLOADS.items())
def test_each_upload_job_uploads_only_its_packages_files(release_workflow, environment, package):
    job = release_workflow["jobs"][upload_jobs(release_workflow)[environment]]
    [publish] = [s["run"] for s in job["steps"] if "uv publish" in s.get("run", "")]
    words = shlex.split(publish)
    # --check-url skips the files already on PyPI, so rerunning the job finishes a partial upload.
    assert words[:6] == [
        "uv",
        "publish",
        "--trusted-publishing",
        "always",
        "--check-url",
        "https://pypi.org/simple/",
    ]
    built = [
        f"dist/{name}{end}" for name in UPLOADS.values() for end in ("-py3-none-any.whl", ".tar.gz")
    ]
    uploaded = [f for f in built if any(fnmatch.fnmatchcase(f, glob) for glob in words[6:])]
    assert uploaded == [f"dist/{package}-py3-none-any.whl", f"dist/{package}.tar.gz"]


def test_no_step_of_the_release_reads_a_token_secret():
    text = WORKFLOW.read_text()
    assert "secrets." not in text and "secrets[" not in text
    assert not re.search(r"(?i)password|api.?token|PUBLISH_TOKEN|TWINE_", text)


def test_a_manual_run_of_the_release_skips_both_uploads(release_workflow):
    for name in upload_jobs(release_workflow).values():
        assert release_workflow["jobs"][name]["if"] == "github.event_name == 'push'", name


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

    assert needs("checks") == {"tag"}
    for name in upload_jobs(release_workflow).values():
        assert needs(name) == {"tag", "checks", "build"}, name
    assert any("scripts/check_release.py" in run for run in runs("tag"))
    for command in ("uv run ruff check", "uv run ruff format --check", "uv run pytest"):
        assert command in runs("checks"), command
    pytest_step = next(s for s in jobs["checks"]["steps"] if s.get("run") == "uv run pytest")
    assert pytest_step["env"]["PRUMI_REQUIRE_BROWSER"] == "1"
    builds = [run for run in runs("build") if run.startswith("uv build")]
    assert [b.split()[3] for b in builds] == ["prumi", "prumi-adapter-dbt"]
    assert "uv run python scripts/check_install.py --dist dist --with-deps" in runs("build")


# The release PR: release-please-config.json and .github/workflows/release-please.yml

CONFIG = REPO / "release-please-config.json"


def test_release_please_versions_the_whole_repo_as_one_package():
    config = json.loads(CONFIG.read_text())
    assert list(config["packages"]) == ["."]
    assert config["include-component-in-tag"] is False
    assert config["bump-minor-pre-major"] is True
    assert config.get("bump-patch-for-minor-pre-major", False) is False
    assert config["draft"] is True and config["force-tag-creation"] is True
    assert json.loads((REPO / ".release-please-manifest.json").read_text()) == {".": VERSION}


def test_the_changelog_shows_features_fixes_performance_and_reverts_only():
    sections = json.loads(CONFIG.read_text())["changelog-sections"]
    shown = {s["type"]: s["section"] for s in sections if not s.get("hidden")}
    assert shown == {
        "feat": "Features",
        "fix": "Bug Fixes",
        "perf": "Performance",
        "revert": "Reverts",
    }
    hidden = {s["type"] for s in sections if s.get("hidden")}
    assert hidden == {"docs", "refactor", "test", "build", "ci", "chore", "style"}


def test_every_file_release_please_bumps_has_its_marker_on_the_current_version():
    extra = json.loads(CONFIG.read_text())["packages"]["."]["extra-files"]
    paths = sorted(e if isinstance(e, str) else e["path"] for e in extra)
    assert paths == sorted(
        [
            "packages/prumi-core/src/prumi/__init__.py",
            "packages/prumi-adapter-dbt/src/prumi_adapter_dbt/__init__.py",
            "packages/prumi-core/pyproject.toml",
            "packages/prumi-adapter-dbt/pyproject.toml",
            "packages/prumi-runtime/package.json",
        ]
    )
    for entry in extra:
        path = entry if isinstance(entry, str) else entry["path"]
        if path.endswith(".json"):
            assert entry == {"type": "json", "path": path, "jsonpath": "$.version"}
            continue
        marked = [
            line
            for line in (REPO / path).read_text().splitlines()
            if "x-release-please-version" in line
        ]
        assert len(marked) == 1 and VERSION in marked[0], path


def test_the_release_pr_runs_as_the_release_app_on_pushes_to_main():
    workflow = yaml.safe_load((REPO / ".github/workflows/release-please.yml").read_text())
    assert workflow.get("on", workflow.get(True)) == {"push": {"branches": ["main"]}}
    assert workflow["permissions"] == {}
    steps = workflow["jobs"]["release-pr"]["steps"]
    token, release = steps[0], steps[1]
    assert token["uses"].startswith("actions/create-github-app-token@v")
    assert token["with"]["client-id"] == "${{ vars.RELEASE_APP_CLIENT_ID }}"
    assert token["with"]["private-key"] == "${{ secrets.RELEASE_APP_PRIVATE_KEY }}"
    assert release["uses"].startswith("googleapis/release-please-action@v")
    assert release["with"]["token"] == f"${{{{ steps.{token['id']}.outputs.token }}}}"


def test_the_release_pr_job_skips_until_the_app_is_configured():
    workflow = yaml.safe_load((REPO / ".github/workflows/release-please.yml").read_text())
    assert workflow["jobs"]["release-pr"]["if"] == "vars.RELEASE_APP_CLIENT_ID != ''"


def workflow_actions() -> dict[str, list[str]]:
    """Every `uses:` of every workflow, by the workflow's file name."""
    found = {}
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        jobs = yaml.safe_load(path.read_text())["jobs"].values()
        found[path.name] = [s["uses"] for job in jobs for s in job["steps"] if "uses" in s]
    return found


def test_every_action_is_pinned_to_an_exact_version():
    # A bare major such as @v4 moves under us; an exact tag changes only when we change it.
    actions = workflow_actions()
    assert {"ci.yml", "release.yml", "release-please.yml"} <= set(actions)
    for name, uses in actions.items():
        loose = [u for u in uses if not re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+\.\d+\.\d+", u)]
        assert not loose, f"{name} uses actions without an exact version: {loose}"
