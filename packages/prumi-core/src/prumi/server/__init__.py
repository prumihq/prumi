"""The HTTP server: one Starlette app on uvicorn."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from prumi.config import ENVIRONMENT, Config, ConfigError
from prumi.container import log_open_hint, warn_if_unreachable
from prumi.reload import watcher_for
from prumi.services import Services

SHUTDOWN_TIMEOUT_S = 5


# Their INFO lines must reach the terminal while serving: a reload that kept the old source
# has to be seen, and so does the address to open in a container.
SHOWN_LOGGERS = ("prumi.reload", "prumi.container")


@contextmanager
def _logs_shown() -> Iterator[None]:
    """Print the SHOWN_LOGGERS lines to the terminal while serving: nothing else configures
    logging. Only these loggers, and only for the duration; their handlers and levels are put
    back afterwards."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:  %(message)s"))
    logs = [logging.getLogger(name) for name in SHOWN_LOGGERS]
    levels = [log.level for log in logs]
    for log in logs:
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    try:
        yield
    finally:
        for log, level in zip(logs, levels, strict=True):
            log.removeHandler(handler)
            log.setLevel(level)


def _unknown_locale(config: Config) -> str:
    """The message for a locale the browser doesn't know, naming the variable if one set it."""
    variable = config.set_by.get(("server", "locale"))
    # A variable's value never appears in a message: it may hold a secret.
    where = (
        f"{ENVIRONMENT}: {variable}: the value"
        if variable is not None
        else f"{config.source_label}: server.locale: {config.server.locale!r}"
    )
    return (
        f"{where} isn't a locale the browser knows, so numbers can't be formatted in it. Use a "
        "tag such as en-US, pt-BR or de-DE."
    )


def serve(services: Services) -> None:
    """Check the source and Chromium, then listen on the configured loopback address."""
    import uvicorn

    from prumi.server.app import create_app

    services.source  # noqa: B018 - load the adapter now: startup checks fail before binding
    with _logs_shown():
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
                raise ConfigError(_unknown_locale(services.config))
            # Ctrl+C or SIGTERM: requests still running after 5 s are cancelled. MCP keeps no
            # stream open between requests (stateless), so nothing else holds the server up.
            warn_if_unreachable(server)
            log_open_hint(server)
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
