# 0014. Package names on PyPI

Status: Proposed · 2026-10-19

## Context

[ADR 0001](0001-repository-layout.md) split Prumi into a server package, `prumi-core`, and one package per adapter, and left open whether a `prumi` package would install the server with default adapters. Nothing is published yet, so names can still change for free. Once a name is on PyPI it stays there, and each version can be uploaded only once.

A user needs the server and at least one adapter. Tools like `uv tool install` and `pipx` put a program in its own environment and expose only the commands of the package they're given.

## Decision

- **The server is published as `prumi`** (import `prumi`, command `prumi`). This replaces the name `prumi-core` of ADR 0001. The folder stays `packages/prumi-core`.
- **Adapters stay separate packages,** named `prumi-adapter-<source>`, each depending on `prumi` within the same minor version.
- **The server offers each first-party adapter as an extra:** `prumi[dbt]`. `uv tool install "prumi[dbt]"` installs a server that can start; installing `prumi` and an adapter package side by side gives the same result.
- **First-party packages are released together, with one version.**
- **Releases are uploaded from GitHub Actions on a version tag, with PyPI's trusted publishing.** No token is stored.

Considered: a `prumi` package with no code that installs `prumi-core` and the dbt adapter (rejected: it makes one adapter the default, so later sources would either install dbt too or change what `prumi` installs; it would also have to declare the `prumi` command again, and it adds a third name to publish). One package with the adapter merged in (rejected: MetricFlow's dependencies would reach every install, and ADR 0001's boundary between core and adapters would become a convention).

## Consequences

- One name for the package, the import and the command.
- `prumi` alone installs a server that can't start; its startup message names the extra to install.
- The extra points to a package that depends on `prumi`: a dependency cycle. Resolvers handle it, and the lockfile carries it.
- The folder `packages/prumi-core` holds the package `prumi`, so the folder and package names differ.
- A third-party adapter is installed next to `prumi` by its own name; it gets no extra.
- Uploads depend on the GitHub environment that holds the trust, not on a token someone could leak.
