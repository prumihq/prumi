"""Browser tests: the shell, the sandboxed frame and the runtime (AC 22, 23, 24).

Headless Chromium through Playwright. Skipped when Chromium isn't installed
(`uv run playwright install chromium`); with PRUMI_REQUIRE_BROWSER=1 (CI) that's a failure.
"""

import os
import re
import time
from pathlib import Path

import pytest
from prumi.spec import load_spec_file

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures"
TIMEOUT = 10_000  # ms


@pytest.fixture(scope="module")
def services(live_services):
    services = live_services()
    for spec, html in (
        (FIXTURES / "demo" / "sales.yaml", FIXTURES / "demo" / "sales.html"),
        (FIXTURES / "browser" / "probe.yaml", FIXTURES / "browser" / "probe.html"),
        (FIXTURES / "browser" / "unverified.yaml", FIXTURES / "browser" / "unverified.html"),
    ):
        report, published = services.publish(load_spec_file(spec), html.read_text())
        assert report.ok, report.issues
    return services


@pytest.fixture(scope="module")
def server(services, live_server):
    with live_server(services) as live:
        yield live


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:  # pragma: no cover
        pytest.skip("playwright isn't installed")
    with sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Error as exc:
            if os.environ.get("PRUMI_REQUIRE_BROWSER"):
                raise
            pytest.skip(
                "Headless Chromium can't start; run `uv run playwright install --with-deps "
                f"chromium`. ({str(exc).splitlines()[0]})"
            )
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(locale="en-US")
    page = context.new_page()
    page.set_default_timeout(TIMEOUT)
    yield page
    context.close()


def open_dashboard(page, server, dashboard="probe"):
    page.goto(f"{server.url}/d/{dashboard}")
    frame = page.frame_locator("#prumi-frame")
    return frame, frame_of(page)


def frame_of(page):
    deadline = time.monotonic() + TIMEOUT / 1000
    while time.monotonic() < deadline:
        for frame in page.frames:
            if frame.url.endswith("/frame"):
                return frame
        time.sleep(0.02)
    raise AssertionError("the dashboard frame didn't load")


def wait_for_calls(frame, block, count=1):
    frame.wait_for_function(
        f"() => (window.__calls || {{}})[{block!r}] >= {count}", timeout=TIMEOUT
    )


def block_requests(page):
    """Block ids of the block API calls the page makes from now on."""
    seen = []

    def on_request(request):
        match = re.search(r"/blocks/([^/?]+)$", request.url)
        if match and request.method == "POST":
            seen.append(match.group(1))

    page.on("request", on_request)
    return seen


# AC 22: the shell and the sandbox


def test_the_badge_and_freshness_come_from_the_server(page, server, services):
    page.goto(f"{server.url}/d/sales")
    badge = page.locator("#prumi-badge")
    assert badge.text_content() == "Draft · not certified"
    page.wait_for_function("() => document.getElementById('prumi-freshness').textContent !== ''")
    assert page.locator("#prumi-freshness").text_content() == "Data through 2026-10-14"

    services.registry.certify("sales", 1, by="ana")
    page.reload()
    assert re.fullmatch(r"Certified by ana on \d{4}-\d{2}-\d{2}", badge.text_content())
    services.registry.uncertify("sales")


def test_the_frame_is_sandboxed(page, server):
    failed, answered = {}, []
    page.on("requestfailed", lambda request: failed.setdefault(request.url, request.failure))
    page.on("response", lambda response: answered.append(response.url))
    _, frame = open_dashboard(page, server)
    wait_for_calls(frame, "kpi")
    frame.wait_for_function("() => Object.keys(window.__probes).length === 5")
    assert frame.evaluate("window.__probes") == {
        "read_parent": "blocked",
        "write_parent": "blocked",
        "fetch_prumi": "blocked",
        "fetch_elsewhere": "blocked",
        "image_elsewhere": "blocked",
    }
    assert page.locator("#prumi-badge").text_content() == "Draft · not certified"
    # Chromium stopped them under the frame's CSP; nothing reached a server.
    probes = ("pixel.png", "example.com", "/status")
    assert not [url for url in answered if any(p in url for p in probes)]
    assert {failure for url, failure in failed.items() if any(p in url for p in probes)} <= {"csp"}


def test_the_frame_is_sandboxed_when_opened_directly(page, server):
    response = page.goto(f"{server.url}/d/probe/v/1/frame")
    assert "sandbox allow-scripts" in response.headers["content-security-policy"]
    # No parent shell: the runtime shows its notice and calls nothing.
    page.wait_for_selector("#prumi-notice")
    assert page.evaluate("window.__calls") == {}


# AC 23: the runtime


