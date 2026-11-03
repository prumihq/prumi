"""The source watcher: when it reloads, when it waits, and what it says."""

import logging
import threading
import time
from pathlib import Path

import pytest
from prumi.adapters import AdapterStartupError
from prumi.reload import RETRY_S, SourceWatcher, watcher_for

NAME = "semantic_manifest.json"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Loader:
    """Stands in for building a source; records calls, fails as told."""

    def __init__(self, fake_source, *failures: Exception) -> None:
        self.source = fake_source
        self.failures = list(failures)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.source


@pytest.fixture(autouse=True)
def _log_everything(caplog):
    caplog.set_level(logging.DEBUG)


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / NAME
    path.write_text("{}")
    return path


@pytest.fixture
def clock() -> Clock:
    return Clock()


def watcher(manifest, loader, swapped, clock) -> SourceWatcher:
    return SourceWatcher([manifest], loader, swapped.append, clock)


def write(path: Path, text: str) -> None:
    """Change the file in a way its signature shows, whatever the timestamp granularity."""
    path.write_text(text)


def records(caplog, level=None):
    return [r for r in caplog.records if r.name == "prumi.reload" and level in (None, r.levelno)]


def test_nothing_happens_while_the_files_dont_change(manifest, fake_source, clock, caplog):
    loader, swapped = Loader(fake_source), []
    w = watcher(manifest, loader, swapped, clock)
    for _ in range(5):
        w.check()
    assert loader.calls == 0 and swapped == []
    assert records(caplog) == []


def test_a_change_is_loaded_once_it_is_the_same_on_two_polls(manifest, fake_source, clock):
    loader, swapped = Loader(fake_source), []
    w = watcher(manifest, loader, swapped, clock)
    write(manifest, '{"changed": true}')
    w.check()
    assert loader.calls == 0
    w.check()
    assert loader.calls == 1 and swapped == [fake_source]
    w.check()
    assert loader.calls == 1


def test_a_file_still_being_written_is_not_loaded(manifest, fake_source, clock):
    loader, swapped = Loader(fake_source), []
    w = watcher(manifest, loader, swapped, clock)
    write(manifest, '{"part": 1')
    w.check()
    write(manifest, '{"part": 1, "more": 2, "even more": 3}')
    w.check()
    assert loader.calls == 0
    w.check()
    assert loader.calls == 1


def test_a_successful_reload_logs_one_info_line(manifest, fake_source, clock, caplog):
    w = watcher(manifest, Loader(fake_source), [], clock)
    write(manifest, '{"changed": true}')
    w.check()
    w.check()
    infos = records(caplog, logging.INFO)
    assert [r.getMessage() for r in infos] == [
        f"reloaded the source after {NAME} changed: {len(fake_source.metric_names)} metrics"
    ]
    assert records(caplog) == infos


def test_the_info_line_says_the_metric_count(manifest, fake_source, clock, caplog):
    w = watcher(manifest, Loader(fake_source), [], clock)
    write(manifest, '{"changed": true}')
    w.check()
    w.check()
    assert records(caplog)[0].getMessage().endswith(f": {len(fake_source.metric_names)} metrics")
    assert len(fake_source.metric_names) > 0


