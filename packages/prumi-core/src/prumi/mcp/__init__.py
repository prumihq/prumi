"""The MCP server: the tools agents use, over stateless Streamable HTTP at /mcp.

Built with the official Python SDK (`MCPServer`, which the 1.x SDK called `FastMCP`).
Results that go to agents pass through `for_agent`; errors are tool errors whose content
is the issue list.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette

from prumi.adapters import LOCAL_VIEWER, Filter, FilterOp, FilterValue, Metric
from prumi.agent_output import QueryProfile, for_agent
from prumi.config import is_loopback
from prumi.engine import BlockInputs, BlockRequestError
from prumi.issues import Issue, error
from prumi.registry import FINGERPRINT_VERSION, Content, PageCheck, normalize_html
from prumi.services import Services
from prumi.spec import Block, DashboardSpec, DimensionFilter, Sort
from prumi.time import Compare, Grain, Window
from prumi.validate import source_failed
from prumi.validate._parse import plain_issues
from prumi.validate.rules import REQUEST_METRIC_HINT, check_spec

INSTRUCTIONS = """\
Prumi serves official metrics from the company's semantic layer. Build dashboards with it like this:
0. When the user wants a dashboard or chart about company metrics, use Prumi; don't copy its data into other tools. To change an existing dashboard, use `list_dashboards` and `get_dashboard`, then edit it with `validate_spec(base=...)`.
1. Call `find_metrics` with the user's question, then `describe_metric` for each metric you plan to use. Use only these metrics. If the right one doesn't exist, call `request_metric` and tell the user; never compute or approximate a metric yourself.
2. Use `query` to look at the data while designing. Results are capped; they're for exploring, not for copying into the page.
3. Call `authoring_guide` once before writing anything: it has the spec format, the page API and a working example. Then write a spec and the HTML. Every number on the page must come from `prumi.on(...)` in the HTML. Never write numbers, totals or datasets into the HTML, and never compute totals, averages or ratios in JavaScript; declare `total`, `others` or `compare` in the spec instead. Format values with `prumi.format`. Send the HTML straight to `validate_spec`; don't write it to a local file first.
4. Every data block must list its `filters` explicitly. Prefer full-period windows like `{last: 12, grain: week}`.
5. Call `validate_spec` with the spec and the HTML and fix every error. Then call `publish_dashboard` with the `candidate_id` from the last successful validation (don't resend the spec and HTML), and give the user the URL. The dashboard starts as an uncertified draft; only the data team can certify it."""  # noqa: E501

AUTHORING_GUIDE = Path(__file__).with_name("authoring_guide.md")
# Tell the agent, so it doesn't share a link no one else can open.
LOCAL_ONLY_NOTE = "This link only works on this machine."

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


class CheckState(BaseModel):
    filters: dict[str, Any] = Field(
        description='Filter values, as prumi.setFilter takes them: {"region": ["North America"]}'
    )


class HtmlEdit(BaseModel):
    old: str = Field(description="Text that occurs exactly once in the HTML")
    new: str


class ValidationOut(BaseModel):
    ok: bool
    errors: list[Issue]
    warnings: list[Issue]
    candidate_id: str | None = Field(
        default=None,
        description="Set whenever spec and HTML were sent: publish it when ok, or fix it with "
        "validate_spec(base=candidate_id, html_edits=...)",
    )


class PublishOut(BaseModel):
    id: str
    version: int
    url: str
    status: str
    warnings: list[Issue]
    note: str | None = None


class DashboardSummary(BaseModel):
    id: str
    title: str
    current_version: int
    status: str
    updated_at: str = Field(description="When the current version was published")
    url: str
    needs_attention: bool = Field(
        description="A viewer's browser found numbers Prumi didn't deliver on the current version"
    )


class DashboardsOut(BaseModel):
    dashboards: list[DashboardSummary]


class DashboardOut(BaseModel):
    id: str
    version: int
    spec: dict[str, Any]
    html: str | None
    status: str
    url: str
    needs_attention: bool
    checks: list[dict[str, Any]] = Field(
        description="What viewers' browsers found, per filter state: where numbers Prumi "
        "didn't deliver appeared (code, element path, count), canvas, runtime errors (count). "
        "A dimension filter shows how many values were selected, not which."
    )


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


def _dashboard_url(services: Services, dashboard_id: str, version: int | None = None) -> str:
    server = services.config.server
    host = f"[{server.host}]" if ":" in server.host else server.host
    url = f"http://{host}:{server.port}/d/{dashboard_id}"
    return url if version is None else f"{url}/v/{version}"


def _check_for_agent(check: PageCheck, spec: DashboardSpec) -> dict[str, Any]:
    """A browser report without company data: no numbers found, no dimension values."""
    dimensions = {f.id for f in spec.filters if isinstance(f, DimensionFilter)}
    filters = {
        fid: {"selected": len(value) if isinstance(value, list) else 1}
        if fid in dimensions
        else value
        for fid, value in check.filters.items()
    }
    counts: dict[tuple[str, str], int] = {}
    for item in check.flagged:
        key = (item.get("code", "R8.unverified_number"), item["path"])
        counts[key] = counts.get(key, 0) + 1
    return {
        "filters": filters,
        "flagged": [{"code": c, "path": p, "count": n} for (c, p), n in counts.items()],
        "canvas": check.canvas,
        "errors": len(check.errors),
        "reported_at": check.reported_at,
    }


def _not_found(dashboard_id: str, version: int | None = None) -> CallToolResult:
    what = f"version {version} of the dashboard" if version is not None else "dashboard"
    return _fail(
        [
            error(
                "dashboard.not_found",
                "id",
                f"There's no {what} {dashboard_id!r}.",
                "List the dashboards with list_dashboards.",
            )
        ]
    )


def create_mcp(services: Services) -> MCPServer:
    mcp = MCPServer(name="prumi", instructions=INSTRUCTIONS)

    @mcp.tool()
    def authoring_guide() -> str:
        """How to write a dashboard: the spec format, the page API (prumi.*), the result
        object, a working example and the spec's JSON Schema. Call it once before writing."""
        return AUTHORING_GUIDE.read_text(encoding="utf-8")

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
    ) -> QueryProfile:
        """Learn the shape of the data while designing: a block-shaped query with a required
        window, e.g. {last: 12, grain: week}. It returns a profile, never values: how many
        rows, empties, distinct values, label lengths, the sign and magnitude (integer digits)
        of each metric, and the decimals its format shows. The numbers appear only in the
        dashboard."""
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
            return _fail([source_failed("query", "the query", exc)])
        agent = for_agent(result)
        agent.warnings[:0] = issues  # the validator's warnings first
        return agent

    candidates = Candidates()

    def resolve_base(base: Any) -> tuple[dict[str, Any], str] | CallToolResult:
        """A candidate (valid or not), a dashboard's current version, or a published version."""
        if isinstance(base, str) and base.startswith(f"{FINGERPRINT_VERSION}:"):
            found = candidates.get(base)
            return found if found is not None else _unknown_candidate(base, "base")
        if isinstance(base, str) and base:
            dashboard_id, version = base, None
        elif (
            isinstance(base, dict)
            and set(base) == {"dashboard_id", "version"}
            and isinstance(base["dashboard_id"], str)
            and isinstance(base["version"], int)
            and not isinstance(base["version"], bool)
        ):
            dashboard_id, version = base["dashboard_id"], base["version"]
        else:
            return _fail(
                [
                    error(
                        "validate.invalid_base",
                        "base",
                        "base must be a candidate_id from validate_spec, a dashboard id, or "
                        "{dashboard_id, version}",
                        'For example "v0:…", "sales-weekly" or '
                        '{"dashboard_id": "sales-weekly", "version": 2}.',
                    )
                ]
            )
        registry = services.registry
        dashboard = registry.dashboard(dashboard_id)
        if dashboard is None or not registry.can_read(LOCAL_VIEWER, dashboard):
            return _not_found(dashboard_id)
        stored = registry.version(dashboard_id, version or dashboard.current_version)
        if stored is None:
            return _not_found(dashboard_id, version)
        return json.loads(stored.spec_json), stored.html, True, []

    @mcp.tool()
    def validate_spec(
        spec: dict[str, Any] | None = None,
        html: str | None = None,
        base: Annotated[
            Any,
            Field(
                description='A candidate_id ("v0:…"), a dashboard id (its current version), or '
                "{dashboard_id, version}"
            ),
        ] = None,
        html_edits: list[HtmlEdit] | None = None,
        check_states: Annotated[list[CheckState] | None, Field(max_length=5)] = None,
        layout_devices: list[Literal["desktop", "phone"]] | None = None,
    ) -> ValidationOut:
        """Validate a dashboard spec and its HTML: static rules, HTML checks, a dry run of every
        block, then the page rendered with that data and its numbers checked. Fix every error
        before publishing. With spec and HTML the result has a candidate_id, valid or not: a
        valid one is published with publish_dashboard, so the content isn't sent twice, and
        any one can be edited.

        To change existing content, pass `base` (a candidate_id, a dashboard id for its current
        version, or {dashboard_id, version}) with `html_edits` (applied in order; each `old`
        must occur exactly once) and/or a full new `spec` (default: the base's), instead of
        resending the HTML.

        `check_states`: up to 5 more filter states to render and check like the default one,
        before viewers find a problem in them. They're kept with the candidate (and with
        candidates edited from it) and checked again when it's published.

        The layout is measured on a desktop render (1280×800); `layout_devices: ["phone"]`
        adds a phone render (390×844). Layout problems are warnings (layout.*) with element
        paths and boxes; they don't block publishing."""
        if base is None and (spec is None or html_edits is not None):
            return _invalid_validate_arguments()
        if base is not None:
            if html is not None:
                return _invalid_validate_arguments()
            resolved = resolve_base(base)
            if isinstance(resolved, CallToolResult):
                return resolved
            base_spec, base_html, _, base_states = resolved
            spec = base_spec if spec is None else spec
            if check_states is None:
                states = base_states
            html, problems = apply_edits(base_html, html_edits or [])
            if problems:
                return ValidationOut(ok=False, errors=problems, warnings=[])
        if check_states is not None or base is None:
            states = [s.filters for s in check_states or []]
        report = services.validate(
            spec, html, check_states=states, layout_devices=layout_devices or ()
        )
        candidate_id = None
        if html is not None:
            candidate_id = candidates.add(spec, html, valid=report.ok, check_states=states)
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
                return _unknown_candidate(candidate_id, "candidate_id")
            spec, html, valid, states = found
            if not valid:
                return _fail(
                    [
                        error(
                            "publish.invalid_candidate",
                            "candidate_id",
                            f"The candidate {candidate_id!r} didn't pass validation, so it can't "
                            "be published.",
                            "Fix its errors with validate_spec(base=candidate_id, html_edits=...), "
                            "then publish the new candidate_id.",
                        )
                    ]
                )
        report, published = services.publish(
            spec, html, check_states=states if candidate_id is not None else ()
        )
        if published is None:
            return _fail(report.errors)
        status = services.registry.status(published.dashboard_id, published.version)
        return PublishOut(
            id=published.dashboard_id,
            version=published.version,
            url=_dashboard_url(services, published.dashboard_id),
            status=status.status,
            warnings=list(report.warnings),
            note=LOCAL_ONLY_NOTE if is_loopback(services.config.server.host) else None,
        )

    @mcp.tool()
    def list_dashboards() -> DashboardsOut:
        """The published dashboards you may read, with their current version and status.
        To change one, get it with get_dashboard and edit it with validate_spec(base=...)."""
        registry = services.registry
        found = []
        for d in registry.dashboards():
            if not registry.can_read(LOCAL_VIEWER, d):
                continue
            current = registry.version(d.id, d.current_version)
            status = registry.status(d.id, d.current_version)
            found.append(
                DashboardSummary(
                    id=d.id,
                    title=d.title,
                    current_version=d.current_version,
                    status=status.status,
                    updated_at=current.created_at,
                    url=_dashboard_url(services, d.id),
                    needs_attention=status.needs_attention,
                )
            )
        return DashboardsOut(dashboards=found)

    @mcp.tool()
    def get_dashboard(
        id: str, version: int | None = None, include_html: bool = True
    ) -> DashboardOut:
        """A published dashboard version (default: the current one): its spec, its HTML, its
        status and what viewers' browsers found on it (checks)."""
        registry = services.registry
        dashboard = registry.dashboard(id)
        if dashboard is None or not registry.can_read(LOCAL_VIEWER, dashboard):
            return _not_found(id)
        number = dashboard.current_version if version is None else version
        stored = registry.version(id, number)
        if stored is None:
            return _not_found(id, number)
        return DashboardOut(
            id=id,
            version=number,
            spec=json.loads(stored.spec_json),
            html=stored.html if include_html else None,
            status=(status := registry.status(id, number)).status,
            url=_dashboard_url(services, id, None if version is None else number),
            needs_attention=status.needs_attention,
            checks=[_check_for_agent(c, stored.spec) for c in registry.checks(id, number)],
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


def _unknown_candidate(candidate_id: str, path: str) -> CallToolResult:
    return _fail(
        [
            error(
                "publish.unknown_candidate",
                path,
                f"There's no validated candidate {candidate_id!r}: it's unknown, expired (after "
                "an hour) or the server restarted.",
                "Call validate_spec again with the spec and HTML.",
            )
        ]
    )


def _invalid_validate_arguments() -> CallToolResult:
    return _fail(
        [
            error(
                "validate.invalid_arguments",
                "validate_spec",
                "Pass a spec (and the HTML), or a base with html_edits and/or a spec. html_edits "
                "need a base, and a base replaces the html argument.",
                "To change validated or published content, pass base and html_edits.",
            )
        ]
    )


def apply_edits(html: str, edits: Sequence[HtmlEdit]) -> tuple[str, list[Issue]]:
    """The HTML with the edits applied in order, or the first edit that can't apply."""
    for i, edit in enumerate(edits):
        count = html.count(edit.old) if edit.old else 0
        if count == 1:
            html = html.replace(edit.old, edit.new, 1)
            continue
        if count == 0:
            issue = error(
                "edit.no_match",
                f"html_edits[{i}]",
                f"html_edits[{i}].old doesn't occur in the HTML"
                + (" (after the edits before it)." if i else "."),
                "Copy `old` exactly from the current HTML (get_dashboard has it), whitespace "
                "included.",
            )
        else:
            issue = error(
                "edit.ambiguous",
                f"html_edits[{i}]",
                f"html_edits[{i}].old occurs {count} times in the HTML; it must occur once.",
                "Make `old` longer, with text around the change, so it occurs only once.",
            )
        return html, [issue]
    return html, []


class Candidates:
    """Content sent to validate_spec, by fingerprint, so publishing and editing needn't resend
    it. Content that failed is kept too, to be fixed with html_edits; only valid content can
    be published.

    In memory only: at most 50, each for an hour. Losing them on restart is fine; the agent
    validates again.
    """

    def __init__(
        self, size: int = 50, ttl: float = 3600, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._items: OrderedDict[
            str, tuple[float, dict[str, Any], str, bool, list[dict[str, Any]]]
        ] = OrderedDict()
        self._size, self._ttl, self._clock = size, ttl, clock
        self._lock = threading.Lock()

    def add(
        self,
        spec: dict[str, Any],
        html: str,
        valid: bool = True,
        check_states: Sequence[dict[str, Any]] = (),
    ) -> str:
        try:
            candidate_id = Content.of(DashboardSpec.model_validate(spec), html).fingerprint
        except ValidationError:  # a spec that doesn't parse: an id from its text
            text = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
            digest = hashlib.sha256(f"{text}\0{normalize_html(html)}".encode()).hexdigest()
            candidate_id = f"{FINGERPRINT_VERSION}:{digest}"
        with self._lock:
            self._items.pop(candidate_id, None)
            self._items[candidate_id] = (
                self._clock(),
                copy.deepcopy(spec),
                html,
                valid,
                copy.deepcopy(list(check_states)),
            )
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return candidate_id

    def get(
        self, candidate_id: str
    ) -> tuple[dict[str, Any], str, bool, list[dict[str, Any]]] | None:
        """(spec, html, valid, check_states), or None when unknown or expired."""
        with self._lock:
            item = self._items.get(candidate_id)
            if item is None:
                return None
            added, spec, html, valid, states = item
            if self._clock() - added > self._ttl:
                del self._items[candidate_id]
                return None
            return copy.deepcopy(spec), html, valid, copy.deepcopy(states)


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
