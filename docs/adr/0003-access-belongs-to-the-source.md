# 0003. Access rules belong to the source; every query runs as a viewer

Status: Accepted · 2026-10-19

## Context

A company's warehouse or semantic layer already decides who may see which rows (grants, row-level security, policies). A dashboard tool that keeps its own copy of those rules has a second security model, and it drifts from the first. Dashboards are different: they're Prumi's own objects, and nothing in the source says who may read or change them.

0.1 has one local user and no login, but its code is the base for releases with many viewers.

## Decision

- **Prumi never decides who sees which rows.** It passes the viewer's identity to the source, and the source enforces access.
- **Every query runs as a viewer.** `Adapter.execute(query, viewer)` takes a `Viewer`, and so does every engine function that runs a query: blocks, totals, freshness, filter values, dry runs. In 0.1 the only viewer is `LOCAL_VIEWER`.
- **Each adapter declares its identity mode** in `capabilities()`: `per_viewer` (queries run with the viewer's own credentials) or `shared_account` (one account for everyone, so every viewer sees what that account sees). The DuckDB connection is `shared_account`.
- **Connections open per `execute` call**, so a per-viewer connection later replaces only the connection factory.
- **Dashboard permissions are Prumi's job.** Every read and edit of a dashboard goes through `Registry.can_read(viewer, dashboard)` and `Registry.can_edit(viewer, dashboard)`: the MCP tools, the block API, publishing a new version and the home page. In 0.1 both return true.
- **Without login, the server stays on the machine.** `prumi serve` refuses a `host` that isn't a loopback address. Every route rejects a `Host` header other than the configured one, against DNS rebinding. No route sends CORS headers, and `POST /api/...` requires `Content-Type: application/json`.

## Consequences

- Login and per-viewer credentials can be added without changing the adapter protocol or the engine.
- With a shared account, the viewer's identity stops at Prumi. A later release must make the admin acknowledge that when connecting the source.
- A results cache, if one is added, must key on the source, the compiled query and the viewer's security context.
- The dbt adapter can't serve per-viewer schemas or databases ([ADR 0002](0002-dbt-adapter-metricflow-compiles.md)): per-viewer access must come from policies on shared tables.
- Owners, editors and viewers for dashboards come with login. The permission checks are already in every path that needs them.
