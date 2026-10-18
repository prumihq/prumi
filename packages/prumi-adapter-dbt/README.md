# prumi-adapter-dbt

Prumi adapter for dbt semantic models. MetricFlow compiles a semantic query into SQL for the warehouse; Prumi executes that SQL over its own connection; the warehouse enforces access rules.

Import name: `prumi_adapter_dbt`.

## Preparing your dbt project

### What Prumi reads

Prumi reads one file: `target/semantic_manifest.json`, which `dbt parse` (or any dbt command that parses the project) writes. It doesn't run dbt and doesn't need your dbt project folder.

For Prumi to show anything, the project needs:

- `semantic_models:` with entities, dimensions and measures, and `metrics:` built on them ([dbt docs](https://docs.getdbt.com/docs/build/semantic-models));
- a time spine model, which MetricFlow requires for time grains.

Prumi shows your metrics as they are: names, labels, descriptions, types and valid dimensions all come from the manifest. It never copies or changes your definitions.

### Display formats: add `config.meta.prumi` (recommended)

dbt's semantic layer describes how a metric is **computed**, but not how it should be **displayed**: it has no field saying that `revenue` is money in USD, or that `gross_margin_pct` is a fraction to show as a percentage. Without that, every dashboard guesses, and the same metric ends up as `0.25` on one page and `25%` on another, or with currencies mixed on one chart.

Prumi takes formats from the metric definition so they're the same everywhere. We suggest adding them under `config.meta.prumi` on each metric:

```yaml
metrics:
  - name: revenue
    label: Revenue
    description: Net sales after discounts, in USD.
    type: simple
    type_params:
      measure: revenue_amount
    config:
      meta:
        prumi:
          format: currency   # number | integer | percent | currency
          unit: USD          # ISO 4217 code for currency; free text otherwise ("orders")
          decimals: 2        # optional
        owner: sales-analytics
```

| Key | Values | Meaning |
|---|---|---|
| `format` | `number`, `integer`, `percent`, `currency` | `percent` means the value is a fraction: 0.25 shows as 25% |
| `unit` | ISO 4217 code for `currency` (`USD`, `EUR`, `BRL`); any text otherwise | Required for `currency`. Prumi warns when one block mixes units |
| `decimals` | 0–20 | Optional. Defaults: 2 for `number` and `currency`, 0 for `integer`, 1 for `percent` |

- **It's optional.** A metric without `meta.prumi` still works: it's shown as a plain number, and the validator warns that no format is declared (`R10.no_format`). An invalid entry is treated the same way.
- **Why under a `prumi` key:** `meta` is dbt's free-form dictionary for other tools, and dbt itself ignores it. Several tools read keys like `meta.format` with their own meanings, so Prumi keeps its settings in its own namespace to avoid clashes.
- **Owner:** Prumi shows `config.meta.owner` (a common convention, outside the `prumi` key) as the metric's owner when it's a string.

The demo project in [`examples/demo-dbt-project`](../../examples/demo-dbt-project/) uses this convention on every metric except `discount_rate`, which is left without a format on purpose.

### Dimension names

Prumi uses MetricFlow's names for dimensions (`customer__region`: the `region` of the `customer` entity). When MetricFlow can reach the same attribute through several join paths, Prumi shows only the shortest one for each metric, so agents don't see near-duplicates.
