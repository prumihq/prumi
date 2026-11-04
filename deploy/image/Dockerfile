# Prumi with the dbt adapter (DuckDB and Postgres drivers) and Chromium.
#
# Build context: the repository root. Install from one of:
#   --build-arg PRUMI_VERSION=0.3.0   prumi[dbt] at that version, from PyPI
#   --build-arg PRUMI_WHEELS=dist     the prumi and prumi-adapter-dbt wheels in that folder
#
# Run it with your prumi.toml mounted at /prumi/prumi.toml, published on loopback only:
#   docker run -p 127.0.0.1:8484:8484 -v ./prumi.toml:/prumi/prumi.toml:ro <image>
# Inside the container Prumi listens on every address; the 127.0.0.1 in -p is what keeps it
# off the network (docs/adr/0017).

FROM python:3.12-slim

ARG PRUMI_VERSION=
ARG PRUMI_WHEELS=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PRUMI_CONTAINER=1

# Both wheels are named explicitly, so pip takes prumi and prumi-adapter-dbt from them and
# never from PyPI; only their other dependencies come from the index.
RUN --mount=type=bind,source=.,target=/context \
    set -eu; \
    if [ -n "$PRUMI_WHEELS" ]; then \
        set -- /context/"$PRUMI_WHEELS"/prumi-*-py3-none-any.whl \
            /context/"$PRUMI_WHEELS"/prumi_adapter_dbt-*-py3-none-any.whl; \
        if [ $# != 2 ] || [ ! -f "$1" ] || [ ! -f "$2" ]; then \
            echo "expected one prumi and one prumi-adapter-dbt wheel in $PRUMI_WHEELS" >&2; \
            exit 1; \
        fi; \
        pip install --no-cache-dir "prumi[dbt] @ file://$1" \
            "prumi-adapter-dbt[duckdb,postgres] @ file://$2"; \
    elif [ -n "$PRUMI_VERSION" ]; then \
        pip install --no-cache-dir "prumi[dbt]==$PRUMI_VERSION"; \
    else \
        echo "set the build arg PRUMI_VERSION or PRUMI_WHEELS" >&2; \
        exit 1; \
    fi

# Chromium and its system libraries, with Playwright's installer (root is needed for the
# libraries), into a folder every user can read. Prumi only runs Chromium headless, so the
# headless shell is enough: the full browser would add about 400 MB.
RUN python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin prumi \
    && install -d -o prumi -g prumi /prumi

USER prumi
WORKDIR /prumi
EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/', timeout=4)"]

ENTRYPOINT ["prumi"]
CMD ["serve"]
