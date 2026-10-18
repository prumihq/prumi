"""End to end: an agent session over MCP, a browser on the URL, certification."""

import asyncio
import concurrent.futures
import json
import os
import re

import duckdb
import pytest
from mcp import Client
from prumi.cli import main

pytestmark = pytest.mark.browser

WINDOW = "order_date BETWEEN '2026-07-20' AND '2026-10-11'"  # {last: 12, grain: week}

SPEC = {
    "spec_version": 0,
    "dashboard": {"id": "regional-sales", "title": "Regional sales"},
    "filters": [{"id": "period", "type": "time", "default": {"last": 12, "grain": "week"}}],
    "data": [
        {"id": "kpi", "metrics": ["revenue", "average_order_value"], "filters": ["period"]},
        {
            "id": "regions",
            "metrics": ["revenue"],
            "dimensions": ["customer__region"],
            "filters": ["period"],
            "sort": {"by": "revenue", "descending": True},
        },
    ],
    "presentation": {"bindings": {"#kpi": "kpi", "#regions": "regions"}},
}

# What an agent writes: every number comes from prumi.on, formatted with prumi.format.
HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Regional sales</title></head>
<body>
  <h1>Regional sales</h1>
  <p id="kpi">Revenue <b id="revenue"></b>, average order <b id="aov"></b></p>
  <ul id="regions"></ul>
  <script>
    prumi.on("kpi", (result) => {
      const row = result.rows[0];
      for (const [id, metric] of [["revenue", "revenue"], ["aov", "average_order_value"]]) {
        const el = document.getElementById(id);
        el.dataset.value = row[metric];
        el.textContent = prumi.format(row[metric], metric);
      }
    });
    prumi.on("regions", (result) => {
      document.getElementById("regions").replaceChildren(...result.rows.map((row) => {
        const item = document.createElement("li");
        item.dataset.region = row.customer__region ?? "";
        item.dataset.value = row.revenue;
        item.textContent = `${prumi.label("customer__region")} ${row.customer__region}: ` +
          prumi.format(row.revenue, "revenue");
        return item;
      }));
    });
  </script>
</body>
</html>
"""


@pytest.fixture(scope="module")
def services(live_services):
    return live_services()


@pytest.fixture(scope="module")
def server(services, live_server):
    with live_server(services) as live:
        yield live


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import Error, sync_playwright

    with sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Error:
            if os.environ.get("PRUMI_REQUIRE_BROWSER"):
                raise
            pytest.skip("Headless Chromium can't start; run `uv run playwright install chromium`.")
        yield chromium
        chromium.close()


def in_thread(coroutine_function):
    """Run an async MCP session beside Playwright, whose sync API holds this thread's loop."""
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coroutine_function()).result()


def agent_session(server):
    """The agent's side: find, explore, validate (an error, then fixed), publish."""

    async def run():
        async with Client(f"{server.url}/mcp") as mcp:

            async def tool(tool_name, /, **arguments):
                result = await mcp.call_tool(tool_name, arguments)
                assert not result.is_error, result.content
                return result.structured_content

            found = await tool("find_metrics", question="revenue by region")
            assert found["metrics"][0]["name"] == "revenue"
            regions = await tool("describe_metric", name="revenue")
            assert "customer__region" in [d["name"] for d in regions["dimensions"]]
            explored = await tool(
                "query",
                metrics=["revenue"],
                dimensions=["customer__region"],
                window={"last": 12, "grain": "week"},
                total=True,
            )
            assert len(explored["rows"]) == 8 and explored["total"]["revenue"] > 0

            draft = json.loads(json.dumps(SPEC))
            draft["data"][0]["metrics"] = ["revenue", "avg_order_value"]
            report = await tool("validate_spec", spec=draft, html=HTML)
            assert not report["ok"]
            [error] = report["errors"]
            assert error["code"] == "R7.unknown_metric"
            assert "average_order_value" in error["message"]

            fixed = await tool("validate_spec", spec=SPEC, html=HTML)
            assert fixed["ok"] and not fixed["errors"] and not fixed["warnings"]
            # Publish what was validated, without sending the HTML again.
            return await tool("publish_dashboard", candidate_id=fixed["candidate_id"])

    return in_thread(run)


def publish_edit(server):
    async def run():
        async with Client(f"{server.url}/mcp") as mcp:
            edited = HTML.replace("<h1>Regional sales</h1>", "<h1>Sales by region</h1>")
            result = await mcp.call_tool("publish_dashboard", {"spec": SPEC, "html": edited})
            assert not result.is_error, result.content
            return result.structured_content

    return in_thread(run)


def hand(demo_project, sql):
    with duckdb.connect(str(demo_project.db_path), read_only=True) as conn:
        return conn.execute(sql).fetchall()


def test_agent_to_certified_dashboard(server, services, browser, demo_project, capsys):
    published = agent_session(server)
    port = services.config.server.port
    assert published == {
        "id": "regional-sales",
        "version": 1,
        "url": f"http://127.0.0.1:{port}/d/regional-sales",
        "status": "draft",
        "warnings": [],
        "note": "This link only works on this machine.",
    }

    page = browser.new_context(locale="en-US").new_page()
    page.set_default_timeout(10_000)
    page.goto(published["url"])
    frame = page.frame_locator("#prumi-frame")
    frame.locator("#revenue[data-value]").wait_for()
    frame.locator("#regions li").first.wait_for()

    # The bound numbers equal hand-written SQL over the warehouse.
    [(revenue, aov)] = hand(
        demo_project,
        f"""SELECT r, r / o FROM
              (SELECT sum(revenue)::double AS r FROM main.order_items WHERE {WINDOW}),
              (SELECT count(*) AS o FROM main.orders WHERE {WINDOW})""",
    )
    assert float(frame.locator("#revenue").get_attribute("data-value")) == pytest.approx(revenue)
    assert float(frame.locator("#aov").get_attribute("data-value")) == pytest.approx(aov)
    assert re.fullmatch(r"\$[\d,]+\.\d\d", frame.locator("#revenue").text_content())
    by_region = hand(
        demo_project,
        f"""SELECT coalesce(c.region, ''), sum(i.revenue)::double FROM main.order_items i
            LEFT JOIN main.customers c USING (customer_id) WHERE {WINDOW}
            GROUP BY 1 ORDER BY 2 DESC""",
    )
    items = frame.locator("#regions li")
    shown = [
        (items.nth(i).get_attribute("data-region"), float(items.nth(i).get_attribute("data-value")))
        for i in range(items.count())
    ]
    assert [r for r, _ in shown] == [r for r, _ in by_region]
    assert [v for _, v in shown] == pytest.approx([v for _, v in by_region])

    badge = page.locator("#prumi-badge")
    assert badge.text_content() == "Draft · not certified"
    page.wait_for_function("() => document.getElementById('prumi-freshness').textContent")
    assert page.locator("#prumi-freshness").text_content() == "Data through 2026-10-14"

    # The data team certifies it from the CLI.
    config = str(services.config.path)
    assert main(["certify", "regional-sales", "1", "--by", "data-team", "--config", config]) == 0
    assert "Certified regional-sales version 1 by data-team." in capsys.readouterr().out
    page.reload()
    assert re.fullmatch(r"Certified by data-team on \d{4}-\d{2}-\d{2}", badge.text_content())

    # An HTML edit is a new version, and a draft again.
    edited = publish_edit(server)
    assert (edited["version"], edited["status"]) == (2, "draft")
    page.reload()
    assert badge.text_content() == "Draft · not certified"
    assert page.frame_locator("#prumi-frame").locator("h1").text_content() == "Sales by region"
