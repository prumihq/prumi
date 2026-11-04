"""PRUMI_CONTAINER=1: the container image's host default and its loopback rule."""

import logging
from pathlib import Path

import pytest
from prumi.config import ConfigError, load_config
from prumi.container import (
    ENV,
    LOOPBACK_IN_CONTAINER_WARNING,
    in_container,
    log_open_hint,
    warn_if_unreachable,
)

MINIMAL = """
[source]
adapter = "dbt"
"""

NON_LOOPBACK_MESSAGE = (
    "'0.0.0.0' isn't a loopback address. Prumi has no login in this release, so it "
    "only listens on 127.0.0.1, ::1 or localhost; anything else would expose the "
    "warehouse to the network"
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "prumi.toml"
    path.write_text(text)
    return path


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1"])
def test_only_1_turns_the_container_mode_on(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, value)
    assert in_container() is False
    monkeypatch.setenv(ENV, "1")
    assert in_container() is True


def test_outside_a_container_the_default_host_is_loopback(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV, raising=False)
    assert load_config(write(tmp_path, MINIMAL)).server.host == "127.0.0.1"


def test_in_a_container_the_default_host_is_every_address(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV, "1")
    assert load_config(write(tmp_path, MINIMAL)).server.host == "0.0.0.0"


def test_in_a_container_a_non_loopback_host_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV, "1")
    config = load_config(write(tmp_path, '[server]\nhost = "0.0.0.0"\n' + MINIMAL))
    assert config.server.host == "0.0.0.0"


def test_outside_a_container_a_non_loopback_host_is_still_refused(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(ConfigError) as exc:
        load_config(write(tmp_path, '[server]\nhost = "0.0.0.0"\n' + MINIMAL))
    assert NON_LOOPBACK_MESSAGE in str(exc.value)
    assert "PRUMI_CONTAINER" not in str(exc.value)


def test_in_a_container_a_loopback_host_logs_a_warning(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv(ENV, "1")
    server = load_config(write(tmp_path, '[server]\nhost = "127.0.0.1"\n' + MINIMAL)).server
    with caplog.at_level(logging.WARNING, logger="prumi.server"):
        warn_if_unreachable(server)
    assert [r.getMessage() for r in caplog.records] == [LOOPBACK_IN_CONTAINER_WARNING]


def test_no_warning_outside_a_container_or_for_a_reachable_host(monkeypatch, caplog, tmp_path):
    monkeypatch.delenv(ENV, raising=False)
    server = load_config(write(tmp_path, MINIMAL)).server
    with caplog.at_level(logging.WARNING, logger="prumi.server"):
        warn_if_unreachable(server)
        monkeypatch.setenv(ENV, "1")
        warn_if_unreachable(load_config(write(tmp_path, MINIMAL)).server)
    assert caplog.records == []


def test_in_a_container_serving_logs_the_address_to_open_on_the_host(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv(ENV, "1")
    server = load_config(write(tmp_path, "[server]\nport = 9000\n" + MINIMAL)).server
    with caplog.at_level(logging.INFO, logger="prumi.container"):
        log_open_hint(server)
    assert [r.getMessage() for r in caplog.records] == [
        "Open Prumi at http://127.0.0.1:9000 (or the host port you gave -p); "
        "the 0.0.0.0 address uvicorn prints below is the container's own."
    ]


def test_no_address_hint_outside_a_container_or_for_a_loopback_host(monkeypatch, caplog, tmp_path):
    monkeypatch.delenv(ENV, raising=False)
    with caplog.at_level(logging.INFO, logger="prumi.container"):
        log_open_hint(load_config(write(tmp_path, MINIMAL)).server)
        monkeypatch.setenv(ENV, "1")
        config = load_config(write(tmp_path, '[server]\nhost = "127.0.0.1"\n' + MINIMAL))
        log_open_hint(config.server)
    assert caplog.records == []
