# Release workflow: release-please, one version source, protected tags

Status: approved by the maintainer on 2026-11-01.

## Why

Prumi is about to be announced. The maintainer wants it to follow common open-source practice, so people can rely on it and contributors know how to take part. The release process built for 0.1.0 doesn't do that:

- The version is written in six places and kept in step by a test.
- `CHANGELOG.md` is written by hand at release time and has no section for unreleased changes.
- `docs/releasing.md` is a manual checklist. Its step "push to `main`" is now blocked by branch protection.
- `v*` tags aren't protected, and published releases can be changed.

Anything about how the repo works today may change: the commit style, the changelog format, the rules in `AGENTS.md`. Only the parts listed under "Must stay" are fixed.

## Goals

1. **Releases come from a release PR.** release-please keeps one PR open. It holds the next version, the version bump in every file and the new changelog section. The maintainer decides when to release by merging it.
2. **Conventional Commits, on PR titles.** `main` only takes squash merges. Each PR's title is the commit on `main` and drives both the version bump and the changelog line. A check on every PR enforces the title format, and a PR template explains it.
3. **One version source per package.** Each Python package's version lives only in its `__init__.py`. `pyproject.toml` reads it from there (a dynamic version). A version bump doesn't change `uv.lock`.
4. **Merging the release PR releases.** It creates the tag `v<version>` and the GitHub Release, and starts `release.yml`. The maintainer approves each PyPI upload, as today.
5. **Tags and releases are protected.** Only the maintainer and the release App can create, move or delete `v*` tags. Published releases are immutable.
6. **Docs explain the new process.** `docs/releasing.md` becomes the maintainer's manual. `CONTRIBUTING.md` gains a short "How releases work" section. `AGENTS.md` gets the new rules.

## Must stay

- `release.yml` starts from the tag. It runs the tag check, ruff, the full test suite with browser tests, the build, and the install check (`scripts/check_install.py`) on the built files, then has one upload job per package.
- Trusted publishing to PyPI, with no stored token. The two existing pending/active publishers name `release.yml` and the environments `pypi` and `pypi-adapter-dbt`. They must keep working without being re-registered.
- The maintainer's approval in the `pypi` and `pypi-adapter-dbt` environments before any upload.
- `prumi`, `prumi-adapter-dbt` and `prumi-runtime` are always released together, with the same version.
- Releases stay 0.x. Nothing in this work may produce 1.0.
- The manual run of `release.yml` (`workflow_dispatch`) stays as a dry run that does everything except the uploads.

## Non-goals

- **Issues, labels, milestones, issue templates and the policy on whether work must start from an issue.** These are a separate, later piece of work. This design only covers the PR template, because the PR title convention is part of releasing.
- Publishing `prumi-runtime` to npm. It stays `"private": true`, and only its version number is bumped.
- TestPyPI, pre-releases, release candidates.
- Changing the version: it stays 0.1.0 in this work. The first release under the new process is whatever release-please proposes next.
- Attaching built files to the GitHub Release. PyPI is where packages come from.

## Anti-goals (a failure even if it technically works)

- Anything that can upload to PyPI without the maintainer's approval in the environment.
- A bot or token with broader rights than it needs, or one tied to the maintainer's personal account.
- A release PR that CI doesn't run on, or that can't be merged because required checks never report.
- Versions that can drift apart between the three packages, or between a tag and the packages it builds.
- A GitHub Release page that says a version is out while PyPI doesn't have it.
- Agents creating tags, bumping versions or editing the release PR's version lines.

## The design

### Everyday flow

- Work happens on branches, as today. A PR's title follows Conventional Commits: `type: description`, optionally `type(scope): description`. A `!` after the type marks a breaking change.
- Squash merge is the only merge method on `main`. The squash commit's title is the PR title and its **body is blank**. release-please reads the whole commit message, so a body made of the branch's commits or the PR template would leak `fix: …` lines and template text into the version bump and the changelog.
  - A breaking change is marked only by `!` in the title.
  - `Closes #N` in the PR description still closes issues, because GitHub reads that from the PR, not from the commit.
