# Reload the dbt manifest without restarting the server

Status: approved by the maintainer on 2026-11-03. Issue: #8 (milestone 0.2).

## Why

Prumi reads `target/semantic_manifest.json` once, when `prumi serve` starts. After `dbt parse` changes a metric, the server keeps serving the old definitions until someone restarts it. The limitation was found while building 0.1, not reported by a user: real users are expected from 0.4 (team server) on. So this release makes the server pick up a new manifest by itself, with the same guarantees as at startup, and keeps the work small.

Success: while `prumi serve` runs, `dbt parse` changes a metric, and within a few seconds agents and pages use the new definition. A new manifest that fails a check changes nothing, and the server log says why.

## Goals

1. **The server notices a new manifest** and loads it, without a restart and without any action from the user.
2. **The same checks as at startup.** The new manifest must pass everything `prumi serve` checks before it binds: MetricFlow parses it, the connection type has a dialect, every table it names exists on the connection (and, on Postgres, the database matches and the tables are readable).
3. **All or nothing.** If the checks pass, the new manifest replaces the old one at once. If any check fails, the server keeps the manifest it has and logs one warning with the same message startup would give.
4. **Agents and pages see the change on their next request.** MCP tools search the new catalog, blocks run the new definitions, and the home page's "data through" is recomputed.

## Non-goals

- **Reloading `prumi.toml`.** Connection settings, port, hosts and everything else in the config still need a restart.
- **Re-checking published dashboards.** A dashboard whose metric the new manifest drops or changes behaves as it does today after a restart: its blocks fail or show the new numbers. Flagging such dashboards is separate work.
- **A command or endpoint to trigger a reload.** Noticing the file is enough for 0.2.
- **Telling agents or the home page that a reload failed.** The maintainer chose the server log as the only place that says why.
- **Reloading in the other CLI commands** (`list`, `certify`, `validate`, …). They run once and exit, so they already read the current manifest.

## Anti-goals

The work fails, even if reloading "works", if:

- a request sees half of one manifest and half of another (the catalog of the new one, the compiler of the old one);
- a manifest that fails a check replaces a good one, even briefly;
- a half-written manifest (caught while `dbt parse` is still writing it) is loaded, or produces a warning storm;
- a failed reload stops the server or makes it stop serving the old manifest;
- the reload loop logs on every poll while nothing changes;
- startup behavior changes: a bad manifest at startup still stops `prumi serve` before it binds.

## Constraints

- AGENTS.md's rules: core never imports an adapter, numbers only come from the source, dialects are MetricFlow's.
- Logs never hold row values, filter values or credentials. Reload warnings reuse the startup messages, which already follow this rule.
- Core reloads by building a new source through the adapter's factory, the same way startup does, so no adapter has to support reloading itself. `discover()` stays "read once" per adapter instance.
- **Which file to watch (my call):** core can't read `semantic_manifest` itself (it's a dbt key, and core never knows an adapter's settings). The adapter protocol gets one optional method, `watched_files() -> Sequence[Path]`: the files whose change means "build me again". The dbt adapter returns the resolved manifest path. An adapter without the method, or returning nothing, is never reloaded. The adapter package README documents the method (it's part of the protocol's public API).

## Decided by the maintainer

- **The server notices the file by itself**; no reload command.
- **A failed reload is reported in the server log only**, with the message startup would give. Agents and the home page aren't told.

## How it works

- **Noticing (my call):** `prumi serve` polls the watched files' modification time and size every 2 seconds, starting from the values seen at startup. Polling, not file-system events, because events are unreliable on WSL2, Docker volumes and network drives, and one `stat` every 2 seconds costs nothing.
- **Waiting for the write to finish (my call):** the server loads a changed file only once its modification time and size are the same on two polls in a row. A missing file is a change too: missing on two polls in a row, it's a failed reload like any other, with the startup message for a missing manifest.
- **Retrying (my call):** a failed reload is retried every 30 seconds while the file stays the same, and right away when it changes. This matters for the usual dbt flow: `dbt build` writes the manifest when it parses, before it creates a new model's table, so the first try fails on the missing table and a later one succeeds. The warning is logged once per version of the file, not on each retry; the success is logged when it comes.
- **Loading:** core builds a new `Source` from the same `[source]` config, exactly as at startup (through the adapter's factory), which runs every startup check. This happens in the background, off the request path; requests keep using the current source meanwhile.
- **Swapping:** the source and everything built from it (the engine, the relations cache) are replaced together, in one step. A request that already started finishes on the manifest it started with.
- **One manifest per request (my call):** each request handler (a page, a block, filter values, an MCP tool call) takes one snapshot of the current source and engine at its start and uses only that. Reading `services.source` and then `services.engine` separately, as call sites do today, could mix two manifests; those call sites change. Tests may still set up a `Services` with a given source.
- **"Data through" (my call):** a swap empties its cache, so the next home page request computes it again, as when its 10-minute cache expires today.
- **Logging:** a successful reload logs one INFO line (for example `manifest reloaded: 12 metrics`). A failed one logs one WARNING, once per version of the file. A startup error (`AdapterStartupError`) is logged with its message, the one startup would print. Any other exception is logged by its type name only, since a driver's message can hold connection details.
- **Only the server reloads.** The polling starts with `prumi serve` and stops with it. Tests and other commands that build `Services` don't poll unless they ask for it.

## Tests

- Changing the manifest while the server runs (a copy of the demo manifest in a temp folder, with a metric's label or description changed) shows up in the catalog an MCP tool returns, without a restart.
- A manifest that doesn't parse, and one that names a table that doesn't exist, each leave the old catalog in place and log one warning with the startup message; a later good manifest is then loaded.
- A manifest whose table doesn't exist yet is loaded on a retry once the table appears, without the file changing again, and the warning isn't repeated meanwhile.
- A failure that isn't a startup error is logged by its type name only.
- A file caught half-written is not loaded (the stable-twice rule), and nothing is logged for it.
- After a reload, "data through" is recomputed rather than served from the old cache.
- A request in flight during a swap uses one manifest throughout (the source and engine it sees belong together).
- Startup is unchanged: a bad manifest still stops `prumi serve` before it binds.
- The Postgres checks run on reload too: on the Postgres demo, a manifest naming another database is refused and the old one kept.

## Docs

- `README.md`: remove "Manifest changes need a restart." from the known limitations, and say in the serve section that the server picks up a new manifest after `dbt parse` (changes to `prumi.toml` still need a restart). The troubleshooting list notes that a manifest that fails a check at reload is logged and the old one kept.
- `docs/configuration.md`: next to `semantic_manifest`, that the file is watched while the server runs.
- `packages/prumi-adapter-dbt/README.md` (and wherever the adapter protocol is documented): the optional `watched_files()`.
- No ADR: an optional protocol method that adapters may ignore doesn't change any accepted decision.

## Left to the builder

- How the polling runs (a thread or a task in the server's lifespan) and how it's stopped cleanly.
- How the swap is made atomic (one reference to an immutable bundle of source and engine, a lock, …), as long as no request mixes two manifests.
- How tests drive the poller without waiting real seconds (an injectable interval or a "check now" method).
- The exact wording of the INFO line.
