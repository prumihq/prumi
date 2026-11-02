# Releasing

The maintainer's manual for publishing `prumi` and `prumi-adapter-dbt` on PyPI. Both packages are released together, always with the same version, and stay 0.x.

Two workflows do the work:

- [`.github/workflows/release-please.yml`](../.github/workflows/release-please.yml) runs on every push to `main`. It keeps one pull request open, the **release PR** (`chore: release X.Y.Z`), which bumps every version and adds the next entry to `CHANGELOG.md`. The entry is built from the titles of the pull requests merged since the last release: `feat` titles under Features, `fix` under Bug Fixes, `perf` and `revert` in their own sections, every other type left out. `feat` makes the next version a minor one (0.1.0 → 0.2.0), `fix` and `perf` a patch (0.1.0 → 0.1.1); while Prumi is 0.x, a breaking change (`feat!:`) also gives a minor one. Merging the release PR creates the tag `v<version>` and a **draft** GitHub Release.
- [`.github/workflows/release.yml`](../.github/workflows/release.yml) runs on that tag. It checks that the tag matches all three versions (both `__init__.py` files and `packages/prumi-runtime/package.json`), that the version is below 1.0 and that `CHANGELOG.md` has a dated heading for it ([`scripts/check_release.py`](../scripts/check_release.py)). Then it runs the checks (ruff, the full suite with browser tests), builds a wheel and an sdist of each package, runs the install check on those files ([`scripts/check_install.py`](../scripts/check_install.py)) and uploads them, each package from a job of its own, after you approve. Last, it publishes the draft release. A manual run (Actions → Release → Run workflow) does everything except the uploads and the publishing.

What release-please bumps is listed in [`release-please-config.json`](../release-please-config.json); [`.release-please-manifest.json`](../.release-please-manifest.json) holds the current version. Never edit either by hand to make a release.

## 1. One-time setup

Do these in order. Each step says why it comes where it does.

### PyPI publishers and the two environments

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

### The release App

release-please acts with a GitHub App's token, not the workflow's own `GITHUB_TOKEN`. GitHub starts no workflows for events made with `GITHUB_TOKEN`: CI wouldn't run on the release PR, and the tag wouldn't start `release.yml`. Until the App is set up, the `Release PR` workflow skips.

1. **Create the App** in the organization: the `prumihq` organization's Settings → Developer settings → GitHub Apps → New GitHub App.
   - GitHub App name: `prumi-release`.
   - Homepage URL: `https://github.com/prumihq/prumi`.
   - Webhook: clear **Active**. The App never receives events; the workflow only borrows its token.
   - Repository permissions: **Contents** Read and write (to create the tag and the release), **Pull requests** Read and write (to open and update the release PR) and **Issues** Read and write (release-please marks the release PR with its `autorelease: pending` and `autorelease: tagged` labels, and GitHub handles labels through the Issues API). These are the permissions release-please-action documents. Leave everything else at No access; Metadata read-only is added on its own.
   - Where can this GitHub App be installed: Only on this account.
   - Create GitHub App.
2. **Note its two IDs.** The App's settings page (General) shows the **App ID**, a number, which the tag ruleset needs later, and the **Client ID**, which starts with `Iv`. You can also read the App ID with `gh api apps/prumi-release --jq .id`.
3. **Generate a private key** on the same page (Private keys → Generate a private key). It downloads a `.pem` file.
4. **Install it on `prumi` only:** Install App (left menu) → Install next to `prumihq` → Only select repositories → `prumi` → Install.
5. **Store the client ID and the key in the repository** (Settings → Secrets and variables → Actions): the client ID as the repository **variable** `RELEASE_APP_CLIENT_ID` (Variables tab), the whole `.pem` file as the repository **secret** `RELEASE_APP_PRIVATE_KEY` (Secrets tab). Or from a clone:

   ```sh
   gh variable set RELEASE_APP_CLIENT_ID --repo prumihq/prumi --body "Iv23li..."
   gh secret set RELEASE_APP_PRIVATE_KEY --repo prumihq/prumi < prumi-release.*.private-key.pem
   ```

   Then delete the `.pem` file from your machine. If the key leaks, delete it on the App's page and generate a new one.

### Merge settings

Do this after the release workflows are on `main`. Pull requests are then merged as one squash commit whose message is the PR title alone, which is what release-please reads: one PR, one commit, one changelog line.

```sh
gh api -X PATCH repos/prumihq/prumi \
  -F allow_merge_commit=false \
  -F allow_rebase_merge=false \
  -F allow_squash_merge=true \
  -f squash_merge_commit_title=PR_TITLE \
  -f squash_merge_commit_message=BLANK \
  -F delete_branch_on_merge=true
```

