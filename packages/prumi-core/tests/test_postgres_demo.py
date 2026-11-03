"""The server on the Postgres demo, started from a prumi.toml the way a user would."""

import logging
import os

import pytest
from prumi.cli import REFUSED, main
from prumi.config import load_config
from prumi.server.app import create_app
from prumi.validate import page as page_check
from starlette.testclient import TestClient

from .conftest import SuiteServices, write_postgres_demo_config

SENTINEL = "SENTINEL-7f3a9c"


def all_text(caplog) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(repr(record.args))
        if record.exc_info:
            parts.append(logging.Formatter().formatException(record.exc_info))
    return "\n".join(parts)


def assert_refused_without_the_sentinel(path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    assert main(["serve", "--config", str(path)]) == REFUSED
    err = capsys.readouterr().err
    assert err.startswith("prumi:")
    assert "PGPASSWORD" in err
    assert SENTINEL not in err
    assert SENTINEL not in all_text(caplog)


@pytest.fixture
def pg_services(tmp_path, postgres_demo):
    return SuiteServices(load_config(write_postgres_demo_config(tmp_path, postgres_demo)))


@pytest.mark.browser
def test_the_demo_dashboard_publishes_on_postgres(pg_services, demo_dashboard):
    try:
        pg_services.checker.start()
    except page_check.BrowserStartupError:
        if os.environ.get("PRUMI_REQUIRE_BROWSER"):
            raise
        pytest.skip("Headless Chromium can't start; run `uv run prumi install-chromium`.")
    report, published = pg_services.publish(*demo_dashboard)
    assert report.ok, report.issues
    assert not report.issues
    assert published is not None


def test_the_home_page_shows_data_through_on_postgres(pg_services):
    client = TestClient(create_app(pg_services), base_url="http://127.0.0.1:8484")
    assert "data through 2026-10-14" in client.get("/").text


def test_serve_with_a_wrong_password_never_shows_it(
    tmp_path, postgres_demo, monkeypatch, capsys, caplog
):
    monkeypatch.setenv("PGPASSWORD", SENTINEL)
    path = write_postgres_demo_config(tmp_path, postgres_demo)
    assert_refused_without_the_sentinel(path, capsys, caplog)


def test_a_password_in_prumi_toml_is_refused(tmp_path, demo_project, capsys, caplog):
    path = tmp_path / "prumi.toml"
    path.write_text(
        f"""
[server]
port = 8484
data_dir = "data"
as_of = "2026-10-15"

[source]
adapter = "dbt"

[source.dbt]
semantic_manifest = "{demo_project.manifest_path.as_posix()}"

[source.dbt.connection]
type = "postgres"
host = "127.0.0.1"
port = 1
dbname = "demo"
user = "reader"
password = "{SENTINEL}"
"""
    )
    caplog.set_level(logging.DEBUG)
    assert main(["serve", "--config", str(path)]) == REFUSED
    err = capsys.readouterr().err
    assert err.startswith("prumi:")
    assert "password" in err
    assert SENTINEL not in err
    assert SENTINEL not in all_text(caplog)


def test_a_manifest_for_another_postgres_database_is_refused_on_reload(
    tmp_path, postgres_demo, caplog
):
    from prumi.adapters import AdapterStartupError
    from prumi.reload import watcher_for

    caplog.set_level(logging.DEBUG)
    manifest = tmp_path / "semantic_manifest.json"
    manifest.write_text(postgres_demo.manifest_path.read_text())
    path = write_postgres_demo_config(tmp_path, postgres_demo)
    path.write_text(
        path.read_text().replace(postgres_demo.manifest_path.as_posix(), manifest.as_posix())
    )
    services = SuiteServices(load_config(path))
    watcher = watcher_for(services)
    snapshot = services.current()

    manifest.write_text(manifest.read_text().replace(postgres_demo.dbname, "other_db"))
    stat = manifest.stat()
    os.utime(manifest, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    watcher.check()
    watcher.check()

    assert services.current() is snapshot
    [warning] = [
        r.getMessage()
        for r in caplog.records
        if r.name == "prumi.reload" and r.levelno == logging.WARNING
    ]
    with pytest.raises(AdapterStartupError) as startup:
        SuiteServices(services.config).source  # noqa: B018 - the same file, loaded at startup
    assert str(startup.value) in warning
