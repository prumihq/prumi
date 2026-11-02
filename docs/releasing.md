# Releasing

The maintainer's manual for publishing `prumi` and `prumi-adapter-dbt` on PyPI. Both packages are released together, always with the same version, and stay 0.x.

Two workflows do the work:

- [`.github/workflows/release-please.yml`](../.github/workflows/release-please.yml) runs on every push to `main`. It keeps one pull request open, the **release PR** (`chore(main): release X.Y.Z`; release-please puts the target branch in the scope), which bumps every version and adds the next entry to `CHANGELOG.md`. The entry is built from the titles of the pull requests merged since the last release: `feat` titles under Features, `fix` under Bug Fixes, `perf` and `revert` in their own sections, every other type left out. `feat` makes the next version a minor one (0.1.0 → 0.2.0), `fix` and `perf` a patch (0.1.0 → 0.1.1); while Prumi is 0.x, a breaking change (`feat!:`) also gives a minor one. Merging the release PR creates the tag `v<version>` and a **draft** GitHub Release.
- [`.github/workflows/release.yml`](../.github/workflows/release.yml) runs on that tag. It checks that the tag matches all three versions (both `__init__.py` files and `packages/prumi-runtime/package.json`), that the version is below 1.0 and that `CHANGELOG.md` has a dated heading for it ([`scripts/check_release.py`](../scripts/check_release.py)). Then it runs the checks (ruff, the full suite with browser tests), builds a wheel and an sdist of each package, runs the install check on those files ([`scripts/check_install.py`](../scripts/check_install.py)) and uploads them, each package from a job of its own, after you approve. Last, it publishes the draft release. A manual run (Actions → Release → Run workflow) does everything except the uploads and the publishing.

What release-please bumps is listed in [`release-please-config.json`](../release-please-config.json); [`.release-please-manifest.json`](../.release-please-manifest.json) holds the current version. Never edit either by hand to make a release.

## 1. How the repository is set up

Everything below lives in GitHub's or PyPI's settings, not in git, so this list is the record of what's set and why. All of it was set up on 2026-11-02 and has worked through a release (0.1.1).

| Setting | Where | Why |
|---|---|---|
| Trusted publishers for `prumi` (environment `pypi`) and `prumi-adapter-dbt` (environment `pypi-adapter-dbt`), both on workflow `release.yml` | PyPI → each project → Publishing | PyPI trusts that workflow in that environment, for that package only, so no token is stored anywhere. PyPI refuses two publishers with the same owner, repository, workflow and environment, hence one environment per package. |
| Environments `pypi` and `pypi-adapter-dbt`, with you as required reviewer | Settings → Environments | Nothing uploads until you approve. |
| The App `prumi-release`, installed on `prumi` only: Contents, Pull requests and Issues read and write, webhook off | `prumihq` → Settings → Developer settings → GitHub Apps | release-please acts with its token. GitHub starts no workflows for events made with the workflow's own `GITHUB_TOKEN`, so without the App CI wouldn't run on the release PR and the tag wouldn't start `release.yml`. Issues is for the `autorelease:` labels. |
| Variable `RELEASE_APP_CLIENT_ID`, secret `RELEASE_APP_PRIVATE_KEY` | Settings → Secrets and variables → Actions | The App's client ID and private key, for `release-please.yml`. |
| Squash merging only, commit title = PR title, body blank, head branches deleted on merge | Settings → General → Pull Requests | One PR, one commit on `main`, one changelog line. The body stays blank because release-please reads Conventional Commits in it too: stray `fix:` lines from branch commits would become changelog lines. |
| Branch protection on `main`: a pull request (0 approvals), checks `python`, `install-check` and `pr-title`, linear history, admins included, no force pushes or deletion | Settings → Branches | `pr-title` keeps every title readable by release-please. `pr-title` runs the workflow file from the PR's own branch, so review any change under `.github/workflows/` and read the title yourself before merging. |
| Ruleset "Version tags" on `refs/tags/v*`: no creation, update, deletion or force push, except repository admins and `prumi-release` | Settings → Rules → Rulesets | Only a release, or you, can make or remove a version tag. |
| Immutable releases | Settings → General → Releases | A published release's tag and files can't change. `release.yml` publishes the draft only after both uploads, so that's when it locks. |
| Dependabot for `github-actions` ([`.github/dependabot.yml`](../.github/dependabot.yml)) | in git | Every action is pinned to a commit SHA with its version in a comment; a tag can be moved by whoever controls the action, a commit can't. Dependabot opens one `ci(deps):` pull request a week with the updates, which never makes a release. |