def test_on_delivers_block_results_on_load(page, server):
    frame_locator, frame = open_dashboard(page, server)
    for block in ("kpi", "by_region", "trend", "formats"):
        wait_for_calls(frame, block)
    assert frame.evaluate("window.__calls") == {"kpi": 1, "by_region": 1, "trend": 1, "formats": 1}
    kpi = frame.evaluate("window.__results.kpi")
    assert kpi["window"] == {
        "start": "2026-07-20",
        "end": "2026-10-11",
        "grain": "week",
        "partial": False,
    }
    assert re.fullmatch(r"\$[\d,]+\.\d\d", frame_locator.locator("#kpi").text_content())
    by_region = frame.evaluate("window.__results.by_region")
    assert len(by_region["rows"]) == 8  # seven regions and NULL
    assert frame.evaluate("window.__errors") == []
    # The second <script src="/runtime.js"> was ignored: nothing was called twice.
    assert frame.evaluate("window.__filters.length") == 1


def test_set_filter_refetches_only_the_blocks_that_list_it(page, server):
    _, frame = open_dashboard(page, server)
    for block in ("kpi", "by_region", "trend", "formats"):
        wait_for_calls(frame, block)
    requests = block_requests(page)
    before = frame.evaluate("window.__results.kpi.rows[0].revenue")

    frame.evaluate("prumi.setFilter('region', ['Asia Pacific'])")
    wait_for_calls(frame, "kpi", 2)
    wait_for_calls(frame, "trend", 2)
    page.wait_for_timeout(300)  # give any other request time to show up
    assert sorted(requests) == ["kpi", "trend"]
    assert frame.evaluate("prumi.getFilter('region')") == ["Asia Pacific"]
    assert frame.evaluate("window.__results.kpi.rows[0].revenue") < before
    assert frame.evaluate("window.__calls") == {"kpi": 2, "by_region": 1, "trend": 2, "formats": 1}

    frame.evaluate("prumi.setFilter('period', {last: 4, grain: 'week'})")
    wait_for_calls(frame, "formats", 2)
    assert frame.evaluate("window.__results.kpi.window.start") == "2026-09-14"


def test_click_applies_the_interaction_and_a_second_click_clears_it(page, server):
    _, frame = open_dashboard(page, server)
    wait_for_calls(frame, "kpi")
    frame.evaluate("prumi.click('by_region', {customer__region: 'Asia Pacific', revenue: 1})")
    wait_for_calls(frame, "kpi", 2)
    assert frame.evaluate("prumi.getFilter('region')") == ["Asia Pacific"]
    frame.evaluate("prumi.click('by_region', {customer__region: 'Asia Pacific', revenue: 1})")
    wait_for_calls(frame, "kpi", 3)
    assert frame.evaluate("prumi.getFilter('region')") == []
    # A block without interactions changes nothing.
    frame.evaluate("prumi.click('kpi', {revenue: 1})")
    page.wait_for_timeout(300)
    assert frame.evaluate("window.__calls.kpi") == 3


def test_click_on_a_null_value(page, server):
    _, frame = open_dashboard(page, server)
    wait_for_calls(frame, "kpi")
    frame.evaluate("prumi.click('by_region', {customer__region: null})")
    wait_for_calls(frame, "kpi", 2)
    assert frame.evaluate("prumi.getFilter('region')") == [None]
    assert frame.evaluate("window.__results.kpi.rows[0].revenue") > 0  # NULL-region customers


def test_filter_values(page, server):
    _, frame = open_dashboard(page, server)
    wait_for_calls(frame, "kpi")
    values = frame.evaluate("prumi.filterValues('region')")
    assert values[-1] is None and "Asia Pacific" in values and len(values) == 8
    error = frame.evaluate("prumi.filterValues('period').then(() => 'resolved', (e) => 'rejected')")
    assert error == "rejected"


def test_format_renders_each_format_type(page, server):
    _, frame = open_dashboard(page, server)
    wait_for_calls(frame, "formats")
    formatted = frame.evaluate(
        """() => ({
            currency: prumi.format(1234.5, "revenue"),
            integer: prumi.format(1234.6, "orders"),
            percent: prumi.format(0.2512, "gross_margin_pct"),
            number: prumi.format(1234.5678, "discount_rate"),
            unknown: prumi.format(2.5, "not_a_metric"),
            missing: prumi.format(null, "revenue"),
            text: prumi.format("n/a", "revenue"),
            label: prumi.label("customer__region"),
            label_unknown: prumi.label("nope"),
        })"""
    )
    assert formatted == {
        "currency": "$1,234.50",
        "integer": "1,235",
        "percent": "25.1%",
        "number": "1,234.57",
        "unknown": "2.5",
        "missing": "–",
        "text": "n/a",
        "label": "Region",
        "label_unknown": "nope",
    }


