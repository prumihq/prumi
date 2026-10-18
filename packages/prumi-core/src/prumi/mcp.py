"""The MCP server: six tools for agents, over stateless Streamable HTTP at /mcp (section 14).

Built with the official Python SDK (`MCPServer`, which the 1.x SDK called `FastMCP`).
Results that go to agents pass through `for_agent`; errors are tool errors whose content
is the issue list.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette

from prumi.adapters import LOCAL_VIEWER, Filter, FilterOp, FilterValue, Metric
from prumi.agent_output import AgentResult, for_agent
from prumi.engine import BlockInputs, BlockRequestError
from prumi.issues import Issue, error
from prumi.registry import Content
from prumi.services import Services
from prumi.spec import Block, DashboardSpec, Sort
from prumi.time import Compare, Grain, Window
from prumi.validate._parse import plain_issues
from prumi.validate.rules import REQUEST_METRIC_HINT, check_spec

INSTRUCTIONS = """\
Prumi serves official metrics from the company's semantic layer. Build dashboards with it like this:
1. Call `find_metrics` with the user's question, then `describe_metric` for each metric you plan to use. Use only these metrics. If the right one doesn't exist, call `request_metric` and tell the user; never compute or approximate a metric yourself.
2. Use `query` to look at the data while designing. Results are capped; they're for exploring, not for copying into the page.
3. Write a spec and the HTML. Every number on the page must come from `prumi.on(...)` in the HTML. Never write numbers, totals or datasets into the HTML, and never compute totals, averages or ratios in JavaScript; declare `total`, `others` or `compare` in the spec instead. Format values with `prumi.format`.
4. Every data block must list its `filters` explicitly. Prefer full-period windows like `{last: 12, grain: week}`.
5. Call `validate_spec` with the spec and the HTML and fix every error. Then `publish_dashboard` and give the user the URL. The dashboard starts as an uncertified draft; only the data team can certify it."""  # noqa: E501

STOPWORDS = frozenset(
    "a an and are as at be by do does for from how i in is it many me much my of on or per "
    "show the to was we what which who with".split()
)


# Outputs


class FormatOut(BaseModel):
    type: str
    unit: str | None
    decimals: int | None


class MetricSummary(BaseModel):
    name: str
    label: str
    description: str
    kind: str
    additive: bool
    format: FormatOut
    owner: str | None
    score: int


class MetricsFound(BaseModel):
    metrics: list[MetricSummary]


class DimensionOut(BaseModel):
    name: str
    label: str
    description: str


class MetricDescription(BaseModel):
    name: str
    label: str
    description: str
    kind: str
    aggregation: str | None
    additive: bool
    format: FormatOut
    owner: str | None
    filter: str | None = Field(description="The metric's own filter, applied by the source")
    dimensions: list[DimensionOut]
    time_dimension: str = "metric_time"
    time_grains: list[str]
    caveats: list[str]


class ValidationOut(BaseModel):
    ok: bool
    errors: list[Issue]
    warnings: list[Issue]
    candidate_id: str | None = Field(
        default=None,
        description="Set when spec and HTML validated without errors: publish with it",
    )


class PublishOut(BaseModel):
    id: str
    version: int
    url: str
    status: str
    warnings: list[Issue]


class MetricRequestOut(BaseModel):
    id: int
    message: str


class WhereIn(BaseModel):
    dimension: str
    op: FilterOp
    values: list[FilterValue]


# Ranking


def tokens(text: str | None) -> set[str]:
    words = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {w for w in words if w and w not in STOPWORDS}


def score(question: set[str], metric: Metric) -> int:
    """2 per question word in the name or label, 1 per word in the description."""
    title = tokens(metric.name) | tokens(metric.label)
    description = tokens(metric.description)
    return sum(2 * (w in title) + (w in description) for w in question)


def rank(question: str, metrics: Iterable[Metric], limit: int) -> list[tuple[int, Metric]]:
    words = tokens(question)
    scored = [(score(words, m), m) for m in metrics]
    return sorted(scored, key=lambda pair: (-pair[0], pair[1].name))[:limit]


# The server


def _format(metric: Metric) -> FormatOut:
    f = metric.format
    return FormatOut(type=f.type, unit=f.unit, decimals=f.decimals)


def _fail(issues: Sequence[Issue]) -> CallToolResult:
    """A tool error whose content is the issue list (the SDK would prefix a raised error)."""
    text = json.dumps([i.model_dump() for i in issues], ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _dashboard_url(services: Services, dashboard_id: str) -> str:
    server = services.config.server
    host = f"[{server.host}]" if ":" in server.host else server.host
    return f"http://{host}:{server.port}/d/{dashboard_id}"


def create_mcp(services: Services) -> MCPServer:
    mcp = MCPServer(name="prumi", instructions=INSTRUCTIONS)

    @mcp.tool()
    def find_metrics(question: str, limit: int = 20) -> MetricsFound:
        """Find catalog metrics for a question, ranked by how well their names, labels and
        descriptions match it. Use only metrics from the catalog."""
        found = rank(question, services.source.catalog.metrics, max(limit, 0))
        return MetricsFound(
            metrics=[
                MetricSummary(
                    name=m.name,
                    label=m.label,
                    description=m.description,
                    kind=m.kind,
                    additive=m.additive,
                    format=_format(m),
                    owner=m.owner,
                    score=s,
                )
                for s, m in found
            ]
        )

    @mcp.tool()
    def describe_metric(name: str) -> MetricDescription:
        """Everything the catalog knows about a metric: its dimensions, time grains, own filter
        and caveats. Read the caveats before using it."""
        source = services.source
        metric = source.metric(name)
        if metric is None:
            return _fail([_unknown_metric(services, name, "name")])
        caveats = []
        if not metric.additive:
            caveats.append(
                "non-additive: never sum or average its rows; ask for total, others or a "
                "coarser grain instead"
            )
        if any(w.code == "R10.no_format" for w in source.warnings_for(name)):
            caveats.append("no format declared: values show as plain numbers")
        if metric.filter:
            caveats.append(f"the metric has its own filter: {metric.filter}")
        dimensions = []
        for dimension_name in sorted(source.relations([name])):
            dimension = source.dimension(dimension_name)
            dimensions.append(
                DimensionOut(
                    name=dimension_name,
                    label=dimension.label if dimension else dimension_name,
                    description=dimension.description if dimension else "",
                )
            )
        return MetricDescription(
            name=metric.name,
            label=metric.label,
            description=metric.description,
            kind=metric.kind,
            aggregation=metric.aggregation,
            additive=metric.additive,
            format=_format(metric),
            owner=metric.owner,
            filter=metric.filter,
            dimensions=dimensions,
            time_grains=list(source.adapter.capabilities().time_grains),
            caveats=caveats,
        )

    @mcp.tool()
    def query(
        metrics: list[str],
        window: Window,
        dimensions: list[str] | None = None,
        time_grain: Grain | None = None,
        where: list[WhereIn] | None = None,
        sort: Sort | None = None,
        top_n: int | None = None,
        others: bool = False,
        total: bool = False,
        compare: Compare | None = None,
    ) -> AgentResult:
        """Look at the data while designing: a block-shaped query with a required window,
        e.g. {last: 12, grain: week}. Results are capped at 200 rows; they're for exploring,
        never for copying into the page."""
        try:
            block = Block(
                id="query",
                metrics=tuple(metrics),
                dimensions=tuple(dimensions or ()),
                time_grain=time_grain,
                filters=(),
                window=window,
                compare=compare,
                sort=sort,
                top_n=top_n,
                others=others,
                total=total,
            )
        except ValidationError as exc:
            return _fail(plain_issues(exc, {}, Block))
        issues = _check_query(services, block)
        filters, where_issues = _where(services, block, where or [])
        issues += where_issues
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            return _fail(errors)
        engine = services.engine
        inputs = BlockInputs(window, filters)
        try:
            result = engine.run(block, inputs, LOCAL_VIEWER, "agent query")
        except BlockRequestError as exc:
            return _fail(exc.issues)
        except Exception as exc:
            return _fail(
                [error("query.failed", "query", f"The source couldn't run the query: {exc}")]
            )
        agent = for_agent(
            result,
            fetch_total=lambda: engine.total_for(block, inputs, LOCAL_VIEWER, "agent query"),
        )
        agent.warnings[:0] = issues  # the validator's warnings first
        return agent

    candidates = Candidates()

    @mcp.tool()
    def validate_spec(spec: dict[str, Any], html: str | None = None) -> ValidationOut:
        """Validate a dashboard spec and its HTML: static rules, HTML checks, then a dry run of
        every block. Fix every error before publishing. With spec and HTML and no errors, the
        result has a candidate_id to publish with, so the content isn't sent twice."""
        report = services.validate(spec, html)
        candidate_id = None
        if report.ok and html is not None:
            candidate_id = candidates.add(spec, html)
        return ValidationOut(
            ok=report.ok,
            errors=list(report.errors),
            warnings=list(report.warnings),
            candidate_id=candidate_id,
        )

    @mcp.tool()
    def publish_dashboard(
        candidate_id: str | None = None,
        spec: dict[str, Any] | None = None,
        html: str | None = None,
    ) -> PublishOut:
        """Publish a dashboard: pass the candidate_id from the last successful validate_spec
        (preferred), or the spec and HTML. It's validated again, starts as an uncertified
        draft, and a change makes a new version. Give the user the URL."""
        by_candidate = candidate_id is not None and spec is None and html is None
        by_content = candidate_id is None and spec is not None and html is not None
        if not (by_candidate or by_content):
            return _fail(
                [
                    error(
                        "publish.invalid_arguments",
                        "publish_dashboard",
                        "Pass either candidate_id, or both spec and html.",
                        "Use the candidate_id from the last successful validate_spec.",
                    )
                ]
            )
        if candidate_id is not None:
            found = candidates.get(candidate_id)
            if found is None:
                return _fail(
                    [
                        error(
                            "publish.unknown_candidate",
                            "candidate_id",
                            f"There's no validated candidate {candidate_id!r}: it's unknown, "
                            "expired (after an hour) or the server restarted.",
                            "Call validate_spec again with the spec and HTML.",
                        )
                    ]
                )
            spec, html = found
        report, published = services.publish(spec, html)
        if published is None:
            return _fail(report.errors)
        status = services.registry.status(published.dashboard_id, published.version)
        return PublishOut(
            id=published.dashboard_id,
            version=published.version,
            url=_dashboard_url(services, published.dashboard_id),
            status=status.status,
            warnings=list(report.warnings),
        )

    @mcp.tool()
    def request_metric(
        description: str, reason: str, dashboard_id: str | None = None
    ) -> MetricRequestOut:
        """Ask the data team for a metric the catalog doesn't have, instead of computing or
        approximating it. Put the returned id in the spec's metric_requests."""
        request = services.registry.request_metric(description, reason, dashboard_id)
        return MetricRequestOut(
            id=request.id,
            message=(
                f"Recorded metric request {request.id} for the data team. Tell the user the "
                "metric doesn't exist yet; you can list the id in the spec's metric_requests."
            ),
        )

    return mcp


