"""The container image's switch: PRUMI_CONTAINER=1 (docs/adr/0017)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prumi.config import ServerConfig

ENV = "PRUMI_CONTAINER"

LOOPBACK_IN_CONTAINER_WARNING = (
    "host is a loopback address inside a container, so the server can't be reached from "
    "outside it; leave host unset in the image (see docs/configuration.md)."
)

# uvicorn's own startup line names the address it listens on inside the container, which a
# browser on the host machine can't use; this line names one it can.
OPEN_HINT = (
    "Open Prumi at http://127.0.0.1:{port} (or the host port you gave -p); "
    "the {host} address uvicorn prints below is the container's own."
)


def in_container() -> bool:
    """True only for PRUMI_CONTAINER=1; read on every call so tests can change it."""
    return os.environ.get(ENV) == "1"


def warn_if_unreachable(server: ServerConfig) -> None:
    # Imported here: config imports this module for in_container.
    from prumi.config import is_loopback

    if in_container() and is_loopback(server.host):
        # No handler is configured for this logger; Python's last-resort handler prints
        # warnings to stderr.
        logging.getLogger("prumi.server").warning(LOOPBACK_IN_CONTAINER_WARNING)


def log_open_hint(server: ServerConfig) -> None:
    """In the container, log the address to open on the host machine. Not for a loopback
    host there: that server can't be reached at all, and warn_if_unreachable says so."""
    from prumi.config import is_loopback

    if in_container() and not is_loopback(server.host):
        logging.getLogger("prumi.container").info(
            OPEN_HINT.format(port=server.port, host=server.host)
        )
