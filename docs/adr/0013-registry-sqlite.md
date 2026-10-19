# 0013. The registry is SQLite

Status: Accepted · 2026-10-19

## Context

Prumi stores dashboards, their versions, certifications, metric requests and viewers' check reports. 0.1 runs on one machine, and the server and the CLI (`certify`, `delete`) are separate processes that work on the same data. Installing Prumi shouldn't need a database server.

## Decision

- **SQLite from Python's standard library,** in one file: `<data_dir>/registry.sqlite3`.
- **WAL mode,** so the CLI and the server use the file at the same time. The server reads status on every request, so a `certify` shows on the next page load.
- **Plain-SQL migrations,** numbered in `prumi/registry/migrations/` and recorded in a `schema_version` table, run at startup.
- **Tables:** `dashboards`, `versions`, `certifications`, `metric_requests`, and `page_checks` (the latest in-browser check per version and filter state).
- **Versions are never updated.** Each stores the canonical spec JSON, the HTML with line endings normalized to `\n`, and its hashes and fingerprint ([ADR 0007](0007-certification-fingerprint.md)). Status isn't stored; it's computed on read.
- **Delete** removes a dashboard with its versions, certifications and checks. Its metric requests stay, unlinked.

Considered: a database server (rejected for 0.1: one more thing to install and run for a single local user). Files per dashboard (rejected: certification and status need transactions across versions and certifications).

## Consequences

- Nothing to install or configure. To back up the registry, stop the server and copy the file, or run `sqlite3 registry.sqlite3 ".backup <file>"` while it runs: in WAL mode, recent writes may still be in `registry.sqlite3-wal`, so copying the main file alone can miss them.
- `data_dir` must be on a local disk: SQLite's WAL mode doesn't work over a network filesystem.
- One machine only. Several server processes behind one URL, or a hosted deployment, need another store. Keeping the SQL plain and the migrations numbered makes that move possible, not free.
- The registry holds what agents and the data team wrote, plus viewers' check reports reduced to codes, element paths and counts: a filter state is stored as how many values each dimension filter selected, and error messages are cleaned. No query results and no company data ([ADR 0010](0010-no-company-data-to-the-agent.md)).
