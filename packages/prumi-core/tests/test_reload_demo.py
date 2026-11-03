"""Reloading the demo's dbt manifest while the server runs: what the watcher does end to end."""

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

import duckdb
import pytest
from mcp import Client
from prumi.adapters import AdapterStartupError
from prumi.cli import main
from prumi.config import load_config
from prumi.reload import RETRY_S, SourceWatcher, watcher_for

from .conftest import SuiteServices, free_port


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _log_everything(caplog):
    caplog.set_level(logging.DEBUG)


@pytest.fixture
def reload_logger():
    """serve() attaches a stream handler to this logger; each test starts without one."""
    logger = logging.getLogger("prumi.reload")
    handlers, level = logger.handlers[:], logger.level
    logger.handlers[:] = []
    yield logger
    logger.handlers[:] = handlers
    logger.setLevel(level)


@pytest.fixture
def manifest_copy(tmp_path, demo_project) -> Path:
    copy = tmp_path / "semantic_manifest.json"
    shutil.copy(demo_project.manifest_path, copy)
    return copy


def services_for(tmp_path, make_demo_config, manifest, **replacements) -> SuiteServices:
    path = make_demo_config(tmp_path, manifest)
    text = path.read_text()
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text)
    return SuiteServices(load_config(path))


def edit(path: Path, change) -> None:
    """Rewrite the manifest as `change(data)` says; the mtime moves even on a coarse clock."""
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def check_twice(watcher: SourceWatcher) -> None:
    watcher.check()  # sees the change
    watcher.check()  # sees it hold still: loads


def warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "prumi.reload" and r.levelno == logging.WARNING
    ]


def relabel_revenue(data) -> None:
    next(m for m in data["metrics"] if m["name"] == "revenue")["label"] = "Revenue (net)"


def find_metrics(url: str, question: str) -> list[dict]:
    async def run():
        async with Client(f"{url}/mcp") as client:
            result = await client.call_tool("find_metrics", {"question": question})
            assert not result.is_error, result.content
            return result.structured_content["metrics"]

    return asyncio.run(run())


def test_a_changed_metric_label_reaches_the_mcp_catalog_without_a_restart(
    tmp_path, make_demo_config, manifest_copy, live_server
):
    services = services_for(
        tmp_path, make_demo_config, manifest_copy, **{"port = 8484": f"port = {free_port()}"}
    )
    watcher = watcher_for(services)
    with live_server(services) as live:
        before = next(m for m in find_metrics(live.url, "revenue") if m["name"] == "revenue")
        assert before["label"] == "Revenue"
        edit(manifest_copy, relabel_revenue)
        check_twice(watcher)
        after = next(m for m in find_metrics(live.url, "revenue") if m["name"] == "revenue")
        assert after["label"] == "Revenue (net)"


def test_a_manifest_that_doesnt_parse_keeps_the_old_catalog(
    tmp_path, make_demo_config, manifest_copy, caplog
):
    services = services_for(tmp_path, make_demo_config, manifest_copy)
    watcher = watcher_for(services)
    snapshot = services.current()
    manifest_copy.write_text("{")
    bump = manifest_copy.stat().st_mtime_ns + 1_000_000_000
    os.utime(manifest_copy, ns=(bump, bump))
    check_twice(watcher)
    check_twice(watcher)
    assert services.current() is snapshot
    [warning] = warnings(caplog)
    with pytest.raises(AdapterStartupError) as startup:
        SuiteServices(services.config).source  # noqa: B018 - the same file, loaded at startup
    assert str(startup.value) in warning
    assert manifest_copy.name in warning


def test_a_manifest_naming_a_missing_table_loads_once_the_table_exists(
    tmp_path, make_demo_config, demo_project, manifest_copy, caplog
):
    database = tmp_path / "demo.duckdb"
    shutil.copy(demo_project.db_path, database)
    services = services_for(
        tmp_path,
        make_demo_config,
        manifest_copy,
        **{demo_project.db_path.as_posix(): database.as_posix()},
    )
    clock = Clock()
    watcher = watcher_for(services, clock)
    snapshot = services.current()

    def point_customers_elsewhere(data) -> None:
        for model in data["semantic_models"]:
            if model["name"] == "customers":
                model["node_relation"]["relation_name"] = '"demo"."main"."customers_v2"'
                model["node_relation"]["alias"] = "customers_v2"
        relabel_revenue(data)

    edit(manifest_copy, point_customers_elsewhere)
    check_twice(watcher)
    assert services.current() is snapshot
    [warning] = warnings(caplog)
    assert "customers_v2" in warning

    with duckdb.connect(str(database)) as conn:
        conn.execute("CREATE TABLE main.customers_v2 AS SELECT * FROM main.customers")
    clock.now += RETRY_S + 1
    watcher.check()
    assert services.current() is not snapshot
    assert services.current().source.metric_names == snapshot.source.metric_names
    assert len(warnings(caplog)) == 1


class FakeChecker:
    """Stands in for headless Chromium, which these tests don't need."""

    def start(self) -> None:
        pass

    def supports_locale(self, locale: str) -> bool:
        return True

    def close(self) -> None:
        pass


@pytest.fixture
def serving(tmp_path, make_demo_config, manifest_copy, monkeypatch, reload_logger):
    """A config for `prumi serve` on the manifest copy, with the browser and uvicorn faked.
    `run` is what the fake uvicorn.run calls; the watcher polls every 50 ms."""
    monkeypatch.setattr("prumi.services.shared_checker", FakeChecker)
    start = SourceWatcher.start
    monkeypatch.setattr(SourceWatcher, "start", lambda self, interval=0.05: start(self, interval))

    def serve(run) -> int:
        monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: run())
        path = make_demo_config(tmp_path, manifest_copy)
        return main(["serve", "--config", str(path)])

    return serve


def watcher_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "prumi-reload" and t.is_alive()]


def test_serve_starts_and_stops_the_watcher(serving):
    during = []
    assert serving(lambda: during.append(len(watcher_threads()))) == 0
    assert during == [1]
    assert watcher_threads() == []


def test_serve_prints_a_reload_to_stderr(serving, manifest_copy, capsys):
    printed = []

    def reload_it() -> None:
        edit(manifest_copy, relabel_revenue)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            printed.append(capsys.readouterr().err)
            if "reloaded the source" in "".join(printed):
                return
            time.sleep(0.05)

    assert serving(reload_it) == 0
    err = "".join(printed) + capsys.readouterr().err
    assert "reloaded the source after semantic_manifest.json changed:" in err