In the web UI: Settings → General → Pull Requests. Clear "Allow merge commits" and "Allow rebase merging", keep "Allow squash merging" with the default message "Pull request title", and select "Automatically delete head branches".

### Branch protection on `main`

`main` uses classic branch protection (Settings → Branches), which already requires a pull request with 0 approvals and the checks `python` and `install-check`, for admins too, and blocks force pushes and deletion. Two additions:

1. **Require the PR title check, `pr-title`,** so a pull request can't merge with a title release-please can't read. Do this after `.github/workflows/pr-title.yml` is on `main` and has run on a pull request; a required check that nothing reports blocks every merge.

   ```sh
   gh api -X POST repos/prumihq/prumi/branches/main/protection/required_status_checks/contexts \
     -f 'contexts[]=pr-title'
   ```

2. **Require linear history,** so `main` only ever gets squash commits. Do this after the merge settings above, so that squash, the method linear history allows, is the only one left on the merge button. The API can only change it by sending the whole protection again, so the web UI is simpler: Settings → Branches → Edit next to `main` → select **Require linear history** → Save changes.

   The same with `gh api`, which replaces the whole protection and therefore restates everything `main` already has:

   ```sh
   gh api -X PUT repos/prumihq/prumi/branches/main/protection --input - <<'EOF'
   {
     "required_status_checks": {
       "strict": false,
       "checks": [
         {"context": "python", "app_id": 15368},
         {"context": "install-check", "app_id": 15368},
         {"context": "pr-title", "app_id": 15368}
       ]
     },
     "enforce_admins": true,
     "required_pull_request_reviews": {"required_approving_review_count": 0},
     "restrictions": null,
     "required_linear_history": true,
     "allow_force_pushes": false,
     "allow_deletions": false
   }
   EOF
   ```

   `15368` is the GitHub Actions app, which reports the checks. Before running it, compare with what `main` has now (`gh api repos/prumihq/prumi/branches/main/protection`); anything that differs and isn't in the body above would be dropped.

Check the result with `gh api repos/prumihq/prumi/branches/main/protection --jq '{checks: .required_status_checks.contexts, linear: .required_linear_history.enabled}'`.

### After the first release has worked end to end

Two protections come last, on purpose. Until then, a misfire on the first release can still be cleaned up by deleting the draft release and the tag (section 4). Once they're on, a tag can't be deleted by mistake, and neither can a published release's.

1. **A tag ruleset for `refs/tags/v*`:** only repository admins (you) and the release App can create, move or delete a version tag, and nobody can force-push one. Replace `APP_ID` with the App ID from "The release App" (`gh api apps/prumi-release --jq .id`); `5` is GitHub's id for the repository admin role.

   ```sh
   gh api -X POST repos/prumihq/prumi/rulesets --input - <<'EOF'
   {
     "name": "Version tags",
     "target": "tag",
     "enforcement": "active",
     "conditions": {
       "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
     },
     "rules": [
       {"type": "creation"},
       {"type": "update"},
       {"type": "deletion"},
       {"type": "non_fast_forward"}
     ],
     "bypass_actors": [
       {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},
       {"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}
     ]
   }
   EOF
   ```

   Then open Settings → Rules → Rulesets → Version tags and check that the bypass list shows "Repository admin" and `prumi-release`. The same ruleset can be made there by hand (New ruleset → New tag ruleset).

2. **Immutable releases:** Settings → General → Releases → select **Enable release immutability**. From then on, a published release's tag and files can't change. It applies to releases published after you turn it on, which is why `release.yml` publishes the draft only once both uploads are done.

## 2. Each release

