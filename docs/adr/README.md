# Architecture decision records

Short records of technical decisions: context, decision, consequences. Numbered, never rewritten once accepted; a later ADR supersedes an earlier one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-repository-layout.md) | Repository layout: one repo, uv workspace, one package per adapter | Accepted |
| [0002](0002-dbt-adapter-metricflow-compiles.md) | dbt adapter: MetricFlow compiles from the semantic manifest, Prumi executes over its own connection | Accepted |
| [0003](0003-access-belongs-to-the-source.md) | Access rules belong to the source; every query runs as a viewer | Proposed |
| [0004](0004-sandboxed-frame.md) | Dashboards run in a sandboxed frame; the browser sends only filter values | Proposed |
| [0005](0005-structured-filters.md) | Filters are structured; agent and browser input never becomes SQL text | Proposed |
| [0006](0006-catalog-as-the-source-has-it.md) | The catalog shows the source as it is: its names, formats from `config.meta.prumi`, conservative additivity | Proposed |
| [0007](0007-certification-fingerprint.md) | Certification follows a fingerprint of the spec and the stored HTML; status is computed | Proposed |
| [0008](0008-validated-candidates.md) | Publishing goes through validated candidates; edits instead of resending | Proposed |
| [0009](0009-number-provenance.md) | Every number shown is a value Prumi delivered, checked in headless Chromium | Proposed |
| [0010](0010-no-company-data-to-the-agent.md) | No company data reaches the agent: `query` returns a profile | Proposed |
| [0011](0011-one-display-locale.md) | One display locale per server | Proposed |
