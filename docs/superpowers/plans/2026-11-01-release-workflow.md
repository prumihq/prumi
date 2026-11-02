# Release Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Releases come from a release-please release PR. Merging it tags `v<version>`, makes a draft GitHub Release and starts the existing `release.yml`, which publishes the release only after both PyPI uploads succeed. Each package's version lives only in its `__init__.py`.

**Architecture:**
- **Versions.** A dynamic hatchling version is read from each package's `__init__.py`. release-please markers bump the two `__init__.py` files, `package.json` and the two cross-package ranges.
- **Release PR.** A new `release-please.yml` runs as a GitHub App, so CI runs on the release PR and its tag starts `release.yml`.
- **Release.** `release.yml` keeps its tag trigger and gains a final job that un-drafts the release.
- **PR titles.** A PR-title workflow enforces Conventional Commits.

**Tech Stack:** GitHub Actions, release-please (googleapis/release-please-action), actions/create-github-app-token, amannn/action-semantic-pull-request, hatchling, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-release-workflow-design.md`

## Global Constraints

- The version stays `0.1.0` in this work. Nothing may produce a version ≥ 1.0.
- `release.yml` keeps `on: push: tags: ["v*"]` and `workflow_dispatch`, the jobs `tag`, `checks`, `build`, `upload-prumi`, `upload-prumi-adapter-dbt`, the environments `pypi` / `pypi-adapter-dbt`, and no `secrets.` anywhere in the file.
- Tags are `v<version>`, with no component name in them.
- The ranges are written `~=X.Y.Z` with the full current version: `prumi~=0.1.0` and `prumi-adapter-dbt[duckdb]~=0.1.0`.
- Pin every action to an exact version, as the repo already does (`astral-sh/setup-uv@v10.3.0`). No floating major tags.
- The release App's names: repository variable `RELEASE_APP_CLIENT_ID` and repository secret `RELEASE_APP_PRIVATE_KEY`.
- Allowed PR title types: `feat`, `fix`, `perf`, `revert`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `style`. Scopes are optional.
- Changelog sections shown: Features (`feat`), Bug Fixes (`fix`), Performance (`perf`), Reverts (`revert`). All other types are hidden.
- Code and tests never cite the design or this plan, and tests never read `docs/superpowers/`.
- GitHub settings (merge methods, branch protection, rulesets, immutable releases, release edits) are changed only with the maintainer's go-ahead for each one.
- Branch commits may use any message. This branch's PR title is `ci: release with release-please from a release PR`, which doesn't trigger a release.

## Review Focus

1. **Patch releases (`0.2.1`).** The tag check and the range test must accept `~=0.2.1`, not only `X.Y.0`. Covered by `test_the_tag_check_accepts_a_patch_version` (Task 2).
2. **A `Release-As: 1.0.0` or misconfigured bump reaching the tag.** The tag check must refuse it. Covered by `test_the_tag_check_refuses_version_one_or_above` (Task 2).
3. **`package.json` drifting from the Python versions.** The tag check must name it. Covered by `test_the_tag_check_refuses_a_runtime_with_another_version` (Task 2).
4. **A pushed `main` before the App exists.** The release-please job must skip, not fail. Covered by `test_the_release_pr_job_skips_until_the_app_is_configured` (Task 3).
5. **A marker that no longer sits on the version line.** release-please would silently stop bumping that file. Covered by `test_every_file_release_please_bumps_has_its_marker_on_the_current_version` (Task 3).

---

### Task 1: One version source per package (dynamic version)

**Files:**
- Modify: `packages/prumi-core/pyproject.toml`, `packages/prumi-adapter-dbt/pyproject.toml`
- Modify: `packages/prumi-core/src/prumi/__init__.py`, `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/__init__.py`
- Modify: `uv.lock` (regenerated)
- Test: `packages/prumi-core/tests/test_release.py`

**Interfaces:**
- Produces: `VERSION` in `test_release.py`, read from `prumi/__init__.py` (Tasks 2 and 3 use it). It's defined as a module-level function plus constant:

  ```python
  def source_version(init: Path) -> str: ...  # the `__version__ = "..."` value in that file


  VERSION = source_version(PACKAGES / "prumi-core" / "src" / "prumi" / "__init__.py")
  NEXT_RANGE = f"~={VERSION}"
  ```

- [ ] **Step 1: Rewrite the version tests in `test_release.py`**

  Replace the literal `VERSION = "0.1.0"` with the `source_version` definition above, using a regex on `__version__ = "(...)"`. Replace `test_versions` with:

  ```python
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
  ```

  In the wheel metadata tests, change the expected lines:
  - `"Requires-Dist: prumi-adapter-dbt[duckdb]<0.2,>=0.1.0; extra == 'dbt'"` becomes `f"Requires-Dist: prumi-adapter-dbt[duckdb]{NEXT_RANGE}; extra == 'dbt'"`
  - `"Requires-Dist: prumi<0.2,>=0.1.0"` becomes `f"Requires-Dist: prumi{NEXT_RANGE}"`

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -k "init_file or one_version or each_other or watches or dbt_adapter_as_an_extra or registers_itself" -v`
  Expected: FAIL. `version` is still in `[project]`, `tool.uv` is missing, and the ranges are `>=0.1.0,<0.2`.

