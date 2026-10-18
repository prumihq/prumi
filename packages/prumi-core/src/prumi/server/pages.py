"""Prumi's own pages."""

from __future__ import annotations

import json
import re
from html import escape
from typing import Any

from prumi.adapters import METRIC_TIME
from prumi.registry import Registry, Version, VersionStatus
from prumi.source import Source
from prumi.spec import DashboardSpec, DimensionFilter
from prumi.validate import AllowedHosts


def index(registry: Registry) -> str:
    """A plain list of dashboards with their status (for the demo)."""
    rows = []
    for d in registry.dashboards():
        status = registry.status(d.id, d.current_version)
        rows.append(
            f'<tr><td><a href="/d/{escape(d.id)}">{escape(d.title)}</a></td>'
            f"<td>{escape(d.id)}</td><td>{d.current_version}</td><td>{status.status}</td></tr>"
        )
    body = (
        "<table><tr><th>Dashboard</th><th>ID</th><th>Version</th><th>Status</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else "<p>No dashboards yet. Ask your agent to publish one.</p>"
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Prumi</title>'
        "<style>body{font-family:system-ui,sans-serif;margin:24px}"
        "td,th{padding:4px 12px;text-align:left}</style></head>"
        f"<body><h1>Dashboards</h1>{body}</body></html>"
    )


# The frame: the stored HTML, sandboxed, with the runtime injected

_HEAD = re.compile(r"<head(?:\s[^>]*)?>", re.IGNORECASE)
_HTML = re.compile(r"<html(?:\s[^>]*)?>", re.IGNORECASE)
_DOCTYPE = re.compile(r"\A\s*<!doctype[^>]*>", re.IGNORECASE)


def frame_html(html: str, origin: str) -> str:
    """The stored HTML with the theme and the runtime first in <head> (added if missing)."""
    script = (
        f'<link rel="stylesheet" href="{origin}/theme.css">'
        f'<script src="{origin}/provenance.js"></script><script src="{origin}/runtime.js"></script>'
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
        "bindings": dict(spec.presentation.bindings),
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
<iframe id="prumi-frame" src="{frame}" sandbox="allow-scripts"
        title="{escape(spec.dashboard.title)}"></iframe>
<script type="application/json" id="prumi-dashboard">{config}</script>
<script src="/shell.js"></script>
</body>
</html>
"""
