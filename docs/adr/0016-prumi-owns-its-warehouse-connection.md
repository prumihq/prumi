# 0016. Prumi's warehouse connection is its own, and its password stays out of `prumi.toml`

Status: Proposed · 2026-11-02

## Context

dbt teams already have connection settings in `profiles.yml`, so a reader would likely expect Prumi to reuse the dbt profile for Postgres. But a dbt profile belongs to a developer running dbt: it usually names a role that can write, and it can hold a password. Prumi only reads, and later runs each query as the viewer ([ADR 0003](0003-access-belongs-to-the-source.md)), so its connection must not be tied to whichever account dbt builds with.

## Decision

- **Prumi keeps its own connection in `prumi.toml`** (`[source.dbt.connection]`), for a read-only role. Every Postgres session is also read-only at the session level. Keeping it separate leaves room for per-viewer identity later.
- **`prumi.toml` never takes a password.** libpq finds it (`PGPASSWORD`, `~/.pgpass`, the service file), so a password never lands in a config file, a log or an error message.

The keys and where the password comes from are in [`docs/configuration.md`](../configuration.md).

## Consequences

- The connection settings are written twice, once in `profiles.yml` and once in `prumi.toml`, and can drift apart.
- Named connections (issue #20) may supersede where the settings live; this ADR would then be superseded, not edited.
- Starting Prumi needs the password in the environment or a pgpass file, which is one more thing to set up.