- [ ] **Step 3: Make the versions dynamic**

  In each `pyproject.toml`:
  - Replace `version = "0.1.0"` with `dynamic = ["version"]`.
  - Add `[tool.hatch.version]` with `path = "src/prumi/__init__.py"` (core) or `path = "src/prumi_adapter_dbt/__init__.py"` (adapter).
  - Add `[tool.uv]` with `cache-keys = [{ file = "pyproject.toml" }, { file = "<that init path>" }]`.
  - Write the ranges as `"prumi~=0.1.0",  # x-release-please-version` and `dbt = ["prumi-adapter-dbt[duckdb]~=0.1.0"]  # x-release-please-version`.
  - Keep the existing comment above each range, and add one line saying release-please bumps it.

  In each `__init__.py`, write `__version__ = "0.1.0"  # x-release-please-version`. Keep it as a literal: this file is now the source. Add a one-line comment above it saying the version lives here and `pyproject.toml` reads it.

  Then run `uv lock`. Expected output: `Updated prumi v0.1.0 -> (dynamic)` and `Updated prumi-adapter-dbt v0.1.0 -> (dynamic)`, and `git diff uv.lock` shows exactly two removed `version = "0.1.0"` lines.

- [ ] **Step 4: Run the release tests and lint**

  Run: `uv sync --locked && uv run pytest packages/prumi-core/tests/test_release.py -v && uv run ruff check && uv run ruff format --check`
  Expected: the new tests PASS. The tag-check tests (`test_the_tag_check_*`) FAIL, because `check_release.py` still reads `project.version`. Task 2 fixes them; nothing else fails.

- [ ] **Step 5: Check that the build carries the version**

  Run: `uv run python scripts/check_install.py`
  Expected: `check_install: passed`.

- [ ] **Step 6: Commit**

  ```bash
  git add packages/*/pyproject.toml packages/*/src/*/__init__.py uv.lock packages/prumi-core/tests/test_release.py
  git commit -m "build: read each package's version from its __init__.py"
  ```

### Task 2: The tag check reads the new sources

**Files:**
- Modify: `scripts/check_release.py`
- Test: `packages/prumi-core/tests/test_release.py` (the `release_tree` fixture and the `test_the_tag_check_*` tests)

**Interfaces:**
- Consumes: `VERSION` and `source_version` (Task 1).
- Produces: `check_release.py [v<version>]`, with the same CLI and exit codes as today (0 when everything matches, 1 with messages, 2 on usage errors). Messages:
  - `"{name} is version {have} in {file}, not {version}."`, where `{file}` is the `__init__.py` or `packages/prumi-runtime/package.json`, and `{name}` is one of `prumi`, `prumi-adapter-dbt`, `prumi-runtime`
  - `"The tag {tag} isn't v<version>, e.g. v{...}."` (unchanged)
  - `"Version {version} is 1.0 or above; Prumi releases stay 0.x."`
  - `"CHANGELOG.md has no heading for {version}: release-please writes `## [{version}](link) (YYYY-MM-DD)`."`

