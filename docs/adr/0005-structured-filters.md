# 0005. Filters are structured; agent and browser input never becomes SQL text

Status: Proposed · 2026-10-19

## Context

Specs and `query` calls are written by an agent, and filter values come from the browser. MetricFlow writes filter values into the SQL as literals, and renders `where` clauses as Jinja templates ([`metricflow-compile` spike](../../spikes/metricflow-compile/README.md)). A free-text `where` string from an agent would be SQL injection, and a value containing `{{ }}` would be template injection.

## Decision

- **Filters are always structured:** `{dimension, op, values}`, with `op` one of `eq`, `ne`, `in`, `not_in`, `lt`, `le`, `gt`, `ge`. No raw `where` string is accepted anywhere: not in the spec, not in `query`, not from the browser.
- **The dbt adapter renders each filter itself:** strings in single quotes with `'` doubled; ints and finite floats via `repr`; booleans as `TRUE` / `FALSE`; `None` as `IS NULL` / `IS NOT NULL`. A string containing `{` or `}` is refused (`filter.invalid_value`). Dimension names must be plain identifiers that the catalog shows for the query's metrics.
- **NULL semantics are part of the adapter protocol,** so every adapter behaves the same: `eq` / `in` with `None` among the values also match NULL; `ne` / `not_in` keep NULL rows unless `None` is among the values. A NULL region then lands in "Others" instead of disappearing.
- Compiled SQL is logged only at DEBUG, with every literal replaced by `?`.

Considered: bind parameters. MetricFlow doesn't produce them, and the adapter refuses SQL that has them, so rendering literals safely is the adapter's job.

## Consequences

- Filters on metric values (HAVING) or free-form conditions need a new structured form before they can exist.
- A dimension value containing a brace can't be filtered on.
- Every new adapter implements the same filter semantics: they belong to the protocol, not to the dbt adapter.
