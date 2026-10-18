"""The MCP server over Streamable HTTP: tools, instructions, ranking, query (AC 26, 27, 28)."""

import asyncio
import json
import re
from pathlib import Path

import duckdb
import pytest
from mcp import Client
from prumi.mcp import rank, tokens
from prumi.registry import Content
from prumi.spec import DashboardSpec

SPEC_MD = Path(__file__).parents[3] / "specs" / "mvp" / "spec.md"
TOOLS = {
    "authoring_guide",
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


def test_exactly_seven_tools_and_the_instructions(server):
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
    validated = ok(call(server, "validate_spec", spec=spec, html=html))
    assert validated == {
        "ok": True,
        "errors": [],
        "warnings": [],
        "candidate_id": validated["candidate_id"],
    }
    assert validated["candidate_id"].startswith("v0:")
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


# Publishing by candidate (AC 33)


def test_validate_returns_a_candidate_only_for_valid_spec_and_html(server, demo_dashboard):
    spec, html = demo_dashboard
    with_html = ok(call(server, "validate_spec", spec=spec, html=html))
    assert (
        with_html["candidate_id"]
        == Content.of(DashboardSpec.model_validate(spec), html).fingerprint
    )
    assert ok(call(server, "validate_spec", spec=spec))["candidate_id"] is None
    broken = {**spec, "data": [{**spec["data"][0], "metrics": ["revnue"]}, *spec["data"][1:]]}
    assert ok(call(server, "validate_spec", spec=broken, html=html))["candidate_id"] is None


def test_publishing_by_candidate_is_publishing_the_content(server, services, demo_dashboard):
    spec, html = demo_dashboard
    spec = {**spec, "dashboard": {"id": "by-candidate", "title": "By candidate"}}

    async def work(client):
        validated = await client.call_tool("validate_spec", {"spec": spec, "html": html})
        candidate = validated.structured_content["candidate_id"]
        by_id = await client.call_tool("publish_dashboard", {"candidate_id": candidate})
        by_content = await client.call_tool("publish_dashboard", {"spec": spec, "html": html})
        return ok(by_id), ok(by_content)

    by_id, by_content = session(server, work)
    assert (by_id["id"], by_id["version"]) == ("by-candidate", 1)
    assert (by_content["id"], by_content["version"]) == ("by-candidate", 1)  # nothing new
    assert services.registry.version("by-candidate", 2) is None


def test_unknown_candidates_and_bad_arguments(server, demo_dashboard):
    spec, html = demo_dashboard
    [issue] = issues(call(server, "publish_dashboard", candidate_id="v0:nope"))
    assert (issue["code"], issue["path"]) == ("publish.unknown_candidate", "candidate_id")
    assert "validate_spec again with the spec and HTML" in issue["hint"]
    for arguments in ({}, {"spec": spec}, {"candidate_id": "v0:x", "spec": spec, "html": html}):
        [issue] = issues(call(server, "publish_dashboard", **arguments))
        assert issue["code"] == "publish.invalid_arguments"


def test_candidates_expire_and_are_bounded():
    from prumi.mcp import Candidates

    now = [0.0]
    store = Candidates(size=2, ttl=3600, clock=lambda: now[0])

    def spec(n):
        return {
            "spec_version": 0,
            "dashboard": {"id": f"d{n}", "title": "D"},
            "data": [
                {"id": "kpi", "metrics": ["revenue"], "filters": [], "window": {"to_date": "year"}}
            ],
        }

    first, second = store.add(spec(1), "<p>1</p>"), store.add(spec(2), "<p>2</p>")
    assert store.get(first) == (spec(1), "<p>1</p>")
    store.add(spec(3), "<p>3</p>")  # over the size: the oldest goes
    assert store.get(first) is None and store.get(second) is not None
    now[0] = 3601
    assert store.get(second) is None  # expired after an hour


def test_publish_revalidates_a_candidate(
    tmp_path, demo_services, demo_dashboard, demo_project, make_demo_config
):
    """The catalog changes between validate and publish: the stored content is checked again."""
    from prumi.config import load_config
    from prumi.mcp import create_mcp
    from prumi.services import Services

    spec, html = demo_dashboard
    spec = json.loads(json.dumps(spec))
    spec["data"][0]["metrics"] = ["revenue", "discount_rate"]
    mcp = create_mcp(demo_services)

    async def work():
        async with Client(mcp) as client:
            validated = await client.call_tool("validate_spec", {"spec": spec, "html": html})
            candidate = validated.structured_content["candidate_id"]
            assert candidate
            # The data team removes discount_rate from the manifest; the server restarts its
            # source (the candidate store would not survive a real restart, so swap in place).
            manifest = json.loads(demo_project.manifest_path.read_text())
            manifest["metrics"] = [m for m in manifest["metrics"] if m["name"] != "discount_rate"]
            changed = tmp_path / "semantic_manifest.json"
            changed.write_text(json.dumps(manifest))
            later = Services(load_config(make_demo_config(tmp_path / "later", changed)))
            demo_services.source = later.source
            demo_services.__dict__.pop("engine", None)
            return await client.call_tool("publish_dashboard", {"candidate_id": candidate})

    (tmp_path / "later").mkdir()
    result = asyncio.run(work())
    assert [i["code"] for i in issues(result)] == ["R7.unknown_metric"]
    assert demo_services.registry.dashboards() == []


def test_authoring_guide_returns_the_guide(server):
    from prumi.mcp import AUTHORING_GUIDE

    result = call(server, "authoring_guide")
    assert not result.is_error
    assert result.content[0].text == AUTHORING_GUIDE.read_text(encoding="utf-8")
