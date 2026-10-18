"""Prumi's own pages."""

from __future__ import annotations

from html import escape

from prumi.registry import Registry


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
