"""Request guards on every route: only loopback Host headers, against DNS rebinding."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from prumi.container import in_container

if TYPE_CHECKING:
    from prumi.config import ServerConfig

# The names a browser on the same machine uses for the server, as they appear in Host.
LOOPBACK_NAMES = ("localhost", "127.0.0.1", "[::1]")

# A Host header: a name (an IPv6 literal in brackets, or anything without a colon), then an
# optional numeric port.
_HOST = re.compile(r"(\[[^\]]*\]|[^:\[\]]*)(?::\d+)?")


def allowed_hosts(host: str, port: int) -> frozenset[str]:
    """The Host headers the server answers: its own host:port, or localhost:port."""
    shown = f"[{host}]" if ":" in host else host  # IPv6 literals are bracketed in Host
    return frozenset({f"{shown}:{port}", f"localhost:{port}"})


def host_allowed(host_header: str, hosts: frozenset[str]) -> bool:
    """Whether the server answers this Host header. In the container image the port is the
    one Docker maps on the host machine, which Prumi can't know, so a loopback name on any
    port (or none) is answered; elsewhere only the exact `hosts`."""
    host = host_header.lower()
    if not in_container():
        return host in hosts
    match = _HOST.fullmatch(host)
    return match is not None and match.group(1) in LOOPBACK_NAMES


def origin(server: ServerConfig, host_header: str | None) -> str:
    """The scheme, host and port to link to. In the container image, the address the request
    came in on (its Host header, already checked by HostCheck): Prumi's own address there is
    the container's, which the browser can't use."""
    if in_container() and host_header:
        return f"http://{host_header.lower()}"
    host = f"[{server.host}]" if ":" in server.host else server.host
    return f"http://{host}:{server.port}"


class HostCheck:
    """403 for any other Host header. This blocks DNS rebinding: a malicious page can make
    the browser talk to a local, unauthenticated server under another name."""

    def __init__(self, app: ASGIApp, hosts: Iterable[str]) -> None:
        self.app = app
        self.hosts = frozenset(h.lower() for h in hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode("latin-1")
            if not host_allowed(host, self.hosts):
                response = PlainTextResponse("Forbidden: unknown Host header.", status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