def test_a_failed_reload_keeps_the_current_source_and_warns_once(
    manifest, fake_source, clock, caplog
):
    reason = "The semantic manifest isn't valid JSON."
    loader, swapped = Loader(fake_source, AdapterStartupError(reason)), []
    w = watcher(manifest, loader, swapped, clock)
    write(manifest, "{")
    w.check()
    w.check()
    assert swapped == []
    warnings = records(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert warnings[0].getMessage() == (
        f"{NAME} changed, but the source wasn't reloaded; keeping the current one: {reason}"
    )
    for _ in range(5):
        w.check()
    assert len(records(caplog)) == 1


def test_a_failed_reload_is_retried_every_30_seconds_without_a_new_warning(
    manifest, fake_source, clock, caplog
):
    loader, swapped = Loader(fake_source, AdapterStartupError("nope")), []
    w = watcher(manifest, loader, swapped, clock)
    write(manifest, "{")
    w.check()
    w.check()
    assert loader.calls == 1
    clock.now += RETRY_S - 1
    w.check()
    assert loader.calls == 1
    clock.now += 2
    w.check()
    assert loader.calls == 2 and swapped == [fake_source]
    assert len(records(caplog, logging.INFO)) == 1
    assert len(records(caplog, logging.WARNING)) == 1
    clock.now += 10 * RETRY_S
    w.check()
    assert loader.calls == 2


def test_a_retry_that_fails_again_stays_quiet_and_waits_again(manifest, fake_source, clock, caplog):
    loader = Loader(fake_source, AdapterStartupError("a"), AdapterStartupError("b"))
    w = watcher(manifest, loader, [], clock)
    write(manifest, "{")
    w.check()
    w.check()
    clock.now += RETRY_S + 1
    w.check()
    assert loader.calls == 2
    clock.now += RETRY_S - 1
    w.check()
    assert loader.calls == 2
    assert len(records(caplog, logging.WARNING)) == 1


def test_a_new_version_after_a_failure_warns_again(manifest, fake_source, clock, caplog):
    loader = Loader(fake_source, AdapterStartupError("first"), AdapterStartupError("second"))
    swapped = []
    w = watcher(manifest, loader, swapped, clock)
    write(manifest, "{")
    w.check()
    w.check()
    write(manifest, "{ {")
    w.check()
    w.check()
    assert loader.calls == 2 and swapped == []
    write(manifest, "{}  ")  # then a good file is loaded
    w.check()
    w.check()
    assert loader.calls == 3 and swapped == [fake_source]
    messages = [r.getMessage() for r in records(caplog, logging.WARNING)]
    assert len(messages) == 2
    assert messages[0].endswith(": first") and messages[1].endswith(": second")


def test_a_missing_file_is_a_failed_reload(manifest, fake_source, clock, caplog):
    loader = Loader(fake_source, AdapterStartupError("The semantic manifest is missing."))
    swapped = []
    w = watcher(manifest, loader, swapped, clock)
    manifest.unlink()
    w.check()
    w.check()
    assert loader.calls == 1 and swapped == []
    assert len(records(caplog, logging.WARNING)) == 1


def test_a_failure_that_isnt_a_startup_error_is_logged_by_type_only(
    manifest, fake_source, clock, caplog
):
    loader = Loader(fake_source, RuntimeError("password=sentinel-secret"))
    w = watcher(manifest, loader, [], clock)
    write(manifest, "{")
    w.check()
    w.check()
    warnings = records(caplog, logging.WARNING)
    assert len(warnings) == 1 and warnings[0].getMessage().endswith(": RuntimeError")
    assert all("sentinel-secret" not in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)


def test_a_failing_swap_is_a_failed_reload_not_a_dead_watcher(manifest, fake_source, clock, caplog):
    def swap(source):
        raise RuntimeError("password=sentinel-secret")

    w = SourceWatcher([manifest], Loader(fake_source), swap, clock)
    write(manifest, "{")
    w.check()
    w.check()
    assert records(caplog, logging.WARNING)[0].getMessage().endswith(": RuntimeError")
    assert records(caplog, logging.INFO) == []


def test_a_stat_error_counts_as_missing_and_the_watcher_keeps_going(
    manifest, fake_source, clock, monkeypatch
):
    loader, swapped = Loader(fake_source), []
    w = watcher(manifest, loader, swapped, clock)
    real_stat = Path.stat
    raised = []

    def flaky(self, *args, **kwargs):
        if not raised:
            raised.append(True)
            raise PermissionError("no")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky)
    w.check()  # sees "missing": a change
    w.check()  # the file is back and differs from "missing": another change
    w.check()  # stable
    assert raised and loader.calls == 1


def test_names_of_several_changed_files_are_joined(tmp_path, fake_source, clock, caplog):
    a, b, c = (tmp_path / n for n in ("a.json", "b.json", "c.json"))
    for p in (a, b, c):
        p.write_text("{}")
    w = SourceWatcher([a, b, c], Loader(fake_source), [].append, clock)
    write(a, '{"x": 1}')
    write(c, '{"x": 1}')
    w.check()
    w.check()
    assert records(caplog)[0].getMessage().startswith("reloaded the source after a.json, c.json ")


def test_the_thread_stops_when_asked(manifest, fake_source, clock):
    w = watcher(manifest, Loader(fake_source), [], clock)
    w.start(interval=0.01)
    threads = [t for t in threading.enumerate() if t.daemon and t.name == "prumi-reload"]
    assert threads
    started = time.monotonic()
    w.stop()
    assert time.monotonic() - started < 1
    assert not any(t.is_alive() for t in threads)


def test_the_thread_reloads_a_changed_file(manifest, fake_source, clock):
    swapped = []
    w = watcher(manifest, Loader(fake_source), swapped, clock)
    w.start(interval=0.01)
    try:
        write(manifest, '{"changed": true}')
        deadline = time.monotonic() + 5
        while not swapped and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        w.stop()
    assert swapped == [fake_source]


def test_no_watcher_for_an_adapter_that_watches_nothing(demo_services, fake_source):
    demo_services.swap(fake_source)
    assert watcher_for(demo_services) is None


def test_the_watcher_for_a_watching_adapter_swaps_into_the_services(
    demo_services, demo_source, fake_source, clock
):
    watcher = watcher_for(demo_services, clock)
    assert watcher is not None
    assert watcher._swap == demo_services.swap
