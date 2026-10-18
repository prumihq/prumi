"""The HTTP server: one Starlette app on uvicorn."""

from __future__ import annotations

from prumi.services import Services

SHUTDOWN_TIMEOUT_S = 5


def serve(services: Services) -> None:
    """Check the source and Chromium, then listen on the configured loopback address."""
    import uvicorn

    from prumi.server.app import create_app

    services.source  # noqa: B018 - load the adapter now: startup checks fail before binding
    # Publishing renders every page to check its numbers; without Chromium it can't.
    services.checker.start()
    server = services.config.server
    try:
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
        services.checker.close()
