"""Reloading the source while the server runs.

An adapter names the files whose change means its source changed (`watched_files`). The
watcher polls them, waits for a change to settle, builds a new source (every startup check
runs again) and swaps it in. A source that fails the checks is never swapped in: the current
one keeps serving, and the watcher says why once.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from prumi.adapters import AdapterStartupError, watched_files_of
from prumi.config import DEFAULT_SOURCE
from prumi.services import Services
from prumi.source import Source

__all__ = ["POLL_S", "RETRY_S", "STOP_WAIT_S", "SourceWatcher", "watcher_for"]

log = logging.getLogger("prumi.reload")

POLL_S = 2.0  # how often the files are looked at
RETRY_S = 30.0  # how long to wait before trying the same failed files again
STOP_WAIT_S = 5.0  # how long stop() waits for a reload that's under way

# (mtime in ns, size, content digest), or None when the file can't be read
Signature = tuple[int, int, bytes] | None


def _signature(path: Path) -> Signature:
    # The digest too: where timestamps are coarse, a quick rewrite of the same size keeps the
    # old time. Hashing a manifest of a few MB every poll costs milliseconds.
    try:
        stat = path.stat()
        with path.open("rb") as file:
            digest = hashlib.file_digest(file, "blake2b").digest()
    except OSError:  # missing, or unreadable right now: either way there is nothing to load
        return None
    return (stat.st_mtime_ns, stat.st_size, digest)


class SourceWatcher:
    """Polls files; when they change and then hold still for one more poll, loads a new
    source and hands it to `swap`.

    A change that is the same on two polls in a row counts as written: a file still being
    written differs between polls. A failed load is retried every `RETRY_S` while the files
    stay as they were, and warned about once per version of the files."""

    def __init__(
        self,
        files: Sequence[Path],
        load: Callable[[], Source],
        swap: Callable[[Source], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._files = tuple(files)
        self._load = load
        self._swap = swap
        self._clock = clock
        self._seen = (
            self._signatures()
        )  # the files as they were when the watcher was built, right after the source loaded
        self._changed: set[int] = set()  # indexes of the files that changed, for the log lines
        self._pending = False  # changed since the last poll's look: load if it holds still
        self._failed: tuple[Signature, ...] | None = None  # the files as of the last failed load
        self._retry_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _signatures(self) -> tuple[Signature, ...]:
        return tuple(_signature(path) for path in self._files)

    def check(self) -> None:
        """One poll. Never raises: the thread has to survive whatever happens."""
        try:
            now = self._signatures()
            if now != self._seen:
                if not self._pending:
                    self._changed = set()
                self._changed |= {
                    i
                    for i, (before, after) in enumerate(zip(self._seen, now, strict=True))
                    if before != after
                }
                self._seen = now
                self._pending = True
                return
            if self._pending:
                self._pending = False
                self._attempt(warn=now != self._failed)
                return
            if self._failed == now and self._clock() >= self._retry_at:
                self._attempt(warn=False)
        except Exception as exc:  # the type only: the text could carry a connection secret
            log.warning("checking the source's files failed: %s", type(exc).__name__)

    def _attempt(self, warn: bool) -> None:
        names = ", ".join(self._files[i].name for i in sorted(self._changed))
        try:
            source = self._load()
            self._swap(source)
        except Exception as exc:
            self._failed = self._seen
            self._retry_at = self._clock() + RETRY_S
            if warn:
                reason = str(exc) if isinstance(exc, AdapterStartupError) else type(exc).__name__
                log.warning(
                    "%s changed, but the source wasn't reloaded; keeping the current one: %s",
                    names,
                    reason,
                )
            return
        self._failed = None
        log.info(
            "reloaded the source after %s changed: %d metrics", names, len(source.metric_names)
        )

    def start(self, interval: float = POLL_S) -> None:
        """Poll every `interval` seconds in a daemon thread until `stop()`."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(interval,), name="prumi-reload", daemon=True
        )
        self._thread.start()

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self.check()

    def stop(self, timeout: float = STOP_WAIT_S) -> None:
        """Stop polling. Waits at most `timeout` seconds for a reload under way: one stuck on
        a connection that never answers mustn't keep the server from exiting. The thread is a
        daemon, so a reload still running then ends with the process."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)


def watcher_for(
    services: Services, clock: Callable[[], float] = time.monotonic
) -> SourceWatcher | None:
    """A watcher for the services' source, or None when its adapter names no files to watch."""
    files = watched_files_of(services.current().source.adapter)
    if not files:
        return None
    return SourceWatcher(
        files,
        lambda: Source.from_config(services.config.sources[DEFAULT_SOURCE]),
        services.swap,
        clock,
    )