- [ ] **Step 1: Update the fixture and tests**

  - `release_tree` copies `scripts/check_release.py`, both `__init__.py` files (at their paths under `packages/`) and `packages/prumi-runtime/package.json`. It no longer copies the pyproject files. It writes a `CHANGELOG.md` containing `## [{VERSION}](https://github.com/prumihq/prumi/compare/v0.0.9...v{VERSION}) (2026-10-20)`.
  - Add a fixture helper `set_versions(tree, version, *, runtime=None)` that rewrites all three version sources, so tests can try other versions.
  - Keep the existing tests, updated to the new sources and messages (`... in packages/prumi-core/src/prumi/__init__.py, not 0.1.99`).
  - In the changelog test, swap the parametrized headings for these refused cases:
    - `f"## {VERSION} — 2026-10-20"` (the old format)
    - `f"## [{VERSION}](x) (2026-13-01)"`
    - `"## [0.0.9](x) (2026-10-01)"`
    - `f"### [{VERSION}](x) (2026-10-20)"`
    - `f"## [{VERSION}](x)"`

    It asserts `f"CHANGELOG.md has no heading for {VERSION}"`.
  - Add:

  ```python
  def test_the_tag_check_accepts_a_patch_version(release_tree):
      set_versions(release_tree, "0.2.1")
      (release_tree / "CHANGELOG.md").write_text(
          "# Changelog\n\n## [0.2.1](https://github.com/prumihq/prumi/compare/v0.2.0...v0.2.1) (2026-12-01)\n"
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


  def test_the_repositorys_changelog_has_a_heading_for_the_current_version():
      result = subprocess.run(
          [sys.executable, str(REPO / "scripts" / "check_release.py")], capture_output=True, text=True
      )
      assert result.returncode == 0, result.stderr
  ```

  The last test fails until Task 4 rewrites `CHANGELOG.md`. Mark it `@pytest.mark.xfail(strict=True, reason="CHANGELOG.md is rewritten in release-please's format")`; Task 4 removes the mark.

- [ ] **Step 2: Run the tag-check tests to see them fail**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -k tag_check -v`
  Expected: FAIL, because the script still reads `project.version`.

- [ ] **Step 3: Rewrite `problems(tag: str | None) -> list[str]` in `scripts/check_release.py`**

  - Sources: a `PACKAGES` mapping from name to version file: `prumi` → `packages/prumi-core/src/prumi/__init__.py`, `prumi-adapter-dbt` → `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/__init__.py`, `prumi-runtime` → `packages/prumi-runtime/package.json`.
  - Read `__version__` with the same regex as the test, and `package.json` with `json`.
  - Order of messages: tag format, then mismatches, then 1.0, then changelog.
  - Heading regex: `^## \[{version}\]\(\S+\) \((\d{4}-\d{2}-\d{2})\)\s*$`, keeping `_is_date`.
  - Update the module docstring to the new sources.