- **PR title check.** A workflow runs on `pull_request` (opened, edited, synchronize, reopened) and fails when the title doesn't follow the format. It's a required status check on `main`.
- **PR template.** `.github/pull_request_template.md` explains the title format in two or three lines, with examples. It has room for what changed and why, and a short checklist: tests, the docs `AGENTS.md` lists.

### The release PR (release-please)

- **A new workflow, `.github/workflows/release-please.yml`,** runs on every push to `main`. It uses `googleapis/release-please-action`, pinned to an exact version, with a token from the release App (below), not `GITHUB_TOKEN`.
  - Why the App token: GitHub doesn't start workflows for events made with `GITHUB_TOKEN`. CI wouldn't run on the release PR, and the tag wouldn't start `release.yml`.
- **One component for the whole repo.** All three packages share one version, one tag (`v<version>`, no component name in it) and one `CHANGELOG.md` at the root. This is the simplest way to get lockstep versions.
  - Configured in `release-please-config.json` and `.release-please-manifest.json`. The manifest starts at `0.1.0`.
- **0.x bumps:**
  - a breaking change bumps the minor version (`bump-minor-pre-major`)
  - `feat` bumps the minor version
  - `fix` and `perf` bump the patch version
  - other types alone don't open a release PR
- **Files the release PR updates:**
  - `packages/prumi-core/src/prumi/__init__.py`: `__version__`
  - `packages/prumi-adapter-dbt/src/prumi_adapter_dbt/__init__.py`: `__version__`
  - `packages/prumi-runtime/package.json`: `version`
  - `packages/prumi-adapter-dbt/pyproject.toml`: the dependency on `prumi`, written as `prumi~=X.Y.Z`
  - `packages/prumi-core/pyproject.toml`: the `dbt` extra, written as `prumi-adapter-dbt[duckdb]~=X.Y.Z`
  - `CHANGELOG.md`: a new section at the top
  - `.release-please-manifest.json`

  The ranges are written `~=X.Y.Z` with the full current version, because release-please's `x-release-please-version` marker writes the whole version. `prumi~=0.2.1` means `>=0.2.1,<0.3`. That's slightly stricter than `>=0.2.0`, but correct because the packages are always released together. Python files get the generic `x-release-please-version` marker, and `package.json` uses the JSON updater.
- **The release PR must not create extra version files** (such as `version.txt`, which some release-please strategies add). The builder picks the strategy and options that update exactly the files above.
- **Changelog sections:** Features (`feat`), Bug Fixes (`fix`), Performance (`perf`), Reverts (`revert`). `docs`, `chore`, `ci`, `build`, `test`, `refactor` and `style` don't appear. Breaking changes are listed by release-please in their own section.
- **Editing before release.** The maintainer can edit the release PR's changelog text before merging. If a merged PR's title came out wrong, the documented fix is release-please's commit-override block in that PR's description (see `docs/releasing.md`).

### Release on merge

- **Draft first, public last.** When the release PR merges:
  1. release-please creates the GitHub Release as a **draft** and creates the tag `v<version>` itself (`draft: true` with `force-tag-creation: true`), using the App token.
  2. The tag push starts `release.yml`, as a pushed tag does today.
  3. **A new final job in `release.yml`** runs after both uploads succeed and publishes the draft release, using `GITHUB_TOKEN` with `contents: write` on that job only.

  So the Releases page only shows a version once PyPI has both packages, and the release becomes immutable at that moment.
