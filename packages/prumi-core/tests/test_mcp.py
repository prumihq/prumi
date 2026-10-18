"""The MCP server over Streamable HTTP: tools, instructions, ranking, query (AC 26, 27, 28)."""

import asyncio
import json
import re
from pathlib import Path

import duckdb
import pytest
from mcp import Client
from prumi.mcp import rank, tokens

SPEC_MD = Path(__file__).parents[3] / "specs" / "mvp" / "spec.md"
TOOLS = {
    "find_metrics",
    "describe_metric",
    "query",
    "validate_spec",
    "publish_dashboard",
    "request_metric",
}


@pytest.fixture(scope="module")
def services(live_services):
    return live_services()


@pytest.fixture(scope="module")
def server(services, live_server):
    with live_server(services) as live:
        yield live


def session(server, work):
    """Run `work(client)` in an MCP session over Streamable HTTP."""

    async def run():
        async with Client(f"{server.url}/mcp") as client:
            return await work(client)

    return asyncio.run(run())


def call(server, tool, **arguments):
    return session(server, lambda client: client.call_tool(tool, arguments))


def ok(result) -> dict:
    assert not result.is_error, result.content
    return result.structured_content


def issues(result) -> list[dict]:
    """A tool error's content is the issue list."""
    assert result.is_error
    return json.loads(result.content[0].text)


# AC 26


def test_exactly_six_tools_and_the_instructions(server):
    async def work(client):
        listed = await client.list_tools()
        return listed.tools, client.instructions, client.server_info

    tools, instructions, info = session(server, work)
    assert {t.name for t in tools} == TOOLS
    assert info.name == "prumi"
    # The instructions are section 14's text, verbatim.
    quote = re.search(r"\*\*Server instructions\*\*.*?\n\n((?:> .*\n)+)", SPEC_MD.read_text())
    expected = "\n".join(line[2:] for line in quote.group(1).splitlines())
    assert instructions == expected
    query = next(t for t in tools if t.name == "query")
    assert set(query.input_schema["required"]) == {"metrics", "window"}


# AC 27


def test_find_metrics_ranks_by_name_label_and_description(server):
    found = ok(call(server, "find_metrics", question="average order value"))["metrics"]
    assert found[0]["name"] == "average_order_value"
    assert found[0]["score"] == 6  # three words, each in the name and label
    assert found[0]["format"] == {"type": "currency", "unit": "USD", "decimals": 2}
    assert len(found) == 12  # the catalog is smaller than the default limit: all of it
    assert [m["score"] for m in found] == sorted((m["score"] for m in found), reverse=True)
    two = ok(call(server, "find_metrics", question="revenue", limit=2))["metrics"]
    assert [m["name"] for m in two] == ["revenue", "average_order_value"]


def test_ranking_rules():
    assert tokens("How many Customers__per region?") == {"customers", "region"}
    # Ties are broken by name.
    from prumi.adapters import Format, Metric

    def metric(name, description=""):
        return Metric(name, name, description, "simple", "sum", True, Format(), None, None)

    ranked = rank("sales", [metric("b_sales"), metric("a_sales"), metric("c", "sales")], 10)
    assert [(s, m.name) for s, m in ranked] == [(2, "a_sales"), (2, "b_sales"), (1, "c")]


def test_describe_metric(server):
    customers = ok(call(server, "describe_metric", name="customers"))
    assert customers["kind"] == "simple" and customers["aggregation"] == "count_distinct"
    assert not customers["additive"]
    assert customers["caveats"][0].startswith("non-additive: never sum or average its rows")
    assert [d["name"] for d in customers["dimensions"]] == [
        "customer__country",
        "customer__region",
        "customer__segment",
        "order__channel",
    ]
    assert customers["time_grains"] == ["day", "week", "month", "quarter", "year"]

    discount = ok(call(server, "describe_metric", name="discount_rate"))
    assert "no format declared: values show as plain numbers" in discount["caveats"]
    stock = ok(call(server, "describe_metric", name="units_on_hand"))
    assert [d["name"] for d in stock["dimensions"]] == ["product__category"]

    [issue] = issues(call(server, "describe_metric", name="revnue"))
    assert issue["code"] == "R7.unknown_metric" and "Did you mean revenue" in issue["message"]
    assert "request_metric" in issue["hint"]


# AC 28 and the query tool


def test_query_revenue_by_day_over_two_years(server, demo_project):
    result = ok(
        call(
            server,
            "query",
            metrics=["revenue"],
            time_grain="day",
            window={"last": 2, "grain": "year"},
        )
    )
    assert len(result["rows"]) == 200 and result["truncated"]
    assert result["row_count"] == 731
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        [(expected,)] = conn.execute(
            "SELECT sum(revenue)::double FROM main.order_items"
            " WHERE order_date BETWEEN '2024-01-01' AND '2025-12-31'"
        ).fetchall()
    assert result["total"]["revenue"] == pytest.approx(expected)
    assert result["rows"][0]["metric_time"] == "2024-01-01"
    assert "result.truncated" in [w["code"] for w in result["warnings"]]


