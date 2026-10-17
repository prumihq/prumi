# 0001. Repository layout

Status: Accepted · 2026-10-17

## Context

Prumi has a Python server, source adapters that pull in heavy dependencies (MetricFlow for dbt, later Power BI clients), and a browser runtime in JS. Users should install only the adapters they need, and others should be able to write adapters.

## Decision

- **One repository** for the server, the first-party adapters and the runtime.
- **A uv workspace** for the Python packages. The root `pyproject.toml` is not a package; it holds shared dev tools (pytest, ruff).
- **`prumi-core`** (import `prumi`) holds the server and the adapter protocol.
- **One package per adapter**, named `prumi-adapter-<source>` (import `prumi_adapter_<source>`). Core never imports an adapter; adapters depend on core. Discovery will use Python entry points.
- **`prumi-runtime`** is plain JS with no build step, served by the server.
- `examples/` holds demo projects that are not product code; `spikes/` holds throwaway experiments.

## Consequences

- Adapter dependencies (e.g. MetricFlow and dbt-core) stay out of core.
- A change to the adapter protocol and its adapters lands in one commit.
- The JS runtime has no toolchain yet. Add one only when tests or bundling need it.
- Open: whether a `prumi` meta-package on PyPI installs core plus default adapters.
