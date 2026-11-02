# 0015. Releases come from a release-please release PR

Status: Proposed · 2026-11-01

## Context

[ADR 0014](0014-package-names-on-pypi.md) releases both first-party packages together, with one version, uploaded from GitHub Actions on a version tag. Until now the maintainer made each release by hand: bump the version in six places, date the changelog heading, push to `main`, tag. Branch protection now blocks pushes to `main`, and the changelog was written by hand, which doesn't scale once outside contributors send pull requests.

GitHub starts no workflows for events made with the workflow's own `GITHUB_TOKEN`. PyPI's trusted publishing trusts a workflow by its file name and environment, and doesn't accept a reusable workflow. Published GitHub Releases can be made immutable.

## Decision

- **Releases come from a release PR kept open by release-please.** Pull requests are squash-merged with their title as the commit message, and titles follow Conventional Commits, checked on every pull request. release-please reads those commits, bumps every version, writes the changelog entry from the titles, and merging its pull request creates the tag `v<version>`.
- **release-please acts with a GitHub App's token** (`prumi-release`, Contents and Pull requests only, installed on this repository only), so CI runs on the release PR and the tag starts `release.yml`.
- **The GitHub Release is created as a draft and published by `release.yml` after both uploads,** so the Releases page never shows a version PyPI doesn't have, and the release becomes immutable only once it's complete.
- **Each package's version is dynamic, read from its `__init__.py`** (hatchling), so a bump doesn't change `uv.lock` and the release PR needs no relock.

Considered: a hand-run bump script (rejected: a manual step per release, and a hand-curated changelog doesn't scale to outside contributors). Static versions plus a job that relocks on the release PR (rejected: an extra job that reruns on every update of the release PR). A version from git tags (rejected: friction with `uv.lock`, with sdists built outside a checkout, and with the `~=` ranges each package requires of the other). Starting the uploads with `workflow_call` from release-please's workflow (rejected: PyPI doesn't accept a reusable workflow as a trusted publisher).

## Consequences

- The tag still starts `release.yml`, so ADR 0014's "uploaded on a version tag" still holds, and the trusted publishers didn't change.
- Contributors must give pull requests a Conventional Commits title; the title is what users read in the changelog.
- The tag check reads the versions from the two `__init__.py` files and `package.json`, and refuses 1.0 or above.
- The maintainer no longer edits versions or the changelog; a wrong line is fixed with release-please's commit override in the merged pull request.
- The release depends on an App and its private key, stored as a repository secret.
