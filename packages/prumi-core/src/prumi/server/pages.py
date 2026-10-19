"""Prumi's own pages."""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from html import escape
from typing import Any

from prumi import __version__
from prumi.adapters import LOCAL_VIEWER, METRIC_TIME, Viewer
from prumi.registry import Dashboard, MetricRequest, Registry, Version, VersionStatus
from prumi.source import Source
from prumi.spec import DashboardSpec, DimensionFilter
from prumi.validate import AllowedHosts

# The home page: titles, statuses, labels and dates; never a metric value

HOME_CSP = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "connect-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)
DOCS_URL = "https://github.com/prumihq/prumi#readme"
SHOWN_LABELS = 4
SHOWN_REQUESTS = 10


@dataclass(frozen=True)
class Card:
    dashboard: Dashboard
    status: VersionStatus
    updated_at: str
    metrics: list[str]  # labels, in the order the spec first uses them


def home_badge(status: VersionStatus) -> tuple[str, str]:
    """(kind, text) of a card's status. A flagged version needs attention, certified or not."""
    if status.needs_attention:
        return "attention", "Needs attention"
    if status.status == "certified":
        return "certified", f"Certified by {status.certified_by}"
    return "draft", "Draft"


_UNITS = (("year", 365 * 86400), ("month", 30 * 86400), ("day", 86400), ("hour", 3600),
          ("minute", 60))  # fmt: skip