- [ ] **Step 4: Run the release tests**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -v && uv run ruff check && uv run ruff format --check`
  Expected: all PASS, with the repository-changelog test reported as XFAIL.

- [ ] **Step 5: Commit**

  ```bash
  git add scripts/check_release.py packages/prumi-core/tests/test_release.py
  git commit -m "ci: the tag check reads versions from the init files and refuses 1.0"
  ```

### Task 3: release-please configuration and the release PR workflow

**Files:**
- Create: `release-please-config.json`, `.release-please-manifest.json`, `.github/workflows/release-please.yml`
- Test: `packages/prumi-core/tests/test_release.py` (new section, "The release PR")

**Interfaces:**
- Consumes: the `x-release-please-version` markers (Task 1).
- Produces:
  - Workflow `release-please.yml` with one job, `release-pr`.
  - Tags `v<version>` and draft releases, which Task 5 un-drafts.

- [ ] **Step 1: Write the tests**

  ```python
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
              l for l in (REPO / path).read_text().splitlines() if "x-release-please-version" in l
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
  ```

  No `@v` tag may be a bare major (`@v4`). Add the new workflow to a shared helper that checks every `uses:` in `.github/workflows/*.yml` has an exact version: `re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+\.\d+\.\d+", uses)`. Name that test `test_every_action_is_pinned_to_an_exact_version`. If an existing workflow fails it, pin that action too and say so in the commit body.

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -k "release_please or release_pr or changelog_shows or pinned" -v`
  Expected: FAIL, because the files don't exist yet.

- [ ] **Step 3: Write the config, manifest and workflow**

  - `release-please-config.json`: one package `"."` with `"package-name": "prumi"` and the options the tests check. Extra-files:
    - the four marked Python/TOML files, as `{"type": "generic", "path": ...}`
    - `package.json`, as the JSON entry
  - Choose the `release-type` so the release PR changes **only** `CHANGELOG.md`, `.release-please-manifest.json` and the five extra-files. No `version.txt`, and not the root `pyproject.toml`. Step 5 verifies this.
  - `.release-please-manifest.json`: `{".": "0.1.0"}`.
  - `release-please.yml`:
    - name `Release PR`, on push to `main`
    - top-level `permissions: {}`
    - job `release-pr` with the `if` above
    - steps: `actions/create-github-app-token` (exact version, id `app-token`, `client-id` and `private-key` as tested), then `googleapis/release-please-action` (exact version) with `token`, `config-file` and `manifest-file`
    - `concurrency: { group: release-please, cancel-in-progress: false }`
    - one comment saying why the App token: events made with `GITHUB_TOKEN` start no workflows, so CI wouldn't run on the release PR and the tag wouldn't start `release.yml`

- [ ] **Step 4: Run the tests**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -v`
  Expected: PASS, with the changelog test still reported as XFAIL.

- [ ] **Step 5: Dry run against GitHub (needs the maintainer's go-ahead to push the branch)**

  ```bash
  git push -u origin chore/release-workflow
  npx --yes release-please@<exact version matching the action> release-pr --dry-run \
    --repo-url prumihq/prumi --target-branch chore/release-workflow \
    --config-file release-please-config.json --manifest-file .release-please-manifest.json \
    --release-as 0.2.0 --token "$(gh auth token)"
  ```

  Expected: a would-be release PR for `0.2.0` changing exactly:
  - `CHANGELOG.md`
  - `.release-please-manifest.json`
  - both `__init__.py` (`0.2.0`)
  - both `pyproject.toml` (`~=0.2.0`, no other line)
  - `package.json` (`0.2.0`)

  Nothing else changes. If another file appears, adjust `release-type` and options until it doesn't, then rerun. Record the CLI version and the result in the commit body.

- [ ] **Step 6: Commit**

  ```bash
  git add release-please-config.json .release-please-manifest.json .github/workflows/release-please.yml packages/prumi-core/tests/test_release.py
  git commit -m "ci: open the release PR with release-please as the release App"
  ```

### Task 4: Rewrite the changelog; known limitations move to the README

**Files:**
- Modify: `CHANGELOG.md`, `README.md` (section "Status and roadmap")
- Test: `packages/prumi-core/tests/test_release.py` (remove the `xfail` from `test_the_repositorys_changelog_has_a_heading_for_the_current_version`)

- [ ] **Step 1: Remove the `xfail` mark, run the test and see it fail**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py::test_the_repositorys_changelog_has_a_heading_for_the_current_version -v`
  Expected: FAIL with `CHANGELOG.md has no heading for 0.1.0`.

- [ ] **Step 2: Rewrite `CHANGELOG.md`**

  - Keep `# Changelog` and one intro line: releases stay 0.x, and entries come from PR titles through release-please.
  - Then write `## [0.1.0](https://github.com/prumihq/prumi/releases/tag/v0.1.0) (2026-10-19)`, then the release's one-paragraph summary, unchanged in substance, then `### Features`.
  - Put every fact from today's "Added" list under Features as short bullets. Keep nested bullets where today's text has them. Don't drop a fact.
  - Remove the "Known limitations" section.

- [ ] **Step 3: Move the limitations to `README.md`**

  - Under "Status and roadmap", add a "Known limitations" list with each of the ten 0.1.0 limitations that the section doesn't already state.
  - Replace "The [changelog](CHANGELOG.md) lists what's in it and its known limits." with a sentence pointing to the changelog for what each release contains.

- [ ] **Step 4: Run the release tests and the README tests**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -v && uv run pytest -k readme -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add CHANGELOG.md README.md packages/prumi-core/tests/test_release.py
  git commit -m "docs: rewrite the changelog in release-please's format; known limitations move to the README"
  ```

### Task 5: `release.yml` publishes the draft release after both uploads

**Files:**
- Modify: `.github/workflows/release.yml`
- Test: `packages/prumi-core/tests/test_release.py`

**Interfaces:**
- Consumes: draft releases named by their tag `v<version>` (Task 3).

- [ ] **Step 1: Write the test**

  ```python
  def test_the_release_is_published_only_after_both_uploads(release_workflow):
      job = release_workflow["jobs"]["publish-release"]
      assert set(job["needs"]) == set(upload_jobs(release_workflow).values())
      assert job["if"] == "github.event_name == 'push'"
      assert job["permissions"] == {"contents": "write"}
      [run] = [s["run"] for s in job["steps"] if "run" in s]
      assert run.split()[:3] == ["gh", "release", "edit"] and "--draft=false" in run
      assert release_workflow["permissions"] == {"contents": "read"}
  ```

- [ ] **Step 2: Run it to see it fail**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py::test_the_release_is_published_only_after_both_uploads -v`
  Expected: FAIL with `KeyError: 'publish-release'`.

- [ ] **Step 3: Add the job `publish-release` to `release.yml`**

  - It has the `needs`, `if` and `permissions` from the test.
  - Its one step: `gh release edit "$TAG" --repo "$GITHUB_REPOSITORY" --draft=false --latest`, with env `GH_TOKEN: ${{ github.token }}` and `TAG: ${{ github.ref_name }}`. It uses no `secrets.`.
  - Add one comment saying why: the release stays a draft until PyPI has both packages, and publishing it is what makes it immutable.
  - Update the header comment: the tag now comes from the release PR, and `docs/releasing.md` is the maintainer's manual.

- [ ] **Step 4: Run all release tests**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -v`
  Expected: PASS, including `test_no_step_of_the_release_reads_a_token_secret`.

- [ ] **Step 5: Commit**

  ```bash
  git add .github/workflows/release.yml packages/prumi-core/tests/test_release.py
  git commit -m "ci: publish the draft release once both packages are on PyPI"
  ```

### Task 6: PR title check and PR template

**Files:**
- Create: `.github/workflows/pr-title.yml`, `.github/pull_request_template.md`
- Test: `packages/prumi-core/tests/test_release.py`

**Interfaces:**
- Produces: the status check named `pr-title` (the job name), which becomes a required check in Task 8.

- [ ] **Step 1: Write the test**

  ```python
  def test_pr_titles_must_follow_conventional_commits():
      workflow = yaml.safe_load((REPO / ".github/workflows/pr-title.yml").read_text())
      triggers = workflow.get("on", workflow.get(True))
      assert set(triggers["pull_request"]["types"]) == {"opened", "edited", "synchronize", "reopened"}
      assert workflow["permissions"] == {"pull-requests": "read"}
      [step] = workflow["jobs"]["pr-title"]["steps"]
      assert step["uses"].startswith("amannn/action-semantic-pull-request@v")
      assert step["with"]["types"].split() == [
          "feat",
          "fix",
          "perf",
          "revert",
          "docs",
          "refactor",
          "test",
          "build",
          "ci",
          "chore",
          "style",
      ]
      assert step["with"].get("requireScope", False) is False


  def test_the_pr_template_explains_the_title_format():
      text = (REPO / ".github" / "pull_request_template.md").read_text()
      assert "feat:" in text and "fix:" in text and "!" in text and "Closes #" in text
  ```

  Use `pull_request`, not `pull_request_target`: the action only reads the title.

- [ ] **Step 2: Run them to see them fail**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -k "pr_title or pr_template" -v`
  Expected: FAIL (`FileNotFoundError`).

- [ ] **Step 3: Write the workflow and the template**

  - The workflow: name `PR title`, job `pr-title`, the action pinned to an exact version, `env: GITHUB_TOKEN: ${{ github.token }}`.
  - The template, in short sections:
    1. The title: `type: what changes, for a user`. Examples: `feat: add the X tool`, `fix: …`. `!` marks a breaking change. A `feat` or `fix` title becomes the changelog line.
    2. What changed and why.
    3. `Closes #` for the issue, if any.
    4. Checklist: tests pass; the docs `AGENTS.md` lists are updated.

- [ ] **Step 4: Run the tests and the pinned-action test**

  Run: `uv run pytest packages/prumi-core/tests/test_release.py -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add .github/workflows/pr-title.yml .github/pull_request_template.md packages/prumi-core/tests/test_release.py
  git commit -m "ci: check that PR titles follow Conventional Commits; add a PR template"
  ```

### Task 7: Docs: the maintainer manual, CONTRIBUTING, AGENTS and an ADR

**Files:**
- Modify: `docs/releasing.md` (rewrite), `CONTRIBUTING.md`, `AGENTS.md`, `docs/adr/README.md`
- Create: `docs/adr/0015-releases-from-release-please.md`

- [ ] **Step 1: Rewrite `docs/releasing.md` as the maintainer's manual**

  Four sections.

  1. **One-time setup**, in this order:
     - The PyPI publishers and the two environments. Keep today's table and text.
     - **The release App:**
       - Create it in the organization: Settings → Developer settings → GitHub Apps. Name `prumi-release`, webhook off, permissions Contents read/write and Pull requests read/write.
       - Install it on `prumi` only.
       - Store the client ID as repository variable `RELEASE_APP_CLIENT_ID` and a private key as secret `RELEASE_APP_PRIVATE_KEY`.
     - **Merge settings,** after the workflows are on `main`: squash only, title = PR title, blank body, delete branches after merge. Give the `gh api -X PATCH repos/prumihq/prumi` fields: `allow_merge_commit=false`, `allow_rebase_merge=false`, `allow_squash_merge=true`, `squash_merge_commit_title=PR_TITLE`, `squash_merge_commit_message=BLANK`, `delete_branch_on_merge=true`.
     - **Branch protection on `main`:** add the required check `pr-title` next to `python` and `install-check`, and turn on linear history. Give the `gh api` command.
     - **After the first release has worked end to end:** a tag ruleset for `refs/tags/v*` (creation, update, deletion, non-fast-forward; bypass: repository admin role and the `prumi-release` App) as a `gh api -X POST repos/prumihq/prumi/rulesets --input -` JSON body. Then turn on immutable releases (Settings → General → Releases). Explain why this comes last: until then, a misfire can be cleaned up.
  2. **Each release:**
     - Open the release PR (`chore: release X.Y.Z`) and read the changelog. Edit wording in the PR if needed.
     - Merge it.
     - On the `Release` run, approve `pypi` and `pypi-adapter-dbt` in one dialog.
     - Check that the GitHub Release turned from draft to published, then check both PyPI pages and a fresh install (keep today's commands).
     - **First release under this process only:** check that merging started exactly one `Release` run, from the tag `v<version>`. If none started, or two did, reject the deployments, clean up as in section 4, and fix the trigger. The trusted publishers depend only on the file name `release.yml` and the environments, not on the trigger, and `workflow_call` isn't an option. Then do the "after the first release" setup.
  3. **Fixing a changelog line after merge:** the `BEGIN_COMMIT_OVERRIDE` / `END_COMMIT_OVERRIDE` block in the merged PR's description, then the next push to `main` regenerates the release PR.
  4. **When something fails:**
     - Before approving: reject the deployments. Nothing is uploaded, and the release stays a draft.
     - A half-done upload: Re-run failed jobs, as today.
     - The first release before the tag rule and immutability: delete the draft and the tag, fix, then merge the next release PR or rerun.
     - After immutability, what can't be undone: a published release's tag can't move or be deleted, its name can't be reused, and PyPI never takes a version twice. The fix is the next patch version.

- [ ] **Step 2: Update `CONTRIBUTING.md`**

  - Replace the "Releases" paragraph with "How releases work" (five lines or fewer):
    - PR titles follow Conventional Commits and are checked
    - a `feat` title gives a minor release and a `fix` title a patch
    - the title is the changelog line
    - a maintainer merges the release PR, and [docs/releasing.md](docs/releasing.md) is the maintainer's manual
  - Add to the layout table: `release-please-config.json` and `.release-please-manifest.json` (what the release PR bumps), and `.github/` (workflows, the PR template).

- [ ] **Step 3: Update `AGENTS.md`**

  - **"Commits" section:**
    - The PR title follows Conventional Commits (types as in Global Constraints) and becomes the squash commit and the changelog line. A user-visible change gets a `feat`/`fix` title a user would understand. `!` marks a breaking change. Branch commits need no format.
    - Agents never create or push tags, never change version numbers, and never edit the release PR.
    - Keep the `Co-Authored-By:` rule.
  - **"Docs are part of the task":** replace the `CHANGELOG.md` bullet with "the PR title is the changelog entry". Add: "`README.md`'s Status and roadmap section, when a known limitation is added or removed".
  - **"How work is organized":** keep "Releases stay 0.x", and add that releases come from the release PR (see `docs/releasing.md`).

- [ ] **Step 4: Write ADR 0015 (status Proposed) and index it**

  `docs/adr/0015-releases-from-release-please.md`, in the format of 0014: Context, Decision, Considered, Consequences.
  - **Decision:**
    - Releases come from a release-please release PR, driven by Conventional Commit PR titles on squash merges.
    - The App token.
    - A draft release published after the uploads.
    - Each package's version is dynamic, read from its `__init__.py`, so `uv.lock` doesn't churn.
  - **Considered:**
    - a hand-run bump script (rejected: a manual step per release, and a hand-curated changelog doesn't scale to outside contributors)
    - static versions plus a relock job (rejected: an extra job that reruns on every release-PR update)
    - a version from git tags (rejected: friction with `uv.lock`, sdists and the `~=` ranges)
    - `workflow_call` (rejected: PyPI doesn't accept reusable workflows as trusted publishers)
  - **Consequences:**
    - The tag still starts `release.yml`, so ADR 0014's "uploaded on a version tag" still holds.
    - Contributors must title PRs.
    - The tag check reads the init files.

  Add the row to `docs/adr/README.md`'s table with status `Proposed`.

- [ ] **Step 5: Check the other docs `AGENTS.md` lists**

  Run: `grep -rn -E "releasing|CHANGELOG|version" README.md examples/demo-dbt-project/README.md packages/*/README.md docs/bi-rules.md`
  Expected: no stale release steps. Fix any you find.

  Then run the full suite and lint: `uv run pytest && uv run ruff check && uv run ruff format --check`. Expected: PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add docs/releasing.md CONTRIBUTING.md AGENTS.md docs/adr/
  git commit -m "docs: the release manual, how releases work for contributors, the new commit rules; propose ADR 0015"
  ```