def test_format_takes_a_metric_name_or_a_format_object(page, server):
    """AC 35: both forms give the same string; anything else warns once in the console."""
    warnings = []
    page.on("console", lambda m: m.type == "warning" and warnings.append(m.text))
    _, frame = open_dashboard(page, server)
    wait_for_calls(frame, "formats")
    formatted = frame.evaluate(
        """() => {
            const column = window.__results.formats.columns.find((c) => c.name === "revenue");
            return {
                byName: prumi.format(1234.5, "revenue"),
                byObject: prumi.format(1234.5, column.format),
                percent: prumi.format(0.25, {type: "percent", unit: null, decimals: 0}),
                nope: prumi.format(1, "nope"),
                nopeAgain: prumi.format(2, "nope"),
                missing: prumi.format(3),
                badObject: prumi.format(4, {type: "money"}),
            };
        }"""
    )
    assert formatted == {
        "byName": "$1,234.50",
        "byObject": "$1,234.50",
        "percent": "25%",
        "nope": "1",
        "nopeAgain": "2",
        "missing": "3",
        "badObject": "4",
    }
    page.wait_for_timeout(100)
    unknown = [w for w in warnings if w.startswith("prumi.format: unknown metric")]
    assert len(unknown) == 3  # "nope" once, the missing argument once, the bad object once
    assert unknown[0].startswith("prumi.format: unknown metric nope;")


def test_a_bad_filter_value_reaches_on_error(page, server):
    _, frame = open_dashboard(page, server)
    wait_for_calls(frame, "kpi")
    frame.evaluate("prumi.setFilter('region', ['{{ 7 * 7 }}'])")
    frame.wait_for_function("() => window.__errors.length >= 2")
    assert sorted(frame.evaluate("window.__errors")) == [
        ["kpi", "filter.invalid_value"],
        ["trend", "filter.invalid_value"],
    ]


# AC 24: outside Prumi


def test_the_frame_html_as_a_standalone_file(page, server, tmp_path):
    import urllib.request

    with urllib.request.urlopen(f"{server.url}/d/probe/v/1/frame") as response:
        html = response.read().decode("utf-8")
    copy = tmp_path / "dashboard.html"
    copy.write_text(html)
    page.goto(copy.as_uri())
    page.wait_for_selector("#prumi-notice")
    assert page.locator("#prumi-notice").text_content() == "This dashboard only works inside Prumi"
    assert page.evaluate("window.__calls") == {}
    assert page.locator("#kpi").text_content() == ""


def test_the_authoring_guide_example_renders(page, server, services):
    """Agents copy the guide's example, so it must work in a real browser."""
    import json

    from prumi.mcp import AUTHORING_GUIDE

    guide = AUTHORING_GUIDE.read_text(encoding="utf-8")

    def fenced(heading, language):
        section = guide.split(heading, 1)[1]
        return re.search(rf"```{language}\n(.*?)```", section, re.S).group(1)

    report, published = services.publish(
        json.loads(fenced("### Example spec", "json")), fenced("### Example HTML", "html")
    )
    assert report.ok, report.issues
    console = []
    page.on("console", lambda m: m.type in ("warning", "error") and console.append(m.text))
    page.goto(f"{server.url}/d/{published.dashboard_id}")
    frame = page.frame_locator("#prumi-frame")
    frame.locator("#regions button").first.wait_for()
    frame.locator("#trend li").first.wait_for()
    assert re.fullmatch(r"\$[\d,]+\.\d\d", frame.locator("#revenue").text_content())
    assert re.fullmatch(r"[+-]?[\d.]+% vs last year", frame.locator("#change").text_content())
    assert frame.locator("#regions button").count() == 5
    assert frame.locator("#others").text_content().startswith("Others: $")
    frame.locator("#regions button").first.click()
    page.wait_for_timeout(500)
    assert not console, console


# The in-browser number check


def wait_for_checks(services, dashboard, count):
    deadline = time.monotonic() + TIMEOUT / 1000
    while time.monotonic() < deadline:
        checks = services.registry.checks(dashboard, 1)
        if len(checks) >= count:
            return checks
        time.sleep(0.05)
    raise AssertionError(f"expected {count} check reports, got {checks}")


def test_the_browser_reports_numbers_prumi_didnt_deliver(page, server, services, capsys):
    from prumi.cli import main

    frame, _ = open_dashboard(page, server, "unverified")
    frame.locator("#revenue").filter(has_text="$").wait_for()
    [default] = wait_for_checks(services, "unverified", 1)
    assert default.filters == {"region": []} and default.clean
    frame.locator("body").evaluate("() => prumi.setFilter('region', ['North America'])")
    frame.locator("#extra").filter(has_text="1,234.56").wait_for()
    checks = wait_for_checks(services, "unverified", 2)
    [chosen] = [c for c in checks if c.filters == {"region": ["North America"]}]
    assert chosen.flagged == [{"text": "$1,234.56", "path": "p#extra"}]
    # Viewers see nothing of it, and the version can't be certified.
    assert page.locator("#prumi-bar").text_content().count("1,234.56") == 0
    config = str(services.config.path)
    assert main(["certify", "unverified", "1", "--config", config]) == 1
    out = capsys.readouterr().out
    assert '{"region": ["North America"]}' in out and "$1,234.56 in p#extra" in out
