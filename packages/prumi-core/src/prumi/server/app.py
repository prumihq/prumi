"""The HTTP app: home page, shell and frame pages, the runtime, and the block API."""

from __future__ import annotations

import logging
from html import escape
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from prumi.adapters import LOCAL_VIEWER
from prumi.config import DEFAULT_SOURCE
from prumi.engine import BlockRequestError, UnknownBlockError
from prumi.issues import Issue, error
from prumi.mcp import mcp_routes
from prumi.registry import PageCheck, Version
from prumi.server import pages
from prumi.server.assets import read_asset
from prumi.server.guards import HostCheck, allowed_hosts
from prumi.services import Services
from prumi.spec import DimensionFilter

log = logging.getLogger("prumi.server")


class NotFound(Exception):
    pass


def create_app(services: Services) -> HostCheck:
    server = services.config.server

    async def index(request: Request) -> Response:
        data_through = await run_in_threadpool(services.data_through)
        host = f"[{server.host}]" if ":" in server.host else server.host
        page = pages.home(
            services.registry,
            services.source,
            services.config.sources[DEFAULT_SOURCE].project,
            data_through,
            f"http://{host}:{server.port}/mcp",
            services.registry.now(),
        )
        headers = {"Content-Security-Policy": pages.HOME_CSP, "X-Content-Type-Options": "nosniff"}
        return HTMLResponse(page, headers=headers)

    async def asset(request: Request) -> Response:
        name = request.url.path.lstrip("/")
        kind = "text/css" if name.endswith(".css") else "text/javascript"
        return Response(
            read_asset(name),
            media_type=f"{kind}; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    async def shell(request: Request) -> Response:
        dashboard_id = request.path_params["id"]
        version = request.path_params.get("n")
        if version is None:
            found = services.registry.dashboard(dashboard_id)
            if found is None:
                return _not_found_page(f"There's no dashboard {dashboard_id!r}.")
            version = found.current_version
        stored = services.registry.version(dashboard_id, version)
        if stored is None:
            return _not_found_page(f"There's no version {version} of {dashboard_id!r}.")
        status = services.registry.status(dashboard_id, version)
        return HTMLResponse(pages.shell(stored, status, services.source))

    async def frame(request: Request) -> Response:
        stored = services.registry.version(request.path_params["id"], request.path_params["n"])
        if stored is None:
            return _not_found_page("There's no such dashboard version.")
        origin = f"http://{request.headers['host']}"
        return HTMLResponse(
            pages.frame_html(stored.html, origin),
            headers={
                "Content-Security-Policy": pages.frame_csp(origin, services.hosts),
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def status(request: Request) -> Response:
        found = services.registry.status(request.path_params["id"], request.path_params["n"])
        if found is None:
            return _not_found("There's no such dashboard version.")
        return JSONResponse(found.to_json())

    async def block(request: Request) -> Response:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return JSONResponse(
                {"error": "POST /api/... needs Content-Type: application/json."}, 415
            )
        try:
            stored = _version(services, request)
        except NotFound as exc:
            return _not_found(str(exc))
        spec = stored.spec
        block = spec.block(request.path_params["block"])
        if block is None:
            return _not_found(f"There's no block {request.path_params['block']!r}.")
        try:
            body = await request.json()
        except ValueError:
            body = None
        filters = body.get("filters", {}) if isinstance(body, dict) else None
        if not isinstance(filters, dict):
            return _bad_request(
                [
                    error(
                        "filter.invalid_value",
                        "filters",
                        'The body must be a JSON object like {"filters": {"<filter id>": value}}.',
                    )
                ]
            )
        unlisted = [f for f in filters if f not in block.filters]
        if unlisted:
            return _bad_request(
                [
                    error(
                        "filter.not_listed",
                        f"filters.{f}",
                        f"The block {block.id!r} doesn't list the filter {f!r}, so its value "
                        "can't apply to it.",
                    )
                    for f in unlisted
                ]
            )
        try:
            result = await run_in_threadpool(
                services.engine.run_block,
                spec,
                block.id,
                filters,
                LOCAL_VIEWER,
                f"block {stored.dashboard_id}/{stored.version}/{block.id}",
            )
        except BlockRequestError as exc:
            return _bad_request(exc.issues)
        except UnknownBlockError:
            return _not_found(f"There's no block {block.id!r}.")
        except Exception as exc:  # the source failed; never log values
            log.error(
                "block %s/%s/%s failed: %s",
                stored.dashboard_id,
                stored.version,
                block.id,
                type(exc).__name__,
            )
            return JSONResponse({"issues": [_query_failed(block.id).model_dump()]}, status_code=502)
        return JSONResponse(result.to_json())

    async def filter_values(request: Request) -> Response:
        try:
            stored = _version(services, request)
        except NotFound as exc:
            return _not_found(str(exc))
        definition = stored.spec.filter(request.path_params["filter"])
        if not isinstance(definition, DimensionFilter):
            return _not_found(f"There's no dimension filter {request.path_params['filter']!r}.")
        try:
            values = await run_in_threadpool(
                services.engine.dimension_values,
                definition.dimension,
                LOCAL_VIEWER,
                f"filter values {stored.dashboard_id}/{stored.version}/{definition.id}",
            )
        except Exception as exc:
            log.error("filter values %s failed: %s", definition.id, type(exc).__name__)
            return JSONResponse({"issues": []}, status_code=502)
        return JSONResponse({"values": values})

    async def page_check(request: Request) -> Response:
        """A viewer's browser reports what its number check found for one filter state."""
        try:
            stored = _version(services, request)
        except NotFound as exc:
            return _not_found(str(exc))
        body = await request.body()
        if len(body) > MAX_CHECK_BYTES:
            return JSONResponse({"error": "The report is too large."}, status_code=413)
        try:
            report = CheckReport.model_validate_json(body)
        except ValidationError:
            return JSONResponse({"error": "The body isn't a check report."}, status_code=400)
        known = {f.id for f in stored.spec.filters}
        if not set(report.filters) <= known:
            return JSONResponse({"error": "The report names unknown filters."}, status_code=400)
        check = PageCheck(
            filters=report.filters,
            flagged=[f.model_dump() for f in report.flagged],
            canvas=report.canvas,
            errors=report.errors,
        )
        await run_in_threadpool(
            services.registry.record_check, stored.dashboard_id, stored.version, check
        )
        return Response(status_code=204)

    base = "/api/dashboards/{id}/versions/{n:int}"
    mcp, mcp_lifespan = mcp_routes(services)
    app = Starlette(
        lifespan=lambda app: mcp_lifespan(app),
        routes=[
            Route("/", index),
            Route("/runtime.js", asset),
            Route("/shell.js", asset),
            Route("/provenance.js", asset),
            Route("/theme.css", asset),
            Route("/home.css", asset),
            Route("/d/{id}", shell),
            Route("/d/{id}/v/{n:int}", shell),
            Route("/d/{id}/v/{n:int}/frame", frame),
            Route(f"{base}/status", status),
            Route(f"{base}/blocks/{{block}}", block, methods=["POST"]),
            Route(f"{base}/filters/{{filter}}/values", filter_values),
            Route(f"{base}/check", page_check, methods=["POST"]),
            *mcp,  # /mcp: stateless Streamable HTTP
        ],
    )
    app.state.services = services
    return HostCheck(app, allowed_hosts(server.host, server.port))


MAX_CHECK_BYTES = 64 * 1024


class _Flagged(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=200)
    path: str = Field(max_length=500)
    code: Literal["R8.unverified_number", "R8.unbound_number"] = "R8.unverified_number"
    blocks: list[Annotated[str, Field(max_length=100)]] = Field(default=[], max_length=50)


class CheckReport(BaseModel):
    """The body of POST .../check, as the runtime sends it through the shell."""

    model_config = ConfigDict(extra="forbid")

    filters: dict[str, Any]
    flagged: list[_Flagged] = Field(max_length=20)
    canvas: bool
    errors: list[Annotated[str, Field(max_length=300)]] = Field(max_length=20)


def _version(services: Services, request: Request) -> Version:
    """The version the request names, if the viewer may read it (else it's not found)."""
    registry = services.registry
    dashboard = registry.dashboard(request.path_params["id"])
    stored = registry.version(request.path_params["id"], request.path_params["n"])
    if stored is None or dashboard is None or not registry.can_read(LOCAL_VIEWER, dashboard):
        raise NotFound(
            f"There's no version {request.path_params['n']} of {request.path_params['id']!r}."
        )
    return stored


def _not_found(message: str) -> Response:
    return JSONResponse({"error": message}, status_code=404)


def _not_found_page(message: str) -> Response:
    return HTMLResponse(f"<!doctype html><title>Not found</title><p>{escape(message)}</p>", 404)


def _bad_request(issues: Any) -> Response:
    return JSONResponse({"issues": [i.model_dump() for i in issues]}, status_code=400)


def _query_failed(block_id: str) -> Issue:
    return error(
        "query.failed",
        f"blocks.{block_id}",
        f"The source couldn't run the block {block_id!r}. The server log has the error type.",
    )
