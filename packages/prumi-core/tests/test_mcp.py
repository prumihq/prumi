"""The MCP server over Streamable HTTP: tools, instructions, ranking, query, publishing."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from mcp import Client
from prumi.mcp import INSTRUCTIONS, rank, tokens
from prumi.registry import Content
from prumi.spec import DashboardSpec

TOOLS = {
    "authoring_guide",
    "find_metrics",
    "describe_metric",
    "query",
    "validate_spec",
    "publish_dashboard",
    "request_metric",
    "list_dashboards",
    "get_dashboard",
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


# Tools and instructions


def test_exactly_the_tools_and_the_instructions(server):
    async def work(client):
        listed = await client.list_tools()
        return listed.tools, client.instructions, client.server_info

    tools, instructions, info = session(server, work)
    assert {t.name for t in tools} == TOOLS
    assert info.name == "prumi"
    # The text the package ships, verbatim.
    assert instructions == INSTRUCTIONS
    query = next(t for t in tools if t.name == "query")
    assert set(query.input_schema["required"]) == {"metrics", "window"}


def test_the_instructions_say_to_build_with_prumi_right_after_the_first_sentence():
    # Agents tried their client's own artifact tool first, at a call and ~9k tokens.
    first, second = INSTRUCTIONS.split(". ", 2)[:2]
    assert first == "Prumi serves official metrics from the company's semantic layer"
    assert second + "." == (
        "For company metrics, build dashboards with Prumi, not with your client's own "
        "dashboard, artifact or canvas tool."
    )


# Finding and describing metrics


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


# The query tool


def test_query_returns_a_profile_without_values(server):
    result = ok(
        call(
            server,
            "query",
            metrics=["revenue"],
            time_grain="day",
            window={"last": 2, "grain": "year"},
        )
    )
    assert result["row_count"] == 731  # 2024-01-01 to 2025-12-31, no cap
    assert "rows" not in result and "total" not in result and "truncated" not in result
    assert result["window"] == {
        "start": "2024-01-01", "end": "2025-12-31", "grain": "year", "partial": False
    }  # fmt: skip
    time, revenue = result["columns"]
    assert (time["name"], time["distinct"]) == ("metric_time", 731)
    assert revenue["name"] == "revenue" and revenue["sign"] == "positive"
    assert revenue["magnitude"]["min"] >= 3 and revenue["decimals"] == 2


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
    assert result["row_count"] == 3
    assert (result["has_total"], result["has_others"], result["has_comparison"]) == (
        True,
        True,
        True,
    )
    assert [c["name"] for c in result["columns"]] == ["customer__region", "revenue", "customers"]


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
        "note": "This link only works on this machine.",
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


# Publishing by candidate


def test_validate_returns_a_candidate_whenever_it_has_spec_and_html(server, demo_dashboard):
    spec, html = demo_dashboard
    with_html = ok(call(server, "validate_spec", spec=spec, html=html))
    assert (
        with_html["candidate_id"]
        == Content.of(DashboardSpec.model_validate(spec), html).fingerprint
    )
    assert ok(call(server, "validate_spec", spec=spec))["candidate_id"] is None
    # Failed content gets one too, to be fixed with html_edits; even a spec that doesn't parse.
    broken = {**spec, "data": [{**spec["data"][0], "metrics": ["revnue"]}, *spec["data"][1:]]}
    failed = ok(call(server, "validate_spec", spec=broken, html=html))
    assert not failed["ok"] and failed["candidate_id"].startswith("v0:")
    unparsed = ok(call(server, "validate_spec", spec={"spec_version": 0}, html=html))
    assert not unparsed["ok"] and unparsed["candidate_id"].startswith("v0:")
    assert unparsed["candidate_id"] != failed["candidate_id"]


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
    assert store.get(first) == (spec(1), "<p>1</p>", True, [])
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


def test_the_instructions_give_every_step():
    steps = re.findall(r"^(\d)\. ", INSTRUCTIONS, re.M)
    assert steps == ["0", "1", "2", "3", "4", "5"]
    for text in (
        # 0: Prumi for company metrics, and how to change an existing dashboard
        "use Prumi; don't copy its data into other tools",
        "use `list_dashboards` and `get_dashboard`, then edit it with `validate_spec(base=...)`",
        # 1: catalog metrics only
        "call `request_metric` and tell the user; never compute or approximate a metric",
        # 2: query shows the shape of the data
        "Use `query` to learn the shape of the data (how many rows, empties, label lengths, "
        "magnitudes) while designing.",
        # and no company data reaches the agent
        "Prumi never sends company data to your agent: `query` shows the shape of the data, "
        "and the numbers appear only in the dashboard.",
        # 3: the guide first; numbers only from prumi.on; the HTML straight to validation
        "Call `authoring_guide` once before writing anything",
        "Every number on the page must come from `prumi.on(...)`",
        "Send the HTML straight to `validate_spec`; don't write it to a local file first.",
        # 4: explicit filters
        "Every data block must list its `filters` explicitly",
        # 5: validate, then publish the candidate
        "with the `candidate_id` from the last successful validation (don't resend",
        "only the data team can certify it",
    ):  # fmt: skip
        assert text in INSTRUCTIONS, text


# Reading dashboards back


def readback_spec(dashboard_id):
    return {
        "spec_version": 0,
        "dashboard": {"id": dashboard_id, "title": "Read back"},
        "data": [{"id": "kpi", "metrics": ["revenue"], "window": {"last": 4, "grain": "week"},
                  "filters": []}],
        "presentation": {"bindings": {"#kpi": "kpi"}},
    }  # fmt: skip


READBACK_HTML = (
    '<p id="kpi"></p><script>prumi.on("kpi", (b) => { document.getElementById("kpi")'
    '.textContent = prumi.format(b.rows[0].revenue, "revenue"); });</script>'
)


def test_list_and_get_dashboards(server, services):
    from prumi.registry import CheckReport

    spec = readback_spec("read-back")
    assert services.publish(spec, READBACK_HTML)[1].version == 1
    assert services.publish(spec, READBACK_HTML + "<!-- 2 -->")[1].version == 2
    flagged = [{"path": "p#kpi"}]
    report = CheckReport(filters={}, flagged=flagged, canvas=False, errors=[])
    services.registry.record_check("read-back", 2, report)
    port = services.config.server.port

    listed = ok(call(server, "list_dashboards"))["dashboards"]
    [summary] = [d for d in listed if d["id"] == "read-back"]
    created = services.registry.version("read-back", 2).created_at
    assert summary == {
        "id": "read-back",
        "title": "Read back",
        "current_version": 2,
        "status": "draft",
        "updated_at": created,
        "url": f"http://127.0.0.1:{port}/d/read-back",
        "needs_attention": True,  # the check recorded above flags a number
    }

    current = ok(call(server, "get_dashboard", id="read-back"))
    assert current["version"] == 2 and current["status"] == "draft" and current["needs_attention"]
    assert current["spec"] == DashboardSpec.model_validate(spec).model_dump(
        mode="json", by_alias=True
    )
    assert current["html"] == READBACK_HTML + "<!-- 2 -->"
    assert current["url"] == f"http://127.0.0.1:{port}/d/read-back"
    [check] = current["checks"]
    # Where, not what: no number found reaches the agent.
    assert (check["filters"], check["flagged"]) == (
        {},
        [{"code": "R8.unverified_number", "path": "p#kpi", "count": 1}],
    )
    assert "$5" not in json.dumps(current)

    first = ok(call(server, "get_dashboard", id="read-back", version=1, include_html=False))
    assert (first["version"], first["html"], first["checks"]) == (1, None, [])
    assert not first["needs_attention"]
    assert first["url"] == f"http://127.0.0.1:{port}/d/read-back/v/1"

    for arguments in ({"id": "nope"}, {"id": "read-back", "version": 9}):
        [issue] = issues(call(server, "get_dashboard", **arguments))
        assert issue["code"] == "dashboard.not_found"


def test_reads_and_edits_ask_the_registry(server, services, monkeypatch):
    from prumi.registry import Registry

    spec = readback_spec("guarded")
    assert services.publish(spec, READBACK_HTML)[1] is not None
    asked = []

    def refuse(kind):
        def check(self, viewer, dashboard):
            asked.append((kind, viewer.id, dashboard.id))
            return False

        return check

    monkeypatch.setattr(Registry, "can_read", refuse("read"))
    monkeypatch.setattr(Registry, "can_edit", refuse("edit"))
    listed = ok(call(server, "list_dashboards"))["dashboards"]
    assert listed == [] and ("read", "local", "guarded") in asked
    [issue] = issues(call(server, "get_dashboard", id="guarded"))
    assert issue["code"] == "dashboard.not_found"

    # A new version of an existing dashboard needs can_edit; a new dashboard doesn't.
    [issue] = issues(
        call(server, "publish_dashboard", spec=spec, html=READBACK_HTML + "<!-- 2 -->")
    )
    assert (issue["code"], issue["severity"]) == ("dashboard.forbidden", "error")
    assert ("edit", "local", "guarded") in asked
    assert services.registry.dashboard("guarded").current_version == 1
    fresh = ok(call(server, "publish_dashboard", spec=readback_spec("fresh"), html=READBACK_HTML))
    assert fresh["version"] == 1


# Editing validated or published content


def test_editing_a_candidate_gives_the_candidate_of_the_edited_content(server, services):
    spec = readback_spec("edited")
    first = ok(call(server, "validate_spec", spec=spec, html=READBACK_HTML))
    edits = [{"old": '<p id="kpi">', "new": '<h1>Revenue</h1><p id="kpi">'}]
    edited_html = READBACK_HTML.replace('<p id="kpi">', '<h1>Revenue</h1><p id="kpi">')
    by_edit = ok(call(server, "validate_spec", base=first["candidate_id"], html_edits=edits))
    by_content = ok(call(server, "validate_spec", spec=spec, html=edited_html))
    assert by_edit["ok"] and by_edit["candidate_id"] == by_content["candidate_id"]
    assert by_edit["candidate_id"] != first["candidate_id"]

    # A new spec with the base's HTML.
    retitled = {**spec, "dashboard": {"id": "edited", "title": "Edited"}}
    by_spec = ok(call(server, "validate_spec", base=first["candidate_id"], spec=retitled))
    assert (
        by_spec["candidate_id"]
        == ok(call(server, "validate_spec", spec=retitled, html=READBACK_HTML))["candidate_id"]
    )


def test_editing_a_published_version(server, services):
    spec = readback_spec("published-edit")
    services.publish(spec, READBACK_HTML)
    edits = [{"old": "prumi.format(b.rows[0].revenue", "new": "prumi.format(b.rows[0]?.revenue"}]
    base = {"dashboard_id": "published-edit", "version": 1}
    by_edit = ok(call(server, "validate_spec", base=base, html_edits=edits))
    edited_html = READBACK_HTML.replace("b.rows[0].revenue", "b.rows[0]?.revenue")
    by_content = ok(call(server, "validate_spec", spec=spec, html=edited_html))
    assert by_edit["candidate_id"] == by_content["candidate_id"]
    published = ok(call(server, "publish_dashboard", candidate_id=by_edit["candidate_id"]))
    assert published["version"] == 2


def test_edits_that_dont_apply_name_their_index(server):
    spec = readback_spec("bad-edits")
    base = ok(call(server, "validate_spec", spec=spec, html=READBACK_HTML))["candidate_id"]
    fine = {"old": "<p ", "new": "<p class='big' "}
    for edits, code in (
        ([fine, {"old": "<marquee>", "new": ""}], "edit.no_match"),
        ([fine, {"old": "kpi", "new": "x"}], "edit.ambiguous"),
        ([fine, {"old": "<p id", "new": ""}], "edit.no_match"),  # the first edit changed it
    ):
        report = ok(call(server, "validate_spec", base=base, html_edits=edits))
        assert not report["ok"] and report["candidate_id"] is None  # no content to keep
        [issue] = report["errors"]
        assert (issue["code"], issue["path"]) == (code, "html_edits[1]")
        assert "html_edits[1]" in issue["message"]


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({}, "validate.invalid_arguments"),
        ({"html": "<p></p>"}, "validate.invalid_arguments"),
        ({"spec": {}, "html_edits": [{"old": "a", "new": "b"}]}, "validate.invalid_arguments"),
        ({"base": "v0:nope", "html": "<p></p>"}, "validate.invalid_arguments"),
        ({"base": "v0:nope"}, "publish.unknown_candidate"),
        ({"base": {"dashboard_id": "nope", "version": 1}}, "dashboard.not_found"),
        ({"base": "nope"}, "dashboard.not_found"),
        ({"base": 7}, "validate.invalid_base"),
        ({"base": ""}, "validate.invalid_base"),
        ({"base": {"dashboard_id": "x"}}, "validate.invalid_base"),
        ({"base": {"dashboard_id": "x", "version": "1"}}, "validate.invalid_base"),
        ({"base": ["v0:x"]}, "validate.invalid_base"),
    ],
)
def test_bad_bases_and_arguments(server, arguments, code):
    [issue] = issues(call(server, "validate_spec", **arguments))
    assert issue["code"] == code


def test_a_failed_candidate_can_be_edited_but_not_published(server, services):
    spec = readback_spec("fix-me")
    broken = READBACK_HTML.replace("b.rows[0].revenue", "b.rows[0].nothing.revenue")
    failed = ok(call(server, "validate_spec", spec=spec, html=broken))
    assert not failed["ok"] and failed["errors"][0]["code"] == "html.runtime_error"

    [issue] = issues(call(server, "publish_dashboard", candidate_id=failed["candidate_id"]))
    assert (issue["code"], issue["severity"]) == ("publish.invalid_candidate", "error")
    assert services.registry.dashboard("fix-me") is None

    edit = [{"old": "b.rows[0].nothing.revenue", "new": "b.rows[0].revenue"}]
    fixed = ok(call(server, "validate_spec", base=failed["candidate_id"], html_edits=edit))
    assert fixed["ok"] and fixed["candidate_id"] != failed["candidate_id"]
    assert ok(call(server, "publish_dashboard", candidate_id=fixed["candidate_id"]))["version"] == 1


def test_a_dashboard_id_as_base_means_its_current_version(server, services):
    spec = readback_spec("by-id")
    services.publish(spec, READBACK_HTML)
    services.publish(spec, READBACK_HTML + "<!-- 2 -->")
    edit = [{"old": "<!-- 2 -->", "new": "<!-- 3 -->"}]
    by_id = ok(call(server, "validate_spec", base="by-id", html_edits=edit))
    by_content = ok(call(server, "validate_spec", spec=spec, html=READBACK_HTML + "<!-- 3 -->"))
    assert by_id["ok"] and by_id["candidate_id"] == by_content["candidate_id"]


def test_an_invalid_base_says_what_a_base_is(server):
    [issue] = issues(call(server, "validate_spec", base=42))
    assert issue["message"] == (
        "base must be a candidate_id from validate_spec, a dashboard id, or {dashboard_id, version}"
    )


# Checking more filter states before publishing

BROWSER = Path(__file__).parent / "fixtures" / "browser"
CHOSEN = {"filters": {"region": ["North America"]}}


def unverified_page(dashboard_id="states"):
    from prumi.spec import load_spec_file

    spec = load_spec_file(BROWSER / "unverified.yaml")
    spec["dashboard"] = {"id": dashboard_id, "title": "States"}
    return spec, (BROWSER / "unverified.html").read_text()


def test_check_states_are_rendered_and_checked(server):
    spec, html = unverified_page()
    # The page only shows its made-up number once a region is chosen.
    assert ok(call(server, "validate_spec", spec=spec, html=html))["ok"]
    report = ok(call(server, "validate_spec", spec=spec, html=html, check_states=[CHOSEN]))
    assert not report["ok"]
    [issue] = report["errors"]
    assert issue["code"] == "R8.unverified_number"
    assert issue["message"].startswith(
        'With the filters {"region": ["North America"]}: A number in p#extra isn\'t a value'
    )
    assert "1,234" not in issue["message"]
    # A state that's fine adds nothing.
    fine = {"filters": {"region": ["Europe"]}}
    edited = html.replace('"Target $1,234.56"', '""')
    assert ok(call(server, "validate_spec", spec=spec, html=edited, check_states=[fine]))["ok"]


def test_check_states_name_only_the_dashboards_filters(server):
    spec, html = unverified_page()
    states = [{"filters": {"nope": 1}}]
    [issue] = ok(call(server, "validate_spec", spec=spec, html=html, check_states=states))["errors"]
    assert (issue["code"], issue["path"]) == ("spec.unknown_filter", "check_states[0].filters.nope")
    too_many = [CHOSEN] * 6
    assert call(server, "validate_spec", spec=spec, html=html, check_states=too_many).is_error


def test_publishing_checks_the_candidates_states_again(server, services, monkeypatch):
    from prumi.services import Services

    spec, html = unverified_page("states-publish")
    html = html.replace('"Target $1,234.56"', '""')
    fine = {"filters": {"region": ["Europe"]}}
    candidate = ok(call(server, "validate_spec", spec=spec, html=html, check_states=[fine]))
    assert candidate["ok"]
    # Edits keep the base's states.
    edit = [{"old": "<title>", "new": "<title>Edited "}]
    edited = ok(call(server, "validate_spec", base=candidate["candidate_id"], html_edits=edit))

    seen = []
    original = Services.validate

    def spy(self, *args, **kwargs):
        seen.append(kwargs.get("check_states", ()))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Services, "validate", spy)
    assert (
        ok(call(server, "publish_dashboard", candidate_id=edited["candidate_id"]))["version"] == 1
    )
    assert seen == [[fine["filters"]]]


# No company data reaches the agent


def test_browser_reports_reach_the_agent_without_data(server, services):
    from prumi.registry import CheckReport

    spec, html = unverified_page("reports")
    html = html.replace('"Target $1,234.56"', '""')
    assert services.publish(spec, html)[1] is not None
    flagged = [
        {"text": "$1,234.56", "path": "p#extra", "code": "R8.unverified_number"},
        {"text": "$7.5", "path": "p#extra", "code": "R8.unverified_number"},
    ]
    report = CheckReport(
        filters={"region": ["North America"]},
        flagged=flagged,
        canvas=False,
        errors=["Boom: North America"],
    )
    services.registry.record_check("reports", 1, report)
    [found] = ok(call(server, "get_dashboard", id="reports"))["checks"]
    assert found["filters"] == {"region": {"selected": 1}}  # how many values, not which
    assert found["flagged"] == [{"code": "R8.unverified_number", "path": "p#extra", "count": 2}]
    assert found["errors"] == 1
    text = json.dumps(found)
    assert "North" not in text and "1,234" not in text and "7.5" not in text


def test_a_time_filter_value_that_isnt_a_window_reaches_the_agent_as_invalid(server, services):
    from prumi.registry import CheckReport

    spec = readback_spec("windows")
    spec["filters"] = [{"id": "period", "type": "time", "default": {"last": 4, "grain": "week"}}]
    spec["data"] = [{"id": "kpi", "metrics": ["revenue"], "filters": ["period"]}]
    services.registry.save(DashboardSpec.model_validate(spec), READBACK_HTML)
    for period in ("Sentinelia", {"last": 8, "grain": "week"}):
        report = CheckReport(filters={"period": period}, flagged=[], canvas=True, errors=[])
        services.registry.record_check("windows", 1, report)
    checks = ok(call(server, "get_dashboard", id="windows", include_html=False))["checks"]
    filters = [c["filters"] for c in checks]  # reported in the same second: any order
    assert len(filters) == 2 and {"period": {"invalid": True}} in filters
    assert {"period": {"last": 8, "grain": "week"}} in filters  # a window, as sent
    assert "Sentinelia" not in json.dumps(checks)


def test_a_failing_source_sends_no_database_text(server, services, monkeypatch):
    secret = "Binder Error: column 'North America' not found near 2742409.35"

    def fail(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(services.engine, "run", fail)
    [issue] = issues(
        call(server, "query", metrics=["revenue"], window={"last": 1, "grain": "week"})
    )
    assert issue["code"] == "query.failed" and "RuntimeError" in issue["message"]
    assert "North" not in json.dumps(issue) and "2742409" not in json.dumps(issue)

    monkeypatch.setattr(services.engine, "run_block", fail)
    report = ok(call(server, "validate_spec", spec=readback_spec("fails"), html=READBACK_HTML))
    [issue] = report["errors"]
    assert issue["code"] == "query.failed" and "RuntimeError" in issue["message"]
    assert "North" not in json.dumps(report) and "2742409" not in json.dumps(report)


# The second render in another browser language


@pytest.mark.browser
def test_validate_spec_over_mcp_always_renders_twice(demo_services, monkeypatch):
    """The suite's services skip the second render to stay fast; the real ones, behind the
    MCP tools, never do (there's no argument or setting for it)."""
    from prumi.mcp import create_mcp
    from prumi.services import Services
    from prumi.validate.page import PageChecker

    renders = []
    check = PageChecker._check

    def spy(self, *args):
        renders.append(2 if args[-1] else 1)
        return check(self, *args)

    monkeypatch.setattr(PageChecker, "_check", spy)
    spec = {
        "spec_version": 0,
        "dashboard": {"id": "kpi", "title": "Revenue"},
        "data": [{"id": "kpi", "metrics": ["revenue"], "window": {"last": 4, "grain": "week"},
                  "filters": []}],
        "presentation": {"bindings": {"#kpi": "kpi"}},
    }  # fmt: skip
    html = (
        '<p id="kpi"></p><script>prumi.on("kpi", (b) => { document.getElementById("kpi")'
        ".textContent = b.rows[0].revenue.toLocaleString(); });</script>"
    )
    demo_services.validate(spec, html)
    demo_services.validate(spec, html, second_render=True)
    assert renders == [1, 2]

    real = Services(demo_services.config)
    real.source = demo_services.source

    async def validate_over_mcp():
        async with Client(create_mcp(real)) as client:
            return await client.call_tool("validate_spec", {"spec": spec, "html": html})

    result = asyncio.run(validate_over_mcp())
    assert renders == [1, 2, 2]
    codes = [i["code"] for i in result.structured_content["errors"]]
    assert codes == ["R8.browser_locale"]