Check the protection with `gh api repos/prumihq/prumi/branches/main/protection` and the ruleset with `gh api repos/prumihq/prumi/rulesets`.

**Rotating the App's key** (if it leaks, or as a habit): on the App's page, Private keys → Generate a private key, store it with `gh secret set RELEASE_APP_PRIVATE_KEY --repo prumihq/prumi < prumi-release.*.private-key.pem`, delete the old key on the same page, and delete the `.pem` file from your machine.

**A new first-party adapter package** gets its own environment, `pypi-adapter-<source>`, with you as required reviewer, a pending trusted publisher on PyPI with that environment, and its own upload job in `release.yml`. Don't move the uploads into a reusable workflow (`workflow_call`): PyPI doesn't accept a reusable workflow as a trusted publisher.

## 2. Each release

1. **Open the release PR.** After any `feat`, `fix`, `perf` or `revert` pull request merges, release-please opens or updates the pull request `chore(main): release X.Y.Z` (Pull requests tab). It changes `CHANGELOG.md`, `.release-please-manifest.json`, both `__init__.py` files, the version ranges in both `pyproject.toml` files and `packages/prumi-runtime/package.json`. CI runs on it like on any pull request.
2. **Read the changelog entry** in the PR's Files changed, as a user would. If a line's wording is wrong, the lasting fix is the one in section 3: it corrects the merged PR the line came from, and release-please rewrites the release PR. To fix only this release's text instead, right before merging edit both the entry in `CHANGELOG.md` on the release PR's branch and the same text in the release PR's description (the description becomes the GitHub Release's notes). Do it last: every push to `main` regenerates the release PR and drops such edits.
3. **Merge it** (Squash and merge). release-please then creates the tag `v<version>` and a draft GitHub Release, and the tag starts the `Release` workflow.
4. **Approve both deployments.** On the `Release` run's page (Actions → Release: the run is titled with the merge commit, `chore(main): release X.Y.Z (#N)`, and shows the tag `vX.Y.Z` as its ref), once the checks and the build pass, Review deployments lists `pypi` and `pypi-adapter-dbt`; select both and approve, in one dialog. The uploads start then.
5. **Check the result:**
   - The GitHub Release `v<version>` turned from draft to published and is marked Latest (the Releases page). The run's last job, `publish-release`, does that once both uploads finish.
   - Both PyPI pages, [pypi.org/project/prumi](https://pypi.org/project/prumi/) and [pypi.org/project/prumi-adapter-dbt](https://pypi.org/project/prumi-adapter-dbt/), show the new version.
   - A fresh install works: `uv tool install "prumi[dbt]"` (add `--force` if Prumi is installed already), `prumi install-chromium`, then `prumi serve` on a project.

## 3. Fixing a changelog line after merge

A changelog line is the title of a merged pull request. If one came out wrong (a typo, the wrong type, a line that should be hidden), fix it in the **merged** pull request, not in the release PR: edit that PR's description and add, anywhere in it, a block with the title you want:

```
BEGIN_COMMIT_OVERRIDE
fix: stop the home page losing its filter
END_COMMIT_OVERRIDE
```

release-please reads the block instead of the squash commit's message. One merged PR can stand for several lines, one per line in the block:

```
BEGIN_COMMIT_OVERRIDE
feat: add the X tool
fix: stop the home page losing its filter
END_COMMIT_OVERRIDE
```

To drop the line from the changelog, use a hidden type, such as `chore: ...`.

The release PR changes the next time release-please runs, which is on the next push to `main`. To see it sooner, rerun the latest `Release PR` workflow run (Actions → Release PR → the latest run → Re-run all jobs). This works only for PRs merged since the last release; for a version already published, edit its notes on the Releases page and its entry in `CHANGELOG.md` through a `docs:` pull request.

## 4. When something fails

**Before you approve the deployments:** if they are waiting for you, reject them (Review deployments → Reject). Nothing is uploaded, and the release stays a draft that nobody else sees. Find the cause in the run's log. If it was a passing hiccup (a network error, a flaky test), rerun the failed jobs on the same run (Re-run failed jobs); nothing needs cleaning up. Otherwise, clean up and retry as below.

**An upload that stopped halfway** (one package is on PyPI and the other isn't, or a package has only one of its two files there): rerun the failed jobs (the run's page → Re-run failed jobs) and approve their deployments again. Each skips the files already on PyPI and uploads the rest. Then `publish-release` publishes the draft.

**A release that stopped before the uploads** (the checks or the build failed, or you rejected the deployments): it can still be cleaned up too. Immutability starts only when a release is published, and this one is still a draft. The tag ruleset blocks deleting and creating `v*` tags, but you bypass it as a repository admin, so the commands below work for you, not for other contributors. Recommended: follow the steps below and release the same version, so the changelog never lists a version that isn't on PyPI. Leaving the draft and the tag, and letting the next release supersede them, also works, but leaves a draft, a tag and a changelog entry for a version nobody can install.

**Never merge to `main` while a version's tag is missing.** release-please finds the last release by its tag and its GitHub Release. If both are gone when it runs, it falls back to the version in `.release-please-manifest.json`, has nothing to stop at, reads back through the whole history and opens a release PR for the next version that repeats every line of the failed one. So the steps below keep the draft and the tag until the moment they are recreated, and delete and recreate them in one sitting. If such a release PR appears anyway, don't merge it: close it, recreate the tag and the draft as below, and release-please opens a correct one with the next `feat` or `fix`.

Fix the cause, then follow the case that matches it:

- **The fix is in a setting, not in the repository** (an environment, the App, a secret). Fix the setting first. Then, one step right after the other:

  1. Delete the draft and its tag:

     ```sh
     gh release delete v0.2.0 --repo prumihq/prumi --cleanup-tag --yes
     git push --delete origin v0.2.0   # only if the tag is still there
     git tag -d v0.2.0                 # only if you fetched it
     ```

  2. On the merged release PR, replace the label `autorelease: tagged` with `autorelease: pending`.
  3. Rerun the latest `Release PR` run (Actions → Release PR → the latest run → Re-run all jobs). release-please creates the tag and the draft again, on the same commit, with the same notes, and `Release` starts.

- **The fix is a change to the repository,** such as `release.yml`. It has to be in the tagged commit, so the tag moves to the commit that fixes it.

  1. **Keep the draft and the tag for now.** Merge the fix to `main` through a pull request as usual. Give it a type that stays out of the changelog (`ci:` for a workflow, `build:` or `chore:` otherwise), so it doesn't open a release PR of its own. With the tag and the draft still there, release-please treats the version as released, so the next release PR starts after it.
  2. Once the fix is on `main`, save the draft's notes, delete the draft and its tag, and tag the new `main`:

     ```sh
     gh release view v0.2.0 --repo prumihq/prumi --json body --jq .body > notes.md
     gh release delete v0.2.0 --repo prumihq/prumi --cleanup-tag --yes
     git push --delete origin v0.2.0   # only if the tag is still there
     git tag -d v0.2.0                 # only if you have it locally
     git fetch origin
     git tag v0.2.0 origin/main
     git push origin v0.2.0
     ```

     Pushing the tag starts `Release`, on the fixed commit.
  3. Right away, create the draft again with the saved notes:

     ```sh
     gh release create v0.2.0 --repo prumihq/prumi --draft --verify-tag --title v0.2.0 --notes-file notes.md
     ```

     This is the order release-please uses too: the tag first, then the draft on it. `--verify-tag` makes `gh` stop if the tag isn't on GitHub, instead of creating a release for a tag of its own at the latest `main`. The draft has to exist before `publish-release` runs, which is only after you approve the uploads, so create it before approving. `CHANGELOG.md` on `main` already has the version's heading, so the tag check passes.

The examples use `v0.2.0`; use the version of the failed release.

**Some things can't be undone:**

- A published release's tag can't be moved or deleted while the release exists.
- If a published release is deleted, its tag name can't be used again.
- PyPI never accepts the same file name twice, even after a release is deleted there.

So a mistake in a published release is fixed by the next patch version (0.2.1, from a `fix` pull request and its release PR), never by uploading again.