def test_query_with_where_top_n_and_compare(server):
    result = ok(
        call(
            server,
            "query",
            metrics=["revenue", "customers"],
            dimensions=["customer__region"],
            window={"last": 3, "grain": "month"},
            where=[{"dimension": "order__channel", "op": "in", "values": ["web", "store"]}],
            sort={"by": "revenue", "descending": True},
            top_n=3,
            others=True,
            total=True,
            compare="previous_year",
        )
    )
    assert len(result["rows"]) == 3 and result["others"] and result["total"]
    assert result["row_count"] == 3 and not result["truncated"]
    assert set(result["rows"][0]["_previous"]) == {"revenue", "customers"}
    assert result["comparison_window"]["start"] == "2025-07-01"


@pytest.mark.parametrize(
    ("arguments", "codes"),
    [
        ({"metrics": ["revnue"], "window": {"last": 3, "grain": "month"}}, ["R7.unknown_metric"]),
        (
            {
                "metrics": ["units_on_hand"],
                "dimensions": ["customer__region"],
                "window": {"last": 3, "grain": "month"},
            },
            ["R2.invalid_dimension"],
        ),
        (
            {
                "metrics": ["units_on_hand"],
                "window": {"last": 3, "grain": "month"},
                "where": [{"dimension": "customer__region", "op": "eq", "values": ["North"]}],
            },
            ["R2.filter_dimension_invalid"],
        ),
        (
            {
                "metrics": ["revenue"],
                "window": {"last": 3, "grain": "month"},
                "where": [{"dimension": "customer__region", "op": "eq", "values": ["{{ 1 }}"]}],
            },
            ["filter.invalid_value"],
        ),
        (
            {
                "metrics": ["revenue"],
                "window": {"last": 3, "grain": "month"},
                "where": [{"dimension": "customer__region", "op": "eq", "values": ["a", "b"]}],
            },
            ["filter.invalid_value"],
        ),
        (
            {"metrics": ["revenue"], "time_grain": "week", "window": {"last": 3, "grain": "month"}},
            ["R4.grain_mismatch"],
        ),
        (
            {"metrics": ["revenue"], "window": {"last": 3, "grain": "month"}, "others": True},
            ["R6.others_requires_top_n"],
        ),
        (
            {"metrics": ["revenue"], "window": {"last": 3, "grain": "month"}, "top_n": 0},
            ["spec.invalid"],
        ),
    ],
)
def test_query_errors_are_issue_lists(server, arguments, codes):
    assert [i["code"] for i in issues(call(server, "query", **arguments))] == codes


def test_query_needs_a_window(server):
    assert call(server, "query", metrics=["revenue"]).is_error


def test_query_warnings_come_with_the_result(server):
    result = ok(
        call(server, "query", metrics=["discount_rate"], window={"last": 1, "grain": "month"})
    )
    assert [w["code"] for w in result["warnings"]] == ["R10.no_format"]


# Validating, publishing and requesting metrics


def test_validate_publish_and_request_metric(server, services, demo_dashboard):
    spec, html = demo_dashboard
    broken = json.loads(json.dumps(spec))
    broken["data"][0]["metrics"] = ["revenue", "average_ordr_value"]
    report = ok(call(server, "validate_spec", spec=broken, html=html))
    assert not report["ok"]
    assert [e["code"] for e in report["errors"]] == ["R7.unknown_metric"]

    [issue] = issues(call(server, "publish_dashboard", spec=broken, html=html))
    assert issue["code"] == "R7.unknown_metric"
    assert services.registry.dashboards() == []

    request = ok(
        call(server, "request_metric", description="Net revenue after returns", reason="Asked")
    )
    assert request["id"] == 1 and "metric_requests" in request["message"]
    spec = {**spec, "metric_requests": [request["id"]]}
    assert ok(call(server, "validate_spec", spec=spec, html=html)) == {
        "ok": True,
        "errors": [],
        "warnings": [],
    }
    port = services.config.server.port
    published = ok(call(server, "publish_dashboard", spec=spec, html=html))
    assert published == {
        "id": "sales",
        "version": 1,
        "url": f"http://127.0.0.1:{port}/d/sales",
        "status": "draft",
        "warnings": [],
    }
    assert ok(call(server, "publish_dashboard", spec=spec, html=html))["version"] == 1
    assert services.registry.metric_requests()[0].description == "Net revenue after returns"


def test_mcp_rejects_other_origins(server):
    import httpx2

    response = httpx2.post(
        f"{server.url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Origin": "https://evil.example", "Accept": "application/json, text/event-stream"},
    )
    assert response.status_code in (400, 403)
    other_host = httpx2.get(f"{server.url}/mcp", headers={"Host": "evil.example"})
    assert other_host.status_code == 403
