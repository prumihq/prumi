"""Request guards on every route: only loopback Host headers, against DNS rebinding."""

from __future__ import annotations

from collections.abc import Iterable

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def allowed_hosts(host: str, port: int) -> frozenset[str]:
    """The Host headers the server answers: its own host:port, or localhost:port."""
    shown = f"[{host}]" if ":" in host else host  # IPv6 literals are bracketed in Host
    return frozenset({f"{shown}:{port}", f"localhost:{port}"})


class HostCheck:
    """403 for any other Host header. This blocks DNS rebinding: a malicious page can make
    the browser talk to a local, unauthenticated server under another name."""

    def __init__(self, app: ASGIApp, hosts: Iterable[str]) -> None:
        self.app = app
        self.hosts = frozenset(h.lower() for h in hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode("latin-1").lower()
            if host not in self.hosts:
                response = PlainTextResponse("Forbidden: unknown Host header.", status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
