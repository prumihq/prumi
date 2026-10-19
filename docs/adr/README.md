# Architecture decision records

Short records of technical decisions: context, decision, consequences. Numbered, never rewritten once accepted; a later ADR supersedes an earlier one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-repository-layout.md) | Repository layout: one repo, uv workspace, one package per adapter | Accepted; package names replaced by [0014](0014-package-names-on-pypi.md) |
| [0002](0002-dbt-adapter-metricflow-compiles.md) | dbt adapter: MetricFlow compiles from the semantic manifest, Prumi executes over its own connection | Accepted |
| [0003](0003-access-belongs-to-the-source.md) | Access rules belong to the source; every query runs as a viewer | Accepted |
| [0004](0004-sandboxed-frame.md) | Dashboards run in a sandboxed frame; the browser sends only filter values | Accepted |
| [0005](0005-structured-filters.md) | Filters are structured; agent and browser input never becomes SQL text | Accepted |
| [0006](0006-catalog-as-the-source-has-it.md) | The catalog shows the source as it is: its names, formats from `config.meta.prumi`, conservative additivity | Accepted |
| [0007](0007-certification-fingerprint.md) | Certification follows a fingerprint of the spec and the stored HTML; status is computed | Accepted |
| [0008](0008-validated-candidates.md) | Publishing goes through validated candidates; edits instead of resending | Accepted |
| [0009](0009-number-provenance.md) | Every number shown is a value Prumi delivered, checked in headless Chromium | Accepted |
| [0010](0010-no-company-data-to-the-agent.md) | No company data reaches the agent: `query` returns a profile | Accepted |
| [0011](0011-one-display-locale.md) | One display locale per server | Accepted |
| [0012](0012-dashboard-spec-format.md) | The dashboard spec is Prumi's own format: what a dashboard shows, never what a metric means | Accepted |
| [0013](0013-registry-sqlite.md) | The registry is SQLite, in WAL mode, with plain-SQL migrations | Accepted |
| [0014](0014-package-names-on-pypi.md) | Package names on PyPI: the server is `prumi`, adapters stay separate, `prumi[dbt]` installs both | Accepted |