1. **Open the release PR.** After any `feat`, `fix`, `perf` or `revert` pull request merges, release-please opens or updates the pull request `chore: release X.Y.Z` (Pull requests tab). It changes `CHANGELOG.md`, `.release-please-manifest.json`, both `__init__.py` files, the version ranges in both `pyproject.toml` files and `packages/prumi-runtime/package.json`. CI runs on it like on any pull request.
2. **Read the changelog entry** in the PR's Files changed, as a user would. If a line's wording is wrong, the lasting fix is the one in section 3: it corrects the merged PR the line came from, and release-please rewrites the release PR. To fix only this release's text instead, right before merging edit both the entry in `CHANGELOG.md` on the release PR's branch and the same text in the release PR's description (the description becomes the GitHub Release's notes). Do it last: every push to `main` regenerates the release PR and drops such edits.
3. **Merge it** (Squash and merge). release-please then creates the tag `v<version>` and a draft GitHub Release, and the tag starts the `Release` workflow.
4. **Approve both deployments.** On the `Release` run's page (Actions → Release, the run named after the tag), once the checks and the build pass, Review deployments lists `pypi` and `pypi-adapter-dbt`; select both and approve, in one dialog. The uploads start then.
5. **Check the result:**
   - The GitHub Release `v<version>` turned from draft to published and is marked Latest (the Releases page). The run's last job, `publish-release`, does that once both uploads finish.
   - Both PyPI pages, [pypi.org/project/prumi](https://pypi.org/project/prumi/) and [pypi.org/project/prumi-adapter-dbt](https://pypi.org/project/prumi-adapter-dbt/), show the new version.
   - A fresh install works: `uv tool install "prumi[dbt]"` (add `--force` if Prumi is installed already), `prumi install-chromium`, then `prumi serve` on a project.

**The first release under this process only:** check that merging the release PR started exactly one `Release` run, from the tag `v<version>` (Actions → Release). If none started, or two did, reject any deployments waiting for approval, clean up as in section 4, and fix the trigger in `release.yml` so that merging starts it exactly once. Changing the trigger doesn't touch the PyPI trust: the trusted publishers depend only on the file name `release.yml` and the environments. Don't move the uploads into a reusable workflow (`workflow_call`): PyPI doesn't accept a reusable workflow as a trusted publisher. Once one release has gone through end to end, do the setup in "After the first release has worked end to end".

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

**Before you approve the deployments:** if they are waiting for you, reject them (Review deployments → Reject). Nothing is uploaded, and the release stays a draft that nobody else sees. Find the cause in the run's log. If it was a passing hiccup (a network error, a flaky test), rerun the failed jobs on the same run (Re-run failed jobs); nothing needs cleaning up. Otherwise, clean up and retry as below: the first release and later ones differ only in the tag ruleset.

**An upload that stopped halfway** (one package is on PyPI and the other isn't, or a package has only one of its two files there): rerun the failed jobs (the run's page → Re-run failed jobs) and approve their deployments again. Each skips the files already on PyPI and uploads the rest. Then `publish-release` publishes the draft.

**The first release, before the tag ruleset and immutable releases are on:** nothing published can't be cleaned up yet. Follow the steps below.

**A later release that stopped before the uploads** (the checks or the build failed, or you rejected the deployments): it can still be cleaned up too. Immutability starts only when a release is published, and this one is still a draft. The tag ruleset blocks deleting and creating `v*` tags, but you bypass it as a repository admin, so the commands below work for you, not for other contributors. Recommended: follow the steps below and release the same version, so the changelog never lists a version that isn't on PyPI. Leaving the draft and the tag, and letting the next release supersede them, also works, but leaves a draft, a tag and a changelog entry for a version nobody can install.

1. Copy the draft release's notes somewhere (Releases → the draft → Edit), then delete the draft and its tag:

   ```sh
   gh release delete v0.2.0 --repo prumihq/prumi --cleanup-tag --yes
   git push --delete origin v0.2.0   # only if the tag is still there
   git tag -d v0.2.0                 # only if you fetched it
   ```

2. Fix the cause. Then one of:
   - **The fix is in a setting, not in the repository** (an environment, the App, a secret): on the merged release PR, replace the label `autorelease: tagged` with `autorelease: pending`, then rerun the latest `Release PR` run (Actions → Release PR → Re-run all jobs). release-please creates the tag and the draft again, on the same commit, and `Release` starts.
   - **The fix is a change to the repository,** such as `release.yml`: it has to be in the tagged commit, so merge it to `main` first. Then either merge the next release PR when one comes (the unreleased version is simply skipped; it never reached PyPI), or release the same version from the fixed `main` by hand: create the draft with the notes you copied (`gh release create v0.2.0 --repo prumihq/prumi --draft --title v0.2.0 --notes-file notes.md`), then tag and push (`git fetch origin && git tag v0.2.0 origin/main && git push origin v0.2.0`). `CHANGELOG.md` on `main` already has the version's heading, so the tag check passes.

The examples use `v0.2.0`; use the version of the failed release.

**After immutable releases are on, some things can't be undone:**

- A published release's tag can't be moved or deleted while the release exists.
- If a published release is deleted, its tag name can't be used again.
- PyPI never accepts the same file name twice, even after a release is deleted there.

So a mistake in a published release is fixed by the next patch version (0.2.1, from a `fix` pull request and its release PR), never by uploading again.
