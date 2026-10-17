# Spike: MetricFlow compiles, Prumi executes

Status: not run yet · Time box: 1–2 sessions

## Question

Can we use MetricFlow **as a Python library** to compile a semantic query (metrics + group by + filters) from a dbt project's semantic manifest into SQL, **without MetricFlow running the query**, and then execute that SQL ourselves over our own DuckDB connection?

## Why it matters

The dbt adapter's design rests on this split: **MetricFlow compiles, Prumi executes, the warehouse enforces access.** Later, Prumi will run queries with each viewer's own credentials, so the connection must belong to Prumi, never to MetricFlow. If MetricFlow can't hand us the SQL without owning the connection, the adapter design changes before any product code is written.

## Constraints

- Use MetricFlow's **Python API**. No `mf` CLI subprocess, no dbt Cloud Semantic Layer API.
- MetricFlow may only **compile**. Our code opens the DuckDB connection and executes the SQL.
- If MetricFlow insists on a SQL client even just to compile (e.g. to know the dialect), check whether a client that never executes anything works. Record exactly what it needs.
- Everything lives in this folder: its own `pyproject.toml`, its own tiny dbt project, its own DuckDB file. Don't touch `packages/`, and don't add MetricFlow or dbt to the workspace.
- Build a **tiny** dbt project here, on DuckDB, with seed data you write by hand (a few dozen rows). This is not `examples/demo-dbt-project`; that one comes in the MVP.
- Use the newest released MetricFlow and dbt-core versions that work together. A different Python version for this folder is fine if needed.

## What to build

A tiny dbt project with:
- one fact table (e.g. orders: date, region, amount, customer id) and one dimension if needed
- a semantic model with at least:
  - a **simple metric**, e.g. `revenue` (sum of amount)
  - a **ratio metric**, e.g. `average_order_value` (revenue / order count)
  - a **distinct count**, e.g. `customers`
- a time dimension at day grain and a categorical dimension (region)

Then a script that:
1. produces the semantic manifest (`dbt parse` is fine for the spike)
2. loads it into MetricFlow and compiles queries to SQL
3. executes the SQL over its own DuckDB connection and prints the rows

## Success criteria

The answer is **yes** if all of these hold:

1. We get compiled SQL from MetricFlow without MetricFlow executing anything.
2. Our own connection runs that SQL and returns correct rows.
3. The ratio metric queried **by month** and **by region** each match a hand-written SQL check. That proves it's computed at the displayed grain and not re-aggregated from finer rows. Same check for the distinct count.
4. A filter (e.g. `region = 'North'`) and a time window compile and return correct rows.

## Also record

- What metadata we can read for a catalog: metric names, descriptions, labels, types, valid dimensions per metric, time grains, formats/units if any.
- Exact versions of MetricFlow, dbt-core and dbt-duckdb, and **the license of each exact version** (MetricFlow's license has changed in the past; confirm the one we'd depend on).
- What MetricFlow needs at runtime: files, objects, a SQL client or not, network access or not.
- Anything surprising, slow or awkward.

## When done

1. Fill in **Answer** below: yes / no / yes with caveats, plus the evidence (queries, SQL, outputs).
2. Update the row in `spikes/README.md`.
3. If the answer shapes the adapter design, propose an ADR in `docs/adr/` (status: Proposed).
4. If you get stuck or the question turns out to be wrong, stop and write what you found and what's blocking.

## Answer

Not run yet.
