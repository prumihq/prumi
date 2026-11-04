#!/bin/sh
# The demo image's entrypoint: one welcome link, then the server. Arguments go to `prumi serve`.
set -e
echo "Prumi demo: what's in the shop, how to connect your agent and what to ask: https://github.com/prumihq/prumi/blob/main/examples/demo-dbt-project/README.md#try-prumi-on-it"
exec prumi serve --config /demo/prumi.toml "$@"