- **Must be verified by the builder:** that a tag created this way (through the App, by release-please) triggers `on: push: tags` in `release.yml`, and that it does so exactly once. If it doesn't, change the trigger so that merging the release PR starts `release.yml` exactly once. The trusted publishers don't depend on the trigger event, only on the workflow file name and the environment. Don't fall back to `workflow_call`: PyPI doesn't accept a reusable workflow as a trusted publisher.
- **The tag check (`scripts/check_release.py`) keeps its job**, with new sources:
  - It reads both versions from the `__init__.py` files, not from `pyproject.toml`. It also checks `package.json`.
  - It refuses any version 1.0 or above. That guards the 0.x rule against a stray `Release-As:` or a misconfigured bump.
  - It checks that `CHANGELOG.md` has a release-please heading for the version: `## [X.Y.Z](compare-link) (YYYY-MM-DD)`, with a valid date.
  - It still checks that the tag is `v<version>` and that every package matches it.
  - With no tag (a manual run), it checks that the packages agree.
- **If a release fails partway:** nothing is public until the uploads succeed, because the release is still a draft. "Re-run failed jobs" finishes a half-done upload, as today. A broken release that can't be finished is fixed by the next patch version.
  - `docs/releasing.md` explains this, including what immutability rules out: the tag of a published release can't be moved or deleted, and its name can't be reused.

### One version source per package (dynamic version)

- **In each package's `pyproject.toml`:** replace `version = "…"` with `dynamic = ["version"]`, and add `[tool.hatch.version] path = "src/<package>/__init__.py"`.
- **Add `[tool.uv] cache-keys`** in each member listing `pyproject.toml` and that `__init__.py`. Without it, `uv sync` keeps a stale version in the dev environment after a bump.
- **Run `uv lock` once.** It removes the two workspace members' `version` lines from `uv.lock`, and later bumps don't touch the lockfile.
- **Spike result (2026-11-01, on a scratch clone):**
  - lock check and `uv sync --locked` pass after a bump
  - sdist → wheel builds carry the new version, the runtime files, LICENSE and NOTICE, and `Requires-Dist: prumi~=0.2.0`
  - the install check passes
  - every test outside `test_release.py` passes
  - `cache-keys` fixes the stale version
- **`packages/prumi-core/tests/test_release.py` changes:**
  - The expected version is read from `prumi/__init__.py`, not written as a literal.
  - It checks that the adapter's `__init__.py`, `package.json`, both installed versions and both built wheels match that version.
  - It checks that both cross-package ranges are `~=X.Y.Z` for the current version.
  - Its tag-check tests use the new changelog heading.
  - Each check is named after the behavior it checks.

### Protection and GitHub settings

**The release App.** A GitHub App owned by the `prumihq` organization, installed on `prumi` only.
- Permissions: Contents read/write and Pull requests read/write, nothing else.
- Its client ID is stored as a repository variable and its private key as a repository secret. Names are the builder's call; document them.
- Tokens come from `actions/create-github-app-token`, pinned to an exact version.

**Branch rules for `main`** (classic protection or a ruleset; the builder chooses and documents it):
- require a PR, with 0 approvals
- required checks: `python`, `install-check` and the PR title check
- require linear history
- no force pushes, no deletion

**Repository merge settings:**
- squash merge only
- squash commit title = PR title
- delete branches after merge

**Tag ruleset** for `refs/tags/v*`:
- restrict creation, update and deletion
- block force pushes
- bypass: the repository admin role and the release App

**Immutable releases:** turned on for the repository.

