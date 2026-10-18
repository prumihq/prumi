"""Fixtures for core tests: a fake source with a demo-like catalog, no warehouse needed."""

import datetime as dt
import socket
import threading
import time
from pathlib import Path

import pytest
from prumi.adapters import (
    METRIC_TIME,
    Capabilities,
    Catalog,
    CatalogWarning,
    Column,
    Dimension,
    Format,
    Metric,
    Result,
)
from prumi.source import Source

AS_OF = dt.date(2026, 10, 15)
USD = Format("currency", "USD", 2)
SALES = frozenset({"customer__region", "customer__country", "customer__segment", "order__channel"})


def _metric(name, kind="simple", additive=False, fmt=None, aggregation=None) -> Metric:
    return Metric(
        name=name,
        label=name.replace("_", " ").capitalize(),
        description=f"The {name}.",
        kind=kind,
        aggregation=aggregation,
        additive=additive,
        format=fmt or Format(),
        owner="sales-analytics",
        filter=None,
    )


METRICS = {
    "revenue": (
        _metric("revenue", additive=True, fmt=USD, aggregation="sum"),
        SALES | {"product__category"},
    ),
    "orders": (
        _metric("orders", additive=True, fmt=Format("integer", "orders"), aggregation="count"),
        SALES,
    ),
    "average_order_value": (_metric("average_order_value", "ratio", fmt=USD), SALES),
    "customers": (
        _metric("customers", fmt=Format("integer", "customers"), aggregation="count_distinct"),
        SALES,
    ),
    "gross_margin_pct": (
        _metric("gross_margin_pct", "ratio", fmt=Format("percent", None, 1)),
        SALES | {"product__category"},
    ),
    "units_on_hand": (
        _metric("units_on_hand", fmt=Format("integer", "units"), aggregation="sum"),
        frozenset({"product__category"}),
    ),
    "discount_rate": (_metric("discount_rate", "ratio"), SALES | {"product__category"}),
    # Valid in the catalog, but the warehouse fails every query for it.
    "broken_metric": (_metric("broken_metric", fmt=Format("integer")), SALES),
    # More rows than the engine's cap for any grouped query.
    "big_metric": (_metric("big_metric", fmt=Format("integer")), SALES),
    # Its data stops early, so recent windows are stale.
    "late_metric": (_metric("late_metric", fmt=Format("integer")), SALES),
}
DATA_THROUGH = {"late_metric": dt.date(2026, 10, 10)}  # the others: 2026-10-14


class FakeAdapter:
    """Answers every query with one row of placeholder values, like a tiny warehouse."""

    name = "fake"

    def __init__(self):
        self.queries = []

    def capabilities(self):
        return Capabilities(identity_mode="shared_account")

    def discover(self):
        dims = sorted(set().union(*(d for _, d in METRICS.values())))
        return Catalog(
            metrics=tuple(m for m, _ in METRICS.values()),
            dimensions=(
                *(Dimension(d, d.split("__")[-1].capitalize(), "") for d in dims),
                Dimension(METRIC_TIME, "Time", ""),
            ),
            warnings=(CatalogWarning("R10.no_format", "discount_rate", "No format declared."),),
        )

    def relations(self, metrics):
        if not metrics:
            return frozenset().union(*(d for _, d in METRICS.values()))
        return frozenset.intersection(*(METRICS[m][1] for m in metrics))

    def execute(self, query, viewer):
        self.queries.append(query)
        if "broken_metric" in query.metrics:
            raise RuntimeError("the warehouse refused the query")
        columns = (
            *((Column(METRIC_TIME, "time"),) if query.time_grain else ()),
            *(Column(d, "dimension") for d in query.dimensions),
            *(Column(m, "metric") for m in query.metrics),
        )
        if query.window is not None:
            start = query.window.start
        else:  # the engine's freshness query
            start = min(DATA_THROUGH.get(m, dt.date(2026, 10, 14)) for m in query.metrics)
        row = tuple(
            start if c.kind == "time" else "x" if c.kind == "dimension" else 1.0 for c in columns
        )
        grouped = query.dimensions or query.time_grain
        rows = (row,) * (5001 if "big_metric" in query.metrics and grouped else 1)
        rows = rows[: query.limit] if query.limit else rows
        return Result(columns=columns, rows=rows, row_count=len(rows))


@pytest.fixture
def as_of():
    return AS_OF


@pytest.fixture
def fake_adapter():
    return FakeAdapter()


@pytest.fixture
def fake_source(fake_adapter):
    return Source("default", fake_adapter)


@pytest.fixture(scope="session")
def demo_config(demo_project):
    from prumi.config import load_config

    return load_config(demo_project.config_path)


@pytest.fixture(scope="session")
def demo_source(demo_config):
    """The demo's dbt source, loaded through the adapter's entry point like the server does."""
    return Source.from_config(demo_config.sources["default"])


DEMO_DASHBOARD = Path(__file__).parent / "fixtures" / "demo"


def write_demo_config(directory: Path, demo_project, manifest: Path | None = None) -> Path:
    """A prumi.toml for the demo source with its own data dir under `directory`."""
    path = directory / "prumi.toml"
    path.write_text(
        f"""
[server]
port = 8484
data_dir = "data"
as_of = "2026-10-15"

[source]
adapter = "dbt"

[source.dbt]
semantic_manifest = "{(manifest or demo_project.manifest_path).as_posix()}"

[source.dbt.connection]
type = "duckdb"
path = "{demo_project.db_path.as_posix()}"
"""
    )
    return path


@pytest.fixture
def demo_services(tmp_path, demo_project, demo_source):
    """Services on the demo source with an empty registry of their own."""
    from prumi.config import load_config
    from prumi.services import Services

    services = Services(load_config(write_demo_config(tmp_path, demo_project)))
    services.source = demo_source  # reuse the session's loaded adapter
    return services


@pytest.fixture
def make_demo_config(demo_project):
    return lambda directory, manifest=None: write_demo_config(directory, demo_project, manifest)


@pytest.fixture
def demo_dashboard():
    """The demo dashboard's spec (as data) and HTML."""
    from prumi.spec import load_spec_file

    return load_spec_file(DEMO_DASHBOARD / "sales.yaml"), (
        DEMO_DASHBOARD / "sales.html"
    ).read_text()


@pytest.fixture
def published(demo_services, demo_dashboard):
    """Demo services with the demo dashboard published as version 1."""
    report, version = demo_services.publish(*demo_dashboard)
    assert report.ok and version.version == 1, report.issues
    return demo_services


@pytest.fixture
def client(published):
    from prumi.server.app import create_app
    from starlette.testclient import TestClient

    return TestClient(create_app(published), base_url="http://127.0.0.1:8484")


# A live server in a thread, for browser and MCP tests


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """uvicorn on the services' port, in a thread."""

    def __init__(self, services) -> None:
        import uvicorn
        from prumi.server.app import create_app

        port = services.config.server.port
        self.url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(
            create_app(services), host="127.0.0.1", port=port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the test server didn't start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture(scope="module")
def live_services(tmp_path_factory, demo_project, demo_source):
    """Builds demo services on a free port, with their own data dir."""
    from prumi.config import load_config
    from prumi.services import Services

    def make():
        directory = tmp_path_factory.mktemp("live")
        path = write_demo_config(directory, demo_project)
        path.write_text(path.read_text().replace("port = 8484", f"port = {free_port()}"))
        services = Services(load_config(path))
        services.source = demo_source
        return services

    return make


@pytest.fixture(scope="module")
def live_server():
    return LiveServer
