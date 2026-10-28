# prumi

The Prumi server and the `prumi` CLI. AI agents connect to its MCP endpoint and build dashboards on a company's official metrics; Prumi checks every page, hosts it, and lets the data team certify it.

Import name: `prumi`. It holds the catalog, the query engine, the BI validator and the registry, and defines the adapter protocol that source adapters implement. Prumi reads metrics through an adapter package; the first one is [`prumi-adapter-dbt`](https://github.com/prumihq/prumi/tree/main/packages/prumi-adapter-dbt).

- **Agents** connect over MCP (Streamable HTTP, `/mcp`), find the official metrics, learn the shape of the data (never its values) and publish a dashboard: a spec plus any HTML.
- **Numbers** come only from the source, computed at the grain they're shown. Validation renders each page in headless Chromium and checks that every number it shows is a value Prumi delivered.
- **Dashboards** run in a sandboxed frame inside Prumi's own page, which shows the trust badge and data freshness. The data team certifies a version from the CLI; any later change makes it a draft again.

## Install

With [uv](https://docs.astral.sh/uv/), Prumi and its dbt adapter:

```sh
uv tool install "prumi[dbt]"
prumi install-chromium
prumi serve --config prumi.toml
```

`pip install "prumi[dbt]"` installs the same packages. `prumi.toml` points at your dbt project's semantic manifest and warehouse: see [Use it with your metrics](https://github.com/prumihq/prumi#use-it-with-your-metrics) and [every setting](https://github.com/prumihq/prumi/blob/main/docs/configuration.md).

Requirements: Python 3.12 or later, and headless Chromium, which `prumi install-chromium` installs with Playwright (add `--with-deps` on Linux if system libraries are missing); the server won't start without it.

A [five-minute trial on a demo project](https://github.com/prumihq/prumi/blob/main/examples/demo-dbt-project/README.md#try-prumi-on-it) needs no project of your own. Configuration and where data goes are in [the Prumi README](https://github.com/prumihq/prumi#readme); the BI rules Prumi checks are in [`docs/bi-rules.md`](https://github.com/prumihq/prumi/blob/main/docs/bi-rules.md).
