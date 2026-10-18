"""Number provenance: every number a page shows must be a value Prumi delivered.

Each fixture page runs in headless Chromium with its blocks' results for the filters'
default values, and the matcher reads what it shows. The expected numbers it flags are in
fixtures/provenance/expected.json; an empty list means the page passes.
"""

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from prumi.adapters import LOCAL_VIEWER
from prumi.mcp import AUTHORING_GUIDE
from prumi.server.pages import frame_csp, frame_html, shell_config
from prumi.spec import DashboardSpec
from prumi.validate.html import DEFAULT_HOSTS

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures" / "provenance"
RUNTIME = Path(__file__).parents[2] / "prumi-runtime" / "src"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
ORIGIN = "http://prumi.test"

# Stands in for the shell: answers the frame's handshake with the results, nothing else.
HOST = """<!doctype html><html><body>
<iframe id="frame" src="/frame" sandbox="allow-scripts" style="width:1280px;height:900px"></iframe>
<script>
  var setup = JSON.parse(document.getElementById("setup").textContent);
  var frame = document.getElementById("frame");
  function post(type, payload) {
    var message = Object.assign({ prumi: 0, type: type }, payload);
    frame.contentWindow.postMessage(message, "*");
  }
  var ready = false;
  window.addEventListener("message", function (event) {
    var m = event.data;
    if (!m || m.prumi !== 0) return;
    if (m.type === "ready" && !ready) {
      ready = true;
      post("init", { filters: setup.filters, values: setup.values, labels: setup.labels,
                     formats: setup.formats });
      (m.blocks || []).forEach(function (id) {
        if (setup.results[id]) post("data", { block: id, result: setup.results[id] });
      });
    } else if (m.type === "filterValues") {
      post("filterValues", { request: m.request, values: setup.values_of[m.id] || [] });
    }
  });
</script>
</body></html>"""

SETTLE = """() => new Promise((resolve) => {
  let timer;
  const done = () => { observer.disconnect(); resolve(); };
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(done, 300);
  });
  const all = { subtree: true, childList: true, characterData: true, attributes: true };
  observer.observe(document, all);
  timer = setTimeout(done, 300);
  setTimeout(done, 10000);
})"""


def fenced(heading: str, language: str) -> str:
    section = AUTHORING_GUIDE.read_text(encoding="utf-8").split(heading, 1)[1]
    return re.search(rf"```{language}\n(.*?)```", section, re.S).group(1)


def fixture(name: str) -> tuple[dict, str]:
    if name == "guide_example":
        return json.loads(fenced("### Example spec", "json")), fenced("### Example HTML", "html")
    return json.loads((FIXTURES / f"{name}.json").read_text()), (
        FIXTURES / f"{name}.html"
    ).read_text()


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


def shown_numbers(browser, demo_services, spec_data, html):
    """What the matcher flags on the page, rendered with the default filters' results."""
    report = demo_services.validate(spec_data, html)
    assert report.ok, report.errors
    spec = DashboardSpec.model_validate(spec_data)
    engine, source = demo_services.engine, demo_services.source
    results = {b.id: engine.run_block(spec, b.id, {}, LOCAL_VIEWER).to_json() for b in spec.data}
    config = shell_config(SimpleNamespace(spec=spec, version=0), source)
    setup = {
        "filters": config["filters"],
        "values": {f["id"]: f["default"] for f in config["filters"]},
        "labels": config["labels"],
        "formats": config["formats"],
        "results": results,
        "values_of": {},
    }
    host = HOST.replace(
        "<body>",
        '<body><script type="application/json" id="setup">'
        + json.dumps(setup).replace("<", "\\u003c")
        + "</script>",
        1,
    )
    routes = {
        "/": ("text/html", host, {}),
        "/frame": (
            "text/html",
            frame_html(html, ORIGIN),
            {"Content-Security-Policy": frame_csp(ORIGIN, DEFAULT_HOSTS)},
        ),
        "/runtime.js": ("text/javascript", (RUNTIME / "runtime.js").read_text(), {}),
    }

    def route(r):
        path = "/" + r.request.url.removeprefix(ORIGIN).lstrip("/").split("?")[0]
        if path not in routes:
            return r.abort()
        kind, body, headers = routes[path]
        r.fulfill(status=200, content_type=kind, body=body, headers=headers)

    context = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
    try:
        context.route("**/*", route)
        page = context.new_page()
        page.goto(ORIGIN + "/")
        frame = page.locator("#frame").element_handle().content_frame()
        frame.wait_for_load_state()
        frame.evaluate(SETTLE)
        frame.evaluate((RUNTIME / "provenance.js").read_text())
        allowed = {"spec": spec_data, "labels": config["labels"]}
        return frame.evaluate(
            """([results, extra]) => {
                const p = window.prumiProvenance;
                const delivered = p.fromResults(results);
                const allowed = p.fromData(extra, delivered.allowed);
                return p.scan(document, { values: delivered.values, allowed });
            }""",
            [results, allowed],
        )
    finally:
        context.close()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_provenance_fixtures(name, browser, demo_services):
    report = shown_numbers(browser, demo_services, *fixture(name))
    assert report["checked"] >= 3, "the page didn't render its numbers"
    assert sorted(f["text"] for f in report["flagged"]) == sorted(EXPECTED[name]), report


@pytest.mark.parametrize(
    ("text", "readings"),
    [
        ("$2,742,409", [2742409]),
        ("1,234", [1234, 1.234]),
        ("1.234,56 €", [1234.56]),
        ("12,5 %", [12.5]),
        ("2.7M", [2.7]),
    ],
)
def test_numbers_are_read_in_either_locale(browser, text, readings):
    page = browser.new_page()
    try:
        page.add_script_tag(content=(RUNTIME / "provenance.js").read_text())
        found = page.evaluate(
            """(text) => window.prumiProvenance.numbers(text).flatMap(
                (n) => window.prumiProvenance.readings(n.raw).map((r) => r.value))""",
            text,
        )
    finally:
        page.close()
    assert found == pytest.approx(readings)
