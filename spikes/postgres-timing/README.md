# Spike: what "data through" costs on Postgres

Status: done, 2026-11-02 · Time box: one session

## Question

The home page's "data through" runs one query per catalog metric, each over a new connection. Is that slow enough on Postgres to matter on the demo?

## How to run

```sh
PGPASSWORD=... uv run python spikes/postgres-timing/measure.py path/to/prumi.toml
```

The `prumi.toml` is the demo's, with a `postgres` connection (see `write_postgres_demo_config` in `packages/prumi-core/tests/conftest.py`). It times one connection, the adapter start, `Services.data_through()` cold, and one `Engine.run_block` per block of the demo dashboard.

## Answer

No action: `data_through()` cold takes under a second in both places, below the 2 second threshold. Three runs each, in milliseconds.

| | Local (Docker `postgres:18`) | CI (GitHub-hosted, `postgres:18` service) |
|---|---|---|
| open one connection (+ `SELECT 1`) | 13.7 / 12.7 / 12.2 | 10.0 / 10.0 / 10.0 |
| start the adapter | 476 / 471 / 506 | 421 / 402 / 402 |
| `data_through()` cold | 792 / 888 / 806 | 715 / 710 / 708 |
| `run_block` kpi | 202 / 277 / 212 | 181 / 180 / 180 |
| `run_block` by_region | 643 / 743 / 646 | 568 / 558 / 564 |
| `run_block` trend | 101 / 138 / 105 | 92 / 93 / 93 |

Both are on the same machine as the server, so connection setup is cheap; a remote or TLS database will cost more per connection.
