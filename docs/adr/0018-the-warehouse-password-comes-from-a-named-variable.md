# 0018. The warehouse password comes from a variable that `prumi.toml` names

Status: Proposed · 2026-11-04

Supersedes: [0016](0016-prumi-owns-its-warehouse-connection.md)

## Context

[ADR 0016](0016-prumi-owns-its-warehouse-connection.md) gave Prumi a warehouse connection of its own, for a read-only role, and kept the password out of `prumi.toml` by leaving it to libpq, Postgres's client library: `PGPASSWORD`, `~/.pgpass` or a service file.

Few people know that mechanism, and the docs had to teach it before anyone could connect. It also stops working once Prumi has a second Postgres connection, for the registry, with a role of its own: `PGPASSWORD` holds one password, and a pgpass file is one more file to mount into a container. A container or a compose file passes secrets as environment variables, and teams expect to choose those variables' names.

## Decision

- **Prumi keeps its own connection,** as 0016 decided: `[source.dbt.connection]` in `prumi.toml` (or its `PRUMI__SOURCE__DBT__CONNECTION__…` variables), for a read-only role, with every Postgres session read-only.
- **`prumi.toml` never takes a password.** The `password` key is refused, and the message points to `password_env`.
- **`password_env` names the environment variable that holds the password,** the way dbt's `env_var()` does: `password_env = "WAREHOUSE_PASSWORD"`. Prumi reads that variable at startup and hands the password to the driver itself. Only the variable's name is in the config.
- **A named variable that is unset, empty or only whitespace stops Prumi at startup.** The message names `password_env`, and names the variable only when it looks like a variable name (upper case, digits, underscores), so a password typed into `password_env` by mistake is never echoed. No message or log shows a password.
- **Without `password_env`, Prumi passes no password.** A server that needs none (trust or peer authentication, client certificates) works as before.
- **The connection-failure messages point to `password_env`,** not to `PGPASSWORD` or pgpass.

The keys are in [`docs/configuration.md`](../configuration.md#sourcedbtconnection).

Considered: keeping libpq's `PGPASSWORD` and pgpass (rejected: unfamiliar to most users, and one `PGPASSWORD` can't hold two passwords once the registry is on Postgres too). A fixed variable name such as `PRUMI_WAREHOUSE_PASSWORD` (rejected: teams already have a name for the secret, often shared with dbt's own `env_var()`, and naming it keeps one secret in one place). A connection URL in a variable, as Airflow does (rejected: a URL mixes the password with settings that appear in messages, and Prumi already refuses connection strings in `host` and `dbname` for that reason).

## Consequences

- **libpq's own lookup still works underneath.** When `password_env` is unset, libpq looks in `PGPASSWORD` and `~/.pgpass` on its own, as it always has. Blocking it would take code and only hurt people who set it up on purpose, so nothing blocks it; the docs no longer mention it. When `password_env` is set, libpq uses the password Prumi passes.
- The connection settings are still written twice, once in `profiles.yml` and once for Prumi, and can drift apart. Pointing `password_env` at the same variable dbt's `env_var()` reads keeps at least the password in one place.
- Named connections (issue #20) may still supersede where the settings live; this ADR would then be superseded, not edited.