def relative_time(timestamp: str, now: dt.datetime) -> str:
    """'3 hours ago', 'yesterday', 'just now': for people; the exact time goes in a title."""
    seconds = max(0, (now - dt.datetime.fromisoformat(timestamp)).total_seconds())
    for unit, size in _UNITS:
        if seconds >= size:
            n = int(seconds // size)
            return "yesterday" if (unit, n) == ("day", 1) else f"{n} {unit}{'s' * (n > 1)} ago"
    return "just now"


def cards(registry: Registry, source: Source, viewer: Viewer) -> list[Card]:
    """The dashboards `viewer` may read, newest update first."""
    found = []
    for d in registry.dashboards():
        if not registry.can_read(viewer, d):
            continue
        current = registry.version(d.id, d.current_version)
        metrics = dict.fromkeys(m for block in current.spec.data for m in block.metrics)
        labels = [(metric.label if (metric := source.metric(m)) else m) for m in metrics]
        status = registry.status(d.id, d.current_version)
        found.append(Card(d, status, current.created_at, labels))
    found.sort(key=lambda c: c.dashboard.id)
    found.sort(key=lambda c: c.updated_at, reverse=True)
    return found


def _card(card: Card, now: dt.datetime) -> str:
    d = card.dashboard
    kind, text = home_badge(card.status)
    updated = escape(card.updated_at)
    ago = relative_time(card.updated_at, now)
    shown, rest = card.metrics[:SHOWN_LABELS], card.metrics[SHOWN_LABELS:]
    labels = "".join(f"<li>{escape(label)}</li>" for label in shown)
    if rest:
        labels += f'<li class="more" title="{escape(", ".join(rest))}">+{len(rest)}</li>'
    return f"""<li class="card">
  <div class="card-head">
    <a class="card-title" href="/d/{escape(d.id)}">{escape(d.title)}</a>
    <span class="badge" data-kind="{kind}">{escape(text)}</span>
  </div>
  <p class="card-meta"><code>{escape(d.id)}</code> · version {d.current_version} ·
    <time datetime="{updated}" title="{updated}">updated {ago}</time></p>
  {f'<ul class="labels">{labels}</ul>' if labels else ""}
</li>"""


def _requests(requests: list[MetricRequest]) -> str:
    if not requests:
        return ""
    newest = sorted(requests, key=lambda r: (r.created_at, r.id), reverse=True)
    items = "".join(
        f'<li><span>{escape(r.description)}</span> <time datetime="{escape(r.created_at)}">'
        f"{escape(r.created_at[:10])}</time></li>"
        for r in newest[:SHOWN_REQUESTS]
    )
    more = len(newest) - SHOWN_REQUESTS
    rest = f"<p>and {more} more (<code>prumi requests</code>)</p>" if more > 0 else ""
    return f"""<section class="requests" aria-labelledby="requests-heading">
  <h2 id="requests-heading">Metric requests</h2>
  <ul>{items}</ul>
  {rest}
</section>"""


def home(
    registry: Registry,
    source: Source,
    project: str | None,
    data_through: dt.date | None,
    mcp_url: str,
    now: dt.datetime,
    viewer: Viewer = LOCAL_VIEWER,
) -> str:
    info = source.adapter.describe()
    facts = [info.adapter, project, info.connection]
    if data_through is not None:
        facts.append(f"data through {data_through.isoformat()}")
    found = cards(registry, source, viewer)
    if found:
        dashboards = f"""<section aria-labelledby="dashboards-heading">
  <div class="section-head">
    <h1 id="dashboards-heading">Dashboards</h1>
    <input type="search" id="search" placeholder="Search by title, id or metric"
           aria-label="Search dashboards" autocomplete="off">
  </div>
  <ul class="cards" id="cards">{"".join(_card(c, now) for c in found)}</ul>
  <p class="no-match" id="no-match" hidden>No dashboard matches your search.</p>
</section>"""
    else:
        command = f"claude mcp add --scope user --transport http prumi {mcp_url}"
        dashboards = f"""<section class="empty" aria-labelledby="dashboards-heading">
  <h1 id="dashboards-heading">No dashboards yet</h1>
  <p>Ask your AI agent to build a dashboard; it appears here as a draft.</p>
  <p>To connect Claude Code, run:</p>
  <div class="command">
    <code id="connect-command">{escape(command)}</code>
    <button type="button" class="copy" data-copy="connect-command">Copy</button>
  </div>
</section>"""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prumi</title>
<link rel="stylesheet" href="/theme.css">
<link rel="stylesheet" href="/home.css">
<script src="/home.js" defer></script>
</head>
<body>
<header class="top">
  <span class="wordmark">Prumi</span>
  <p class="source" id="source">{" · ".join(f"<span>{escape(f)}</span>" for f in facts if f)}</p>
</header>
<main>
{dashboards}
{_requests(registry.metric_requests())}
</main>
<footer class="bottom">Prumi {escape(__version__)} · <a href="{DOCS_URL}">Docs</a></footer>
</body>
</html>
"""


# The frame: the stored HTML, sandboxed, with the runtime injected

_HEAD = re.compile(r"<head(?:\s[^>]*)?>", re.IGNORECASE)
_HTML = re.compile(r"<html(?:\s[^>]*)?>", re.IGNORECASE)
_DOCTYPE = re.compile(r"\A\s*<!doctype[^>]*>", re.IGNORECASE)


def frame_html(html: str, origin: str, locale: str) -> str:
    """The stored HTML with the theme and the runtime first in <head> (added if missing). The
    runtime reads the server's locale from its tag, so prumi.locale is set before page
    scripts run."""
    script = (
        f'<link rel="stylesheet" href="{origin}/theme.css">'
        f'<script src="{origin}/provenance.js"></script>'
        f'<script src="{origin}/runtime.js" data-locale="{escape(locale)}"></script>'
    )
    if match := _HEAD.search(html):
        return html[: match.end()] + script + html[match.end() :]
    if match := _HTML.search(html):
        return html[: match.end()] + f"<head>{script}</head>" + html[match.end() :]
    if match := _DOCTYPE.search(html):
        return html[: match.end()] + f"<head>{script}</head>" + html[match.end() :]
    return f"<head>{script}</head>" + html


def frame_csp(origin: str, hosts: AllowedHosts) -> str:
    """The frame's policy. `sandbox` applies even when the frame URL is opened directly. The
    origin is listed explicitly, not 'self': the sandboxed document has an opaque origin."""
    return "; ".join(
        [
            "sandbox allow-scripts",
            "default-src 'none'",
            " ".join(["script-src", origin, "'unsafe-inline'", *hosts.script]),
            " ".join(["style-src", origin, "'unsafe-inline'", *hosts.style]),
            " ".join(["font-src", "data:", *hosts.font]),
            "img-src 'none'",  # numbers in images can't be checked; icons are inline SVG
            "connect-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            f"frame-ancestors {origin}",
        ]
    )


# The shell: Prumi's page around the frame


def badge(status: VersionStatus) -> tuple[str, str]:
    """(kind, text) of the trust badge: from the registry, never from the HTML."""
    if status.version != status.current_version:
        return "old", f"Version {status.version} of {status.current_version} · not current"
    if status.status == "certified":
        return "certified", f"Certified by {status.certified_by} on {status.certified_at[:10]}"
    return "draft", "Draft · not certified"


def labels_and_formats(
    spec: DashboardSpec, source: Source
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """What prumi.label and prumi.format know: the spec's metrics and dimensions."""
    metrics = sorted({m for block in spec.data for m in block.metrics})
    dimensions = {d for block in spec.data for d in block.dimensions}
    dimensions |= {f.dimension for f in spec.filters if isinstance(f, DimensionFilter)}
    labels: dict[str, str] = {}
    formats: dict[str, dict[str, Any]] = {}
    for name in metrics:
        metric = source.metric(name)
        labels[name] = metric.label if metric else name
        if metric:
            f = metric.format
            formats[name] = {"type": f.type, "unit": f.unit, "decimals": f.decimals}
    for name in sorted(dimensions | {METRIC_TIME}):
        dimension = source.dimension(name)
        labels[name] = dimension.label if dimension else name
    return labels, formats


def shell_config(version: Version, source: Source) -> dict[str, Any]:
    """What shell.js needs: the API path, filters, blocks, interactions, labels, formats."""
    spec = version.spec
    labels, formats = labels_and_formats(spec, source)
    return {
        "dashboard": spec.dashboard.id,
        "version": version.version,
        "api": f"/api/dashboards/{spec.dashboard.id}/versions/{version.version}",
        "filters": [f.model_dump(mode="json") for f in spec.filters],
        "blocks": [{"id": b.id, "filters": list(b.filters)} for b in spec.data],
        "interactions": [i.model_dump(mode="json", by_alias=True) for i in spec.interactions],
        "labels": labels,
        "formats": formats,
        "bindings": {s: list(b) for s, b in spec.presentation.blocks_of().items()},
        "spec": spec.model_dump(mode="json", by_alias=True),
    }


def _json_for_script(data: Any) -> str:
    # Inside <script type="application/json">: no "</script>" can close it early.
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def shell(version: Version, status: VersionStatus, source: Source) -> str:
    spec = version.spec
    kind, text = badge(status)
    frame = f"/d/{spec.dashboard.id}/v/{version.version}/frame"
    config = _json_for_script(shell_config(version, source))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(spec.dashboard.title)} · Prumi</title>