class Candidates:
    """Content that passed validate_spec, by fingerprint, so publishing needn't resend it.

    In memory only: at most 50, each for an hour. Losing them on restart is fine; the agent
    validates again.
    """

    def __init__(
        self, size: int = 50, ttl: float = 3600, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._items: OrderedDict[str, tuple[float, dict[str, Any], str]] = OrderedDict()
        self._size, self._ttl, self._clock = size, ttl, clock
        self._lock = threading.Lock()

    def add(self, spec: dict[str, Any], html: str) -> str:
        candidate_id = Content.of(DashboardSpec.model_validate(spec), html).fingerprint
        with self._lock:
            self._items.pop(candidate_id, None)
            self._items[candidate_id] = (self._clock(), copy.deepcopy(spec), html)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return candidate_id

    def get(self, candidate_id: str) -> tuple[dict[str, Any], str] | None:
        with self._lock:
            item = self._items.get(candidate_id)
            if item is None:
                return None
            added, spec, html = item
            if self._clock() - added > self._ttl:
                del self._items[candidate_id]
                return None
            return copy.deepcopy(spec), html


def _unknown_metric(services: Services, name: str, path: str) -> Issue:
    close = difflib.get_close_matches(name, services.source.metric_names, n=3)
    suggestion = f" Did you mean {', '.join(close)}?" if close else ""
    return error(
        "R7.unknown_metric",
        path,
        f"The catalog has no metric {name!r}.{suggestion}",
        REQUEST_METRIC_HINT,
    )


def _check_query(services: Services, block: Block) -> list[Issue]:
    """The block rules (R2-R7, R10) on the query, with paths in the query's own terms."""
    spec = DashboardSpec(
        spec_version=0,
        dashboard={"id": "query", "title": "query"},
        data=(block,),
    )
    issues = check_spec(spec, services.source, (), services.engine.today())
    return [i.model_copy(update={"path": i.path.removeprefix("data[0].")}) for i in issues]


def _where(
    services: Services, block: Block, where: Sequence[WhereIn]
) -> tuple[tuple[Filter, ...], list[Issue]]:
    filters, issues = [], []
    if any(services.source.metric(m) is None for m in block.metrics):
        return (), issues  # reported as R7 already
    valid = services.source.relations(block.metrics)
    for i, item in enumerate(where):
        path = f"where[{i}]"
        if item.dimension not in valid:
            issues.append(
                error(
                    "R2.filter_dimension_invalid",
                    path,
                    f"{item.dimension!r} isn't a dimension of {', '.join(block.metrics)} "
                    f"together (valid: {', '.join(sorted(valid)) or 'none'}).",
                    "Filter on a dimension describe_metric lists for every metric.",
                )
            )
            continue
        if any(isinstance(v, str) and ("{" in v or "}" in v) for v in item.values):
            issues.append(
                error("filter.invalid_value", path, "Filter values can't contain '{' or '}'.")
            )
            continue
        try:
            filters.append(Filter(item.dimension, item.op, tuple(item.values)))
        except ValueError as exc:
            issues.append(error("filter.invalid_value", path, str(exc)))
    return tuple(filters), issues


def mcp_routes(services: Services) -> tuple[list, Any]:
    """The /mcp route and the lifespan that runs the SDK's session manager."""
    server = services.config.server
    from prumi.server.guards import allowed_hosts

    hosts = sorted(allowed_hosts(server.host, server.port))
    app: Starlette = create_mcp(services).streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        # The SDK's own DNS-rebinding check, with Prumi's hosts; it also rejects requests
        # from web pages on other origins.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[f"http://{h}" for h in hosts],
        ),
        host=server.host,
    )
    return list(app.routes), app.router.lifespan_context


__all__ = ["INSTRUCTIONS", "create_mcp", "mcp_routes", "rank", "tokens"]
