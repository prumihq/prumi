"""The HTTP server: one Starlette app on uvicorn."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from prumi.config import ConfigError
from prumi.reload import watcher_for
from prumi.services import Services

SHUTDOWN_TIMEOUT_S = 5


@contextmanager
def _reloads_shown() -> Iterator[None]:
    """Print the reload lines to the terminal while serving: nothing else configures logging,
    and a reload that kept the old source has to be seen. Only this logger, and only for the
    duration; its handlers and level are put back afterwards."""
    log = logging.getLogger("prumi.reload")
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:  %(message)s"))
    level = log.level
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        yield
    finally:
        log.removeHandler(handler)
        log.setLevel(level)


def serve(services: Services) -> None:
    """Check the source and Chromium, then listen on the configured loopback address."""
    import uvicorn

    from prumi.server.app import create_app

    services.source  # noqa: B018 - load the adapter now: startup checks fail before binding
    with _reloads_shown():
        # Built right after the load, so a manifest written during the rest of startup is
        # still seen as a change. It doesn't poll until start().
        watcher = watcher_for(services)  # None when the adapter names no files to watch
        services.data_through()  # the home page's "data through" date
        # Publishing renders every page to check its numbers; without Chromium it can't.
        services.checker.start()
        server = services.config.server
        try:
            if watcher is not None:
                watcher.start()
            if not services.checker.supports_locale(server.locale):
                raise ConfigError(
                    f"{services.config.path}: server.locale: {server.locale!r} isn't a locale "
                    "the browser knows, so numbers can't be formatted in it. Use a tag such as "
                    "en-US, pt-BR or de-DE."
                )
            # Ctrl+C or SIGTERM: requests still running after 5 s are cancelled. MCP keeps no
            # stream open between requests (stateless), so nothing else holds the server up.
            uvicorn.run(
                create_app(services),
                host=server.host,
                port=server.port,
                log_level="info",
                timeout_graceful_shutdown=SHUTDOWN_TIMEOUT_S,
            )
        finally:
            if watcher is not None:
                watcher.stop(timeout=SHUTDOWN_TIMEOUT_S)
            services.checker.close()