<link rel="stylesheet" href="/theme.css">
<style>
  html, body {{ margin: 0; height: 100%; font-family: var(--prumi-font);
               background: var(--prumi-bg); color: var(--prumi-text); }}
  body {{ display: flex; flex-direction: column; }}
  #prumi-bar {{ display: flex; gap: 16px; align-items: center; padding: 8px 16px;
               border-bottom: 1px solid var(--prumi-border); background: var(--prumi-surface);
               font-size: 14px; }}
  #prumi-title {{ font-weight: 600; font-size: 16px; }}
  #prumi-badge {{ padding: 2px 10px; border-radius: 12px; border: 1px solid currentColor; }}
  #prumi-badge[data-kind="draft"] {{ color: var(--prumi-negative); }}
  #prumi-badge[data-kind="certified"] {{ color: var(--prumi-positive); }}
  #prumi-badge[data-kind="old"] {{ color: var(--prumi-text-muted); }}
  #prumi-freshness {{ color: var(--prumi-text-muted); }}
  #prumi-details {{ margin-left: auto; position: relative; }}
  #prumi-details ul {{ position: absolute; right: 0; z-index: 1; background: var(--prumi-surface);
                      border: 1px solid var(--prumi-border); border-radius: var(--prumi-radius);
                      margin: 4px 0 0; padding: 8px 24px; width: 28em; }}
  #prumi-unverified {{ padding: 6px 16px; border-bottom: 1px solid var(--prumi-border);
                       color: var(--prumi-negative); background: var(--prumi-surface);
                       font-size: 14px; }}
  #prumi-unverified summary {{ cursor: pointer; font-weight: 600; }}
  #prumi-unverified ul {{ margin: 4px 0; color: var(--prumi-text); }}
  #prumi-frame {{ flex: 1; border: 0; width: 100%; }}
</style>
</head>
<body>
<div id="prumi-bar">
  <span id="prumi-title">{escape(spec.dashboard.title)}</span>
  <span id="prumi-badge" data-kind="{kind}">{escape(text)}</span>
  <span id="prumi-freshness"></span>
  <details id="prumi-details" hidden>
    <summary>Details</summary><ul id="prumi-warnings"></ul>
  </details>
</div>
<details id="prumi-unverified" hidden>
  <summary>This page shows numbers Prumi didn't deliver.</summary>
  <ul id="prumi-unverified-list"></ul>
</details>
<iframe id="prumi-frame" src="{frame}" sandbox="allow-scripts"
        title="{escape(spec.dashboard.title)}"></iframe>
<script type="application/json" id="prumi-dashboard">{config}</script>
<script src="/shell.js"></script>
</body>
</html>
"""
