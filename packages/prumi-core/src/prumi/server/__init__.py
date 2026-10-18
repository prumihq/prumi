"""The HTTP server: one Starlette app on uvicorn (spec section 12)."""

from __future__ import annotations

from prumi.services import Services


def serve(services: Services) -> None:
    """Check the source and Chromium, then listen on the configured loopback address."""
    import uvicorn

    from prumi.server.app import create_app

    services.source  # noqa: B018 - load the adapter now: startup checks fail before binding
    # Publishing renders every page to check its numbers; without Chromium it can't.
    services.checker.start()
    server = services.config.server
    uvicorn.run(create_app(services), host=server.host, port=server.port, log_level="info")
