# Spike: what "data through" costs on Postgres

Status: measuring · Time box: one session

## Question

The home page's "data through" runs one query per catalog metric, each over a new connection. Is that slow enough on Postgres to matter on the demo?

## How to run

```sh
PGPASSWORD=... uv run python spikes/postgres-timing/measure.py path/to/prumi.toml
```

The `prumi.toml` is the demo's, with a `postgres` connection (see `write_postgres_demo_config` in `packages/prumi-core/tests/conftest.py`). It times one connection, the adapter start, `Services.data_through()` cold, and one `Engine.run_block` per block of the demo dashboard.

## Answer

Local numbers (Docker `postgres:18`, three runs, ms): to be completed with the CI numbers.
