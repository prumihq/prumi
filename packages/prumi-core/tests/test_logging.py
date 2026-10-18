"""Logs hold query metadata, never row values or filter values, at any level."""

import asyncio
import json
import logging

import pytest
from mcp import Client
from prumi.mcp import create_mcp

SENTINEL = "SENTINEL-7f3a9c"


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG)  # every logger, every level
    return caplog


def all_text(caplog) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(repr(record.args))
        if record.exc_info:
            parts.append(logging.Formatter().formatException(record.exc_info))
    return "\n".join(parts)


def test_no_values_in_logs(logs, published, client, demo_dashboard):
    spec, html = demo_dashboard
    base = "/api/dashboards/sales/versions/1"

    # The block API, with the sentinel as a filter value; also a refused one.
    assert (
        client.post(f"{base}/blocks/kpi", json={"filters": {"region": [SENTINEL]}}).status_code
        == 200
    )
    assert (
        client.post(
            f"{base}/blocks/by_region", json={"filters": {"region": [SENTINEL]}}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{base}/blocks/kpi", json={"filters": {"region": [SENTINEL + "{"]}}
        ).status_code
        == 400
    )
    regions = client.get(f"{base}/filters/region/values").json()["values"]
    rows = client.post(f"{base}/blocks/by_region", json={"filters": {}}).json()["rows"]

    # Agent queries, validation (with its dry run) and publishing over MCP.
    with_default = json.loads(json.dumps(spec))
    with_default["filters"][1]["default"] = [SENTINEL]

    async def agent():
        async with Client(create_mcp(published)) as mcp:
            where = [{"dimension": "customer__region", "op": "in", "values": [SENTINEL]}]
            window = {"last": 3, "grain": "month"}
            result = await mcp.call_tool(
                "query", {"metrics": ["revenue"], "window": window, "where": where}
            )
            assert not result.is_error
            bad = [{"dimension": "units", "op": "eq", "values": [SENTINEL]}]
            assert (
                await mcp.call_tool(
                    "query", {"metrics": ["revenue"], "window": window, "where": bad}
                )
            ).is_error
            big = await mcp.call_tool(
                "query",
                {
                    "metrics": ["revenue"],
                    "time_grain": "day",
                    "window": {"last": 2, "grain": "year"},
                },
            )
            assert big.structured_content["truncated"]
            assert not (
                await mcp.call_tool("validate_spec", {"spec": with_default, "html": html})
            ).is_error
            assert not (
                await mcp.call_tool("publish_dashboard", {"spec": with_default, "html": html})
            ).is_error

    asyncio.run(agent())

    text = all_text(logs)
    assert SENTINEL not in text
    # Row values: dimension values and metric values from the results.
    for value in regions[:-1] + [r["customer__region"] for r in rows if r["customer__region"]]:
        assert value not in text, value
    for row in rows:
        assert repr(row["revenue"]) not in text and f"{row['revenue']:.2f}" not in text

    # What is logged: origin, step, viewer, rows, duration; SQL only at DEBUG, redacted.
    messages = [r.getMessage() for r in logs.records]
    assert any(
        m.startswith("block sales/1/kpi main: viewer=local rows=1 duration_ms=") for m in messages
    )
    assert any(m.startswith("block sales/1/by_region others: viewer=local rows=") for m in messages)
    assert any(m.startswith("agent query main: viewer=local rows=") for m in messages)
    assert any(m.startswith("agent query total: viewer=local rows=1") for m in messages)
    assert any(m.startswith("dry run sales/trend freshness: viewer=local") for m in messages)
    assert any(
        m.startswith("filter values sales/1/region values: viewer=local rows=8") for m in messages
    )
    sql = [r for r in logs.records if r.getMessage().startswith("compiled SQL")]
    assert sql and all(r.levelno == logging.DEBUG for r in sql)
    assert not [r for r in sql if "'" in r.getMessage()]
    queries = [r for r in logs.records if r.name == "prumi.query"]
    assert queries and all(r.levelno == logging.INFO for r in queries)


def test_redacting_compiled_sql():
    from prumi_adapter_dbt.adapter import redact

    sql = (
        'SELECT subq_10.region AS r, DATE_TRUNC(\'month\', d) FROM "demo"."main"."t2" '
        "WHERE r IN ('Côte d''Ivoire', 'x') AND n > 12.5 AND m = 3 LIMIT 6"
    )
    assert redact(sql) == (
        'SELECT subq_10.region AS r, DATE_TRUNC(?, d) FROM "demo"."main"."t2" '
        "WHERE r IN (?, ?) AND n > ? AND m = ? LIMIT ?"
    )