**The first release is the end-to-end test (maintainer's decision).** There's no rehearsal on a throwaway repository. The safety nets:
- nothing uploads without the maintainer's approval, and a rejected deployment publishes nothing
- the release stays a draft until both uploads succeed

**The tag ruleset and immutable releases are switched on only after that first release has worked end to end,** so a misfire can still be cleaned up: delete the draft and the tag, fix, retry. `docs/releasing.md` says so in its one-time setup, and describes that cleanup for the first release.

These settings are changed by the maintainer, or by an agent running `gh api` commands only after the maintainer says go for each one. `docs/releasing.md` lists them under one-time setup, with the commands. **Order matters:** turn on the PR title check as required and squash-only merging only after the workflows that report those checks are on `main`, or `main` can't be merged into.

### Changelog rewrite

- **`CHANGELOG.md` is rewritten from the top** in release-please's format.
  - The 0.1.0 entry is rewritten as `## [0.1.0](https://github.com/prumihq/prumi/releases/tag/v0.1.0) (2026-10-19)`, with the same facts in release-please's sections (`### Features`).
  - It has no "Known limitations" subsection.
  - release-please adds new sections above it.
- **Known limitations live in the README's "Status and roadmap" section from now on (maintainer's decision).** The changelog records changes, and the README describes the current state.
  - Any limitation from the 0.1.0 entry that the README doesn't already state is added there, so none is lost.
  - `AGENTS.md`'s docs rule changes to match: a PR that adds or removes a limitation updates the README's status section.
- **A GitHub Release for v0.1.0 already exists** (created by hand on 2026-10-20, not immutable). Its text is updated to the rewritten entry. Its tag isn't touched.

### Agent and contributor rules (docs)

- **`AGENTS.md`:**
  - Commits: PR titles follow Conventional Commits and become the changelog line, so a user-visible change gets a `feat`/`fix` title a user would understand. Commits on a branch need no format, because they're squashed.
  - Agents never create or push tags, never change version numbers, and never edit the release PR.
  - The "Docs are part of the task" bullet for `CHANGELOG.md` is replaced: the PR title is the changelog entry.
  - "Releases stay 0.x" stays.
- **`CONTRIBUTING.md`:** a short "How releases work" section:
  - the title format
  - `feat` → minor, `fix` → patch
  - a maintainer merges the release PR
  - link to `docs/releasing.md`

  Also update the repository layout table for the new files, and replace the current "Releases" paragraph.
- **`docs/releasing.md`,** rewritten as the maintainer's manual:
  1. **One-time setup:**
     - the PyPI publishers and environments, kept from today's text
     - the App
     - the variables and secrets
     - the branch, merge and tag settings
     - immutable releases
  2. **Each release:** review the release PR, edit wording if needed, merge, approve the two deployments, check PyPI and a fresh install.
  3. **Fixing a changelog line,** with the commit-override block.
  4. **When something fails,** and what can't be undone.
- **ADR:** propose a new ADR (status Proposed) recording two decisions: releases come from release-please with Conventional Commit PR titles, and each package's version lives in its `__init__.py` (dynamic), not in `pyproject.toml`.
- **The other docs `AGENTS.md` lists** are checked and updated if affected. `README.md` changes only in its status section (known limitations, above).

## Left to the builder

- release-please strategy and option names, as long as the result matches the design above. Verify with a local dry run (`release-please release-pr --dry-run`) against the repo before relying on it.
- The PR title check action and its configuration. Allowed types: `feat`, `fix`, `perf`, `revert`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `style`. Scopes are optional.
- Workflow details: exact action versions (pinned, as the repo already does), concurrency, job names.
- How to test the scripts and config (tests next to the package, named after behavior). Tests never read `docs/superpowers/`.
- Order of tasks, with the constraint on GitHub settings above.

## Calls made by Claude (not discussed with the maintainer)

- One release-please component for the whole repo rather than three linked components.
- Draft release, published by `release.yml` only after both uploads succeed.
- Which commit types appear in the changelog.
- `~=X.Y.Z` (the full current version) for the cross-package ranges.
- A blank squash commit body. Breaking changes are marked only by `!` in the title.
- The tag check refuses versions 1.0 and above.
- Keeping the changelog check in `check_release.py`, adapted to the new heading.
- Delete branches after merge, linear history, and the PR title check as a required check.
- Settings changed by the maintainer, or by an agent only with a go-ahead for each one.
- No TestPyPI, no assets on the GitHub Release.
