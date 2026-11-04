# check=skip=InvalidDefaultArgInFrom
# The demo shop on Prumi: the main image plus the demo's DuckDB warehouse and manifest.
#
# Build context: the repository root.
#   docker build -f deploy/demo-image/Dockerfile --build-arg PRUMI_IMAGE=<main image> .
# Run it published on loopback only (Prumi has no login yet):
#   docker run -p 127.0.0.1:8484:8484 <image>
# Dashboards live in /data; mount a volume there to keep them.

# Required: the main image to build on, of the same version (no default on purpose).
ARG PRUMI_IMAGE

# Throwaway: dbt builds the warehouse and the semantic manifest. Neither dbt nor uv reaches the
# final image, which only reads the two files. They're the same on every platform, so this
# stage runs natively on the build machine, also when building for another architecture.
FROM --platform=$BUILDPLATFORM python:3.12-slim AS demo
RUN pip install --no-cache-dir --disable-pip-version-check uv==0.13.1
WORKDIR /src
COPY examples/demo-dbt-project examples/demo-dbt-project
RUN uv run --project examples/demo-dbt-project python examples/demo-dbt-project/build.py

FROM ${PRUMI_IMAGE}
# demo.duckdb keeps its name: DuckDB names the database after the file, and the manifest's
# SQL refers to it by that name.
COPY --from=demo /src/examples/demo-dbt-project/demo.duckdb /demo/demo.duckdb
COPY --from=demo /src/examples/demo-dbt-project/target/semantic_manifest.json \
    /demo/target/semantic_manifest.json
COPY deploy/demo-image/prumi.toml /demo/prumi.toml
COPY --chmod=755 deploy/demo-image/start.sh /demo/start.sh

USER root
RUN install -d -o prumi -g prumi /data
USER prumi
VOLUME /data

# So `docker exec <container> prumi list` finds /demo/prumi.toml as ./prumi.toml.
WORKDIR /demo
ENTRYPOINT ["/demo/start.sh"]
CMD []