### Task 8: GitHub setup with the maintainer (each step only on their go-ahead)

Nothing in this task is code. Each step changes GitHub, so ask the maintainer before running it, show the exact command, and report the result.

- [ ] **Step 1: Open the PR.** Title: `ci: release with release-please from a release PR`. The body lists the docs changed and ends with the attribution line. Expected: CI's `python` and `install-check` pass, and `pr-title` passes on its own title.
- [ ] **Step 2: The maintainer creates the App and stores the variable and secret,** following `docs/releasing.md` "One-time setup". Check with `gh variable list` and `gh secret list`: expect `RELEASE_APP_CLIENT_ID` and `RELEASE_APP_PRIVATE_KEY`.
- [ ] **Step 3: The maintainer merges the PR.** On `main`, the `Release PR` run succeeds and opens no release PR: there are no `feat`/`fix` commits since v0.1.0.
- [ ] **Step 4: Merge settings and branch protection,** using the commands from `docs/releasing.md`. Check: `gh api repos/prumihq/prumi --jq '{allow_merge_commit,allow_rebase_merge,squash_merge_commit_title,squash_merge_commit_message,delete_branch_on_merge}'` shows `false,false,"PR_TITLE","BLANK",true`, and the protection's `contexts` include `pr-title`.
- [ ] **Step 5: Update the v0.1.0 GitHub Release text** to the rewritten 0.1.0 entry (without its heading line): `gh release edit v0.1.0 --notes-file <that entry>`. Don't touch the tag.
- [ ] **Step 6: Hand over.** Tell the maintainer three things:
  - The tag ruleset and immutable releases wait for the first release under the new process, as `docs/releasing.md` says.
  - That first release is also the check that the App-made tag starts `release.yml` exactly once. Then give them the short guide to starting issues and milestones in a new session, which they asked for.
