"""Browser tests: the shell, the sandboxed frame and the runtime (AC 22, 23, 24).

Headless Chromium through Playwright. Skipped when Chromium isn't installed
(`uv run playwright install chromium`); with PRUMI_REQUIRE_BROWSER=1 (CI) that's a failure.
"""

import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from prumi.config import load_config
from prumi.server.app import create_app
from prumi.services import Services
from prumi.spec import load_spec_file

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures"
TIMEOUT = 10_000  # ms


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    def __init__(self, services: Services) -> None:
        port = services.config.server.port
        self.url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(
            create_app(services), host="127.0.0.1", port=port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the test server didn't start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture(scope="module")
def services(tmp_path_factory, demo_project, demo_source):
    directory = tmp_path_factory.mktemp("browser")
    config = directory / "prumi.toml"
    config.write_text(
        f"""
[server]
port = {free_port()}
data_dir = "data"
as_of = "2026-10-15"

[source]
adapter = "dbt"

[source.dbt]
semantic_manifest = "{demo_project.manifest_path.as_posix()}"

[source.dbt.connection]
type = "duckdb"
path = "{demo_project.db_path.as_posix()}"
"""
    )
    services = Services(load_config(config))
    services.source = demo_source
    for spec, html in (
        (FIXTURES / "demo" / "sales.yaml", FIXTURES / "demo" / "sales.html"),
        (FIXTURES / "browser" / "probe.yaml", FIXTURES / "browser" / "probe.html"),
    ):
        report, published = services.publish(load_spec_file(spec), html.read_text())
        assert report.ok, report.issues
    return services


@pytest.fixture(scope="module")
def server(services):
    with LiveServer(services) as live:
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
