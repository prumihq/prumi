# Releasing

The maintainer's checklist for publishing `prumi` and `prumi-adapter-dbt` on PyPI. Both packages are released together, always with the same version.

The release workflow, [`.github/workflows/release.yml`](../.github/workflows/release.yml), does the work. On a pushed tag `v<version>` it checks that the tag matches both versions and that `CHANGELOG.md` dates that version, runs the checks (ruff, the full suite with browser tests), builds a wheel and an sdist of each package, runs the install check on those files ([`scripts/check_install.py`](../scripts/check_install.py)) and uploads them, each package from a job of its own. A manual run does everything except the uploads.

## Once

**On PyPI**, add a pending publisher for each package (your account → Publishing → Add a new pending publisher), so the first upload can create the projects. Each package has its own GitHub environment, because PyPI refuses a second pending publisher with the same owner, repository, workflow and environment:

| Field | `prumi` | `prumi-adapter-dbt` |
|---|---|---|
| PyPI project name | `prumi` | `prumi-adapter-dbt` |
| Owner | `prumihq` | `prumihq` |
| Repository name | `prumi` | `prumi` |
| Workflow name | `release.yml` | `release.yml` |
| Environment name | `pypi` | `pypi-adapter-dbt` |

A later first-party adapter gets its own environment the same way, `pypi-adapter-<source>`.

**On GitHub**, create both environments, `pypi` and `pypi-adapter-dbt` (the repository's Settings → Environments → New environment), and add yourself as a required reviewer of each. Each package's upload job runs in its environment, so nothing uploads until you approve it. GitHub offers required reviewers on public repositories.

No token or password is stored anywhere: PyPI trusts the workflow running in each package's environment, for that package only (trusted publishing).

## The first release

1. Make the repository public. A package carries the full source, so nothing is uploaded before the repository is public.
2. Turn on private vulnerability reporting (Settings → Code security → Private vulnerability reporting), which [`SECURITY.md`](../SECURITY.md) tells people to use.
3. Then follow "Each release". The README's PyPI commands, such as `uv tool install "prumi[dbt]"`, work once both uploads finish, a few minutes after you approve them.

## Each release

1. **Bump both versions.** The version is in both `pyproject.toml` files (`packages/prumi-core` and `packages/prumi-adapter-dbt`), both `__version__` strings, `packages/prumi-runtime/package.json` and `VERSION` in `packages/prumi-core/tests/test_release.py`, which checks that they all agree. For a new minor version, also raise the range each package requires of the other (`prumi>=0.1.0,<0.2` in the adapter, and the `dbt` extra), which the same test file checks. Then run `uv lock`.
2. **Date the CHANGELOG heading:** `## 0.1.0 — unreleased` becomes `## 0.1.0 — 2026-10-20`, with the day you release.
3. **Try the manual run.** Push to `main`, then Actions → Release → Run workflow. It runs the same checks, build and install check as a tag, with the version in the files, and stops before the uploads. To run the install check on your machine instead: `uv run python scripts/check_install.py`.
4. **Tag and push:**

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

5. **Approve both deployments** when the run asks for them: on the run's page, Review deployments lists `pypi` and `pypi-adapter-dbt`; select both and approve, in one dialog. The uploads start then.
6. **Check both PyPI pages**, [pypi.org/project/prumi](https://pypi.org/project/prumi/) and [pypi.org/project/prumi-adapter-dbt](https://pypi.org/project/prumi-adapter-dbt/), and a fresh install: `uv tool install "prumi[dbt]"` (add `--force` if Prumi is installed already), `prumi install-chromium`, then `prumi serve` on a project.

If the run stops before the uploads, nothing was uploaded. Fix the problem on `main`, delete the tag (`git push --delete origin v0.1.0` and `git tag -d v0.1.0`) and tag again.

## What can't be undone

- **A version uploads once.** PyPI never accepts the same file name twice, even after a release is deleted. A mistake in a release is fixed by the next patch version (0.1.1), not by uploading again.
- **If an upload stops halfway** (one package is on PyPI and the other isn't, or a package has only one of its two files there), rerun the failed jobs (the run's page → Re-run failed jobs) and approve their deployments again. Each skips the files already on PyPI and uploads the rest.
