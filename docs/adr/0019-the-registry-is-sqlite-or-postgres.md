# 0019. The registry is SQLite or Postgres, in a database of its own

Status: Proposed · 2026-11-04

Supersedes: [0013](0013-registry-sqlite.md)

## Context

[ADR 0013](0013-registry-sqlite.md) kept the registry (dashboards, their versions, certifications, metric requests and viewers' check reports) in one SQLite file, `<data_dir>/registry.sqlite3`, because 0.1 ran on one machine for one person and installing Prumi shouldn't need a database server.

That file is now in the way. A container keeps it on a volume that must live on a local disk, backing it up while Prumi runs takes care with WAL mode, and a team that runs Prumi for others already runs Postgres and backs it up. 0013 kept the SQL plain and the migrations numbered so that moving to another store would be possible; this is that move, with SQLite kept for trying Prumi on one machine.

## Decision

- **The backend is chosen in `[registry]`:** `backend = "sqlite"`, the default, or `"postgres"`. Without a `[registry]` section the registry is SQLite at `<data_dir>/registry.sqlite3`, exactly as 0013 had it. Postgres is the one recommended for a real install.
- **What 0013 decided still holds on both:** plain SQL, no ORM; numbered migrations recorded in a `schema_version` table and run at startup, each in its own transaction; the tables `dashboards`, `versions`, `certifications`, `metric_requests` and `page_checks`; versions are never updated, and status is computed on read ([ADR 0007](0007-certification-fingerprint.md)); delete removes a dashboard with its versions, certifications and checks, and its metric requests stay, unlinked.
- **The migrations share their numbers.** Where the SQL can't be the same on both (SQLite's `AUTOINCREMENT`, Postgres's identity columns), a number has a file for each backend, and the tables, columns and constraints they create mean the same. An existing SQLite registry opens unchanged.
- **Text stays text.** Timestamps, spec JSON and HTML are stored as the same text on both, so fingerprints and hashes come from the same bytes. Neither backend stores a NUL character: text with one is refused before it's stored.
- **Writes are serialized on both.** SQLite's `BEGIN IMMEDIATE` on one; on Postgres, every writing transaction takes the same transaction-level advisory lock. Publishing, certifying and deleting stay single transactions.
- **A Postgres registry has a database of its own.** On first use (no `schema_version` table yet), Prumi refuses a database that already holds any table or view outside Postgres's own schemas, and one whose encoding isn't UTF8 (the registry sorts its keys by their bytes, which matches SQLite's order only in UTF-8); the message says what to create. The check needs nothing from the adapter, so it catches the warehouse whatever name or address leads to it.
- **The settings are in `[registry.postgres]`,** keyed like `[source.dbt.connection]` for Postgres: `host`, `port`, `dbname`, `user`, `sslmode`. Unknown keys, a `password` key and connection strings in `host` or `dbname` are refused.
- **The password comes from `PRUMI_REGISTRY_PASSWORD`** and nowhere in the config. Unset, Prumi passes none. No message or log shows it, and the startup messages are Prumi's own text, never the driver's.
- **Startup checks the registry before the server listens.** A database that can't be reached, a login refused, a database that doesn't exist, a role that can't create tables or a missing driver each stop Prumi with a message naming the setting to change. The driver is the core extra `prumi[postgres]`; `prumi[dbt]` already brings it.
- **One registry per deployment.** One `prumi serve` uses a registry, with the CLI commands beside it; two deployments never share one.

The keys are in [`docs/configuration.md`](../configuration.md#registry).

Considered: an ORM such as SQLAlchemy (rejected: the SQL is small and plain, and a dependency that size would hide the few places where the two backends differ). Keeping the registry in the warehouse database, in a schema of its own (rejected: Prumi's warehouse role and every warehouse session are read-only, as [ADR 0018](0018-the-warehouse-password-comes-from-a-named-variable.md) keeps them; giving them write access would undo that, and the registry's tables would mix with what dbt builds and backs up). Copying an existing SQLite registry into Postgres (rejected for now: there are no team installs yet to move, and the tool can come when someone needs it).

## Consequences

- **Backups:** on Postgres, the database's own tools, such as `pg_dump`. On SQLite, as 0013 says: stop the server and copy `registry.sqlite3`, or run `sqlite3 registry.sqlite3 ".backup <file>"` while it runs, since recent writes may still be in `registry.sqlite3-wal`.
- **SQLite's limits stay SQLite's:** WAL mode, and `data_dir` on a local disk. With Postgres, `data_dir` holds nothing.
- **Moving from SQLite to Postgres starts an empty registry.** Dashboards in an old `registry.sqlite3` stay there.
- **Several servers behind one URL still aren't supported.** The registry would allow it, but validated candidates live in each server's memory ([ADR 0008](0008-validated-candidates.md)), so a candidate checked on one server couldn't be published on another.
- **The queries are shared; only some migrations differ.** A schema change may need a file per backend, and the registry's tests run on both to keep them meaning the same.
- The registry still holds no company data: what agents and the data team wrote, plus viewers' check reports reduced to codes, element paths and counts ([ADR 0010](0010-no-company-data-to-the-agent.md)).
