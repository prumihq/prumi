"""The HTTP server: one Starlette app on uvicorn."""

from __future__ import annotations

import logging

from prumi.config import ConfigError
from prumi.reload import watcher_for
from prumi.services import Services

SHUTDOWN_TIMEOUT_S = 5


def _show_reloads() -> None:
    """Print the reload lines to the terminal: nothing else configures logging, and a reload
    that kept the old source has to be seen. Only this logger; the others stay as they are."""
    log = logging.getLogger("prumi.reload")
    if not log.handlers:
        log.addHandler(logging.StreamHandler())
        log.setLevel(logging.INFO)


def serve(services: Services) -> None:
    """Check the source and Chromium, then listen on the configured loopback address."""
    import uvicorn

    from prumi.server.app import create_app

    services.source  # noqa: B018 - load the adapter now: startup checks fail before binding
    services.data_through()  # the home page's "data through" date
    # Publishing renders every page to check its numbers; without Chromium it can't.
    services.checker.start()
    server = services.config.server
    _show_reloads()
    watcher = None
    try:
        watcher = watcher_for(services)  # None when the adapter names no files to watch
        if watcher is not None:
            watcher.start()
        if not services.checker.supports_locale(server.locale):
            raise ConfigError(
                f"{services.config.path}: server.locale: {server.locale!r} isn't a locale the "
                "browser knows, so numbers can't be formatted in it. Use a tag such as en-US, "
                "pt-BR or de-DE."
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
            watcher.stop()
        services.checker.close()
