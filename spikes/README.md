# Spikes

Throwaway experiments. Each spike answers one question, lives in its own folder with a `README.md` stating the question and the answer, and is never imported by `packages/`.

| Spike | Question | Answer |
|---|---|---|
| [`metricflow-compile`](metricflow-compile/) | Can MetricFlow compile a query from the dbt semantic manifest while Prumi executes the SQL over its own connection? | Yes, with caveats ([ADR 0002](../docs/adr/0002-dbt-adapter-metricflow-compiles.md), Accepted) |
