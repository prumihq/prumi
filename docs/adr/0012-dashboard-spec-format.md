# 0012. The dashboard spec is Prumi's own format

Status: Proposed · 2026-10-19

## Context

An agent-built dashboard has to be checked against BI rules, certified, read back and edited by later sessions. The rules need every data decision written down: which metrics, split by what, which filters apply to which block, whether a total or "Others" is shown, which periods are compared. Page code can't be checked for that without running it. Existing dashboard definitions either carry SQL or metric formulas, which would re-implement what the source already defines, or mix the data with the layout.

## Decision

- **The spec says what a dashboard shows, never what a metric means.** It references metrics and dimensions by the adapter's names ([ADR 0006](0006-catalog-as-the-source-has-it.md)). It has no SQL, no formulas and no aggregation.
- **Two parts.** The data part (`filters`, `data` blocks, `interactions`) is the only source of a dashboard's queries. `presentation.bindings` maps HTML selectors to the block or blocks each element shows. `metric_requests` links the dashboard to metrics it asked the data team for. The HTML isn't in the spec: tools take `spec` and `html` side by side.
- **Every BI choice is explicit.** A block's `filters` list is required (it may be `[]`), so a filter never applies by accident. `compare`, `total`, `others`, `top_n`, `sort`, `window` and `time_grain` are declared, so the source computes totals, "Others" and comparisons instead of the page.
- **Strict and versioned.** `spec_version: 0`. Pydantic models with `extra="forbid"`; the generated JSON Schema is committed at `packages/prumi-core/src/prumi/spec/dashboard-spec.v0.schema.json`, and a test fails when it's out of date. MCP tools take JSON; the CLI also takes YAML.
- **Status isn't in the spec,** and its canonical form is what certification hashes ([ADR 0007](0007-certification-fingerprint.md)).

Considered: queries written in the page (rejected: they can't be validated or certified without running page code). SQL or metric formulas in the spec (rejected: they re-implement the source's metrics, and agent-written SQL is an injection risk, [ADR 0005](0005-structured-filters.md)). An existing BI tool's dashboard format (rejected: it brings its own metric and query semantics, which would compete with the source's).

## Consequences

- The validator and the engine read one structure, and the BI rules apply before anything runs.
- A dashboard's data can be reviewed, compared between versions and read back without the HTML, and could later be rebuilt in another BI tool.
- Everything a page shows needs a block. A weekly total across regions is a second block, not a sum in JavaScript ([ADR 0009](0009-number-provenance.md)), so specs get longer.
- The format may change before 1.0, with migration notes. The canonical form includes defaults, so even a new optional field changes it. Stored fingerprints and their certifications survive the change, but content published after it gets a new fingerprint: republishing an unchanged certified dashboard then creates a new draft version.
