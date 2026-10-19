"""Number provenance: every number a page shows must be a value Prumi delivered.

The server check renders each page in headless Chromium with its blocks' results for the
filters' default values, and reads what it shows. What each fixture in fixtures/provenance/
must get flagged ([code, element path], "line N" for copied numbers, null for page-wide codes)
is in expected.json; an empty
list means it passes.
"""

import json
import os
import re
from pathlib import Path

import pytest
from prumi.cli import main
from prumi.mcp import AUTHORING_GUIDE
from prumi.spec import DashboardSpec
from prumi.validate import page as page_check

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures" / "provenance"
RUNTIME = Path(__file__).parents[2] / "prumi-runtime" / "src"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
# Where an R8 issue's numbers are: an element path, never the number itself.
WHERE = re.compile(r"(?:On hover, )?(?:[Aa] number|\d+ numbers) in (.+?) (?:is|are|isn't|aren't) ")
LINE = re.compile(r"on line (\d+)")
FLAGGED = {"html.image", "html.iframe", "html.closed_shadow_root", "html.canvas"}

KPI_SPEC = {
    "spec_version": 0,
    "dashboard": {"id": "kpi", "title": "Revenue"},
    "data": [{"id": "kpi", "metrics": ["revenue"], "window": {"last": 12, "grain": "week"},
              "filters": []}],
    "presentation": {"bindings": {"#page": "kpi"}},
}  # fmt: skip

SHOW_REVENUE = """prumi.on("kpi", (b) => {
  document.getElementById("revenue").textContent = prumi.format(b.rows[0].revenue, "revenue");
});"""


def fenced(heading: str, language: str) -> str:
    section = AUTHORING_GUIDE.read_text(encoding="utf-8").split(heading, 1)[1]
    return re.search(rf"```{language}\n(.*?)```", section, re.S).group(1)


def fixture(name: str) -> tuple[dict, str]:
    if name == "guide_example":
        return json.loads(fenced("### Example spec", "json")), fenced("### Example HTML", "html")
    return json.loads((FIXTURES / f"{name}.json").read_text()), (
        FIXTURES / f"{name}.html"
    ).read_text()


def kpi_page(script: str, body: str = '<p id="revenue"></p>') -> str:
    """A page whose body is bound to the kpi block."""
    return (
        f'<!doctype html><html><body><div id="page">{body}</div><script>{script}</script>'
        "</body></html>"
    )


def codes(report) -> list[str]:
    return [i.code for i in report.issues]


@pytest.fixture(autouse=True)
def chromium(demo_services):
    """The shared checker, or a skip when Chromium can't start (a failure in CI)."""
    try:
        demo_services.checker.start()
    except page_check.BrowserStartupError:
        if os.environ.get("PRUMI_REQUIRE_BROWSER"):
            raise
        pytest.skip("Headless Chromium can't start; run `uv run playwright install chromium`.")


def flagged(report) -> list[tuple[str, str | None]]:
    found = []
    for issue in report.issues:
        if issue.code == "R8.copied_number":
            found.append((issue.code, f"line {LINE.search(issue.message)[1]}"))
        elif issue.code.startswith("R8."):
            found.append((issue.code, WHERE.match(issue.message)[1]))
        elif issue.code in FLAGGED:
            found.append((issue.code, None))
    return sorted(found, key=str)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_provenance_fixtures(name, demo_services):
    report = demo_services.validate(*fixture(name))
    expected = sorted(((code, text) for code, text in EXPECTED[name]), key=str)
    assert flagged(report) == expected, report.issues
    if not EXPECTED[name]:
        assert report.ok, report.errors


def test_an_unverified_number_names_its_element(demo_services):
    page = kpi_page(SHOW_REVENUE, '<p id="revenue"></p><div class="note"><b>$1,234.56</b></div>')
    [issue] = demo_services.validate(KPI_SPEC, page).errors
    assert (issue.code, issue.severity, issue.path) == ("R8.unverified_number", "error", "html")
    assert WHERE.match(issue.message)[1] == "div#page > div.note > b"
    assert "1,234" not in issue.message and "1234" not in issue.message  # never the number


def test_at_most_twenty_numbers_are_listed(demo_services):
    made_up = "".join(f"<p>{n},111.5</p>" for n in range(100, 130))
    report = demo_services.validate(
        KPI_SPEC, kpi_page(SHOW_REVENUE, '<p id="revenue"></p>' + made_up)
    )
    assert codes(report) == ["R8.unverified_number"] * 20


@pytest.mark.parametrize(
    ("page", "code"),
    [
        (kpi_page(SHOW_REVENUE, '<p id="revenue"></p><canvas></canvas>'), "html.canvas"),
        (kpi_page("document.getElementById('missing').textContent = 'x';"), "html.runtime_error"),
        (kpi_page('prumi.on("kpi", (b) => { b.rows[0].nothing.value; });'), "html.runtime_error"),
        (kpi_page("setInterval(() => { document.body.dataset.tick = Date.now(); }, 50);"),
         "html.render_timeout"),
    ],
)  # fmt: skip
def test_pages_that_cant_be_checked_are_errors_and_cant_be_published(
    demo_services, monkeypatch, page, code
):
    monkeypatch.setattr(demo_services.checker, "timeout_s", 2.0)
    report = demo_services.validate(KPI_SPEC, page)
    assert code in codes(report), report.issues
    assert next(i for i in report.issues if i.code == code).severity == "error"
    refused, published = demo_services.publish(KPI_SPEC, page)
    assert published is None and code in codes(refused)


def test_page_code_cant_switch_the_check_off(demo_services):
    """The scan runs in an isolated world: overriding prumi or the DOM API changes nothing."""
    tamper = """
      prumi.on("kpi", () => {
        document.getElementById("revenue").textContent = "$9,999,999.99";
        setTimeout(() => {
          window.prumi = { format: () => "", on: () => {} };
          window.prumiProvenance = { scan: () => ({ flagged: [], checked: 0, canvas: false }) };
          Object.defineProperty(Node.prototype, "textContent", { get: () => "" });
          Object.defineProperty(CharacterData.prototype, "data", { get: () => "" });
          Document.prototype.createTreeWalker = () => ({ nextNode: () => null });
          window.MutationObserver = function () { this.observe = () => {}; };
        }, 0);
      });"""
    report = demo_services.validate(KPI_SPEC, kpi_page(tamper))
    assert [WHERE.match(i.message)[1] for i in report.errors] == ["p#revenue"]


def test_a_blocked_request_is_a_warning(demo_services):
    page = kpi_page(SHOW_REVENUE + 'fetch("https://example.com/data.json").catch(() => {});')
    report = demo_services.validate(KPI_SPEC, page)
    assert report.ok
    static, blocked = report.warnings
    assert static.code == "html.network_call"  # found in the source before the page ran
    assert blocked.code == "html.blocked_request"
    # Where it went, not the path or query, which can carry data.
    assert "https://example.com;" in blocked.message and "data.json" not in blocked.message


def test_the_server_check_runs_no_extra_queries(demo_services, monkeypatch):
    runs = []
    original = demo_services.engine.run_block
    monkeypatch.setattr(
        demo_services.engine, "run_block", lambda *a, **k: runs.append(a[1]) or original(*a, **k)
    )
    spec, html = fixture("first_run")
    assert demo_services.validate(spec, html).ok
    assert sorted(runs) == sorted(b["id"] for b in spec["data"])


def test_one_chromium_per_process(demo_services, make_demo_config, tmp_path):
    from prumi.config import load_config
    from prumi.services import Services

    other = Services(load_config(make_demo_config(tmp_path)))
    assert other.checker is demo_services.checker


def test_serve_without_chromium_fails_with_the_install_command(
    make_demo_config, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browsers"))
    monkeypatch.setattr(page_check, "_shared", None)
    try:
        assert main(["serve", "--config", str(make_demo_config(tmp_path))]) == 1
    finally:
        if page_check._shared is not None:
            page_check._shared.close()
    assert "uv run playwright install chromium" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("text", "locale", "readings"),
    [
        ("$2,742,409", "en-US", [2742409]),
        ("1,234", "en-US", [1234]),
        ("1.234", "en-US", [1.234]),
        ("2.7M", "en-US", [2.7]),
        # The other convention doesn't read in en-US, so it can't match anything.
        ("1.234,56 €", "en-US", []),
        ("12,5 %", "en-US", []),
        ("1,2345", "en-US", []),
        ("1.234", "pt-BR", [1234]),
        ("1.234,56 €", "de-DE", [1234.56]),
        ("12,5 %", "de-DE", [12.5]),
        ("1,234.56", "de-DE", []),
        ("12,34,567.5", "en-IN", [1234567.5]),
        ("1,234,567.5", "en-IN", []),
    ],
)
def test_numbers_are_read_strictly_in_one_locale(demo_services, text, locale, readings):
    def evaluate(browser):
        page = browser.new_page()
        try:
            page.add_script_tag(content=(RUNTIME / "provenance.js").read_text())
            return page.evaluate(
                """([text, locale]) => window.prumiProvenance.numbers(text, locale).flatMap(
                    (n) => window.prumiProvenance.readings(n.raw, locale).map((r) => r.value))""",
                [text, locale],
            )
        finally:
            page.close()

    assert demo_services.checker._submit(evaluate) == pytest.approx(readings)


@pytest.mark.parametrize(
    ("text", "locale", "values"),
    [
        ("US$\u00a047\u00a0mil", "pt-BR", [47e3]),
        ("US$\u00a02.742.409,35", "pt-BR", [2742409.35]),
        ("2,7\u00a0mi", "pt-BR", [2.7e6]),
        ("3\u00a0bi", "pt-BR", [3e9]),
        ("1,2\u00a0Mio.\u00a0€", "de-DE", [1.2e6]),
        ("1'234'567.5", "de-CH", [1234567.5]),
        ("1\u00a0Md", "fr-FR", [1e9]),
        ("100万", "ja-JP", [1e6]),  # 万 is 1e4
        ("10L · ₹100Cr", "en-IN", [1e6, 1e9]),  # lakh and crore
        # en-US reads as before: "mil" isn't a scale there.
        ("$47K", "en-US", [47e3]),
        ("47 mil", "en-US", [47]),
    ],
)
def test_numbers_are_read_in_the_servers_locale(demo_services, text, locale, values):
    def evaluate(browser):
        page = browser.new_page()
        try:
            page.add_script_tag(content=(RUNTIME / "provenance.js").read_text())
            return page.evaluate(
                """([text, locale]) => window.prumiProvenance.numbers(text, locale).flatMap(
                    (n) => n.readings.map((r) => r.value * n.scale))""",
                [text, locale],
            )
        finally:
            page.close()

    assert demo_services.checker._submit(evaluate) == pytest.approx(values)


RESULTS = {
    "kpi": {
        "columns": [{"name": "revenue", "kind": "metric", "label": "Revenue"}],
        "rows": [{"revenue": 47123}],
        "total": {"revenue": 2742409.35},
    }
}


def in_locale(services, locale):
    import dataclasses

    config = services.config
    services.config = dataclasses.replace(
        config, server=dataclasses.replace(config.server, locale=locale)
    )
    return services


def test_the_server_check_reads_numbers_in_the_servers_locale(demo_services):
    in_locale(demo_services, "pt-BR")
    spec = DashboardSpec.model_validate(KPI_SPEC)
    results = RESULTS
    shown = """prumi.on("kpi", (b) => {
      document.getElementById("compact").textContent =
        prumi.format(b.rows[0].revenue, "revenue", {compact: true});
      document.getElementById("total").textContent = prumi.format(b.total.revenue, "revenue");
    });"""
    body = '<p id="compact"></p><p id="total"></p>'
    assert demo_services.check_page(spec, kpi_page(shown, body), results) == []
    # Off by a thousand, in the same words: refused.
    wrong = shown.replace("b.rows[0].revenue, ", "b.rows[0].revenue + 1000, ")
    [issue] = demo_services.check_page(spec, kpi_page(wrong, body), results)
    assert issue.code == "R8.unverified_number"
    assert WHERE.match(issue.message)[1] == "p#compact"


def test_the_other_convention_doesnt_match_in_en_us(demo_services):
    spec = DashboardSpec.model_validate(KPI_SPEC)
    script = """prumi.on("kpi", (b) => {
      document.getElementById("revenue").textContent =
        new Intl.NumberFormat("pt-BR", {minimumFractionDigits: 2}).format(b.total.revenue);
    });"""
    [issue] = demo_services.check_page(spec, kpi_page(script), RESULTS)
    assert issue.code == "R8.unverified_number"
    assert WHERE.match(issue.message)[1] == "p#revenue"


@pytest.mark.parametrize(
    ("body", "script"),
    [("<p>US$ 2.742.409,35</p>", ""), ("", "const total = 2742409.35;")],
)
def test_copied_numbers_in_another_locale(demo_services, body, script):
    in_locale(demo_services, "pt-BR")
    page = kpi_page(SHOW_REVENUE + script, '<p id="revenue"></p>' + body)
    report = demo_services.validate(KPI_SPEC, page)
    assert "R8.copied_number" in codes(report), report.issues


# Numbers copied into the source


@pytest.mark.parametrize(
    ("body", "script", "copied"),
    [
        # The 12-week revenue typed into the text, at any precision with 3+ digits.
        ("<p>Revenue was $2,742,409.35</p>", "", ["$2,742,409.35"]),
        ("<p>about $2,742K</p>", "", ["$2,742K"]),
        # In a script literal, with 4+ digits.
        ("", "const total = 2742409.35;", ["2742409.35"]),
        ("", 'const label = "$2,742,409";', ["$2,742,409"]),
        # Round layout constants, attributes and round text don't count.
        ("", "const width = 1200; const height = 2742;", []),
        ('<svg width="2742409"><rect x="2742409" /></svg>', "", []),
        ("<p>$2.7M</p>", "", []),
    ],
)
def test_copied_numbers(demo_services, body, script, copied):
    page = kpi_page(SHOW_REVENUE + script, '<p id="revenue"></p>' + body)
    report = demo_services.validate(KPI_SPEC, page)
    found = [i for i in report.issues if i.code == "R8.copied_number"]
    assert len(found) == (1 if copied else 0), report.issues
    for issue in found:
        assert issue.severity == "error" and LINE.search(issue.message)
        assert not any(c.strip("$") in issue.message for c in copied)  # never the number


# Content the number check can't read


@pytest.mark.parametrize(
    ("script", "code"),
    [
        ('document.body.append(document.createElement("iframe"));', "html.iframe"),
        ('document.body.attachShadow({ mode: ["clo", "sed"].join("") });',
         "html.closed_shadow_root"),
        ('document.body.append(new Image());', "html.image"),
        (
            'const svg = "http://www.w3.org/2000/svg";'
            'document.body.append(document.createElementNS(svg, "image"));',
            "html.image",
        ),
        ('document.body.style.backgroundImage = "url(data:image/png;base64,iVBORw0KGgo=)";',
         "html.image"),
    ],
)  # fmt: skip
def test_the_server_check_finds_what_it_cant_read(demo_services, script, code):
    report = demo_services.validate(KPI_SPEC, kpi_page(SHOW_REVENUE + script))
    assert [i.code for i in report.errors] == [code], report.issues


def test_open_shadow_roots_are_read(demo_services):
    script = """
      const root = document.getElementById("page").attachShadow({ mode: "open" });
      root.innerHTML = "<p>$9,999,999.99</p><slot></slot>";"""
    report = demo_services.validate(KPI_SPEC, kpi_page(SHOW_REVENUE + script))
    assert [WHERE.match(i.message)[1] for i in report.errors] == ["div#page > p"]


# Axis ticks


TICKS = "".join(f'<{{tag}} id="t{n}">${n}K</{{tag}}>' for n in (100, 200, 300, 400))


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        # Evenly spaced round numbers in a chart are its axis.
        (f"<svg>{TICKS.format(tag='text')}</svg>", []),
        # The same numbers in HTML need the marker.
        (
            f"<div>{TICKS.format(tag='span')}</div>",
            ["span#t100", "span#t200", "span#t300", "span#t400"],
        ),
        (f'<div data-prumi-role="axis">{TICKS.format(tag="span")}</div>', []),
    ],
)
def test_unmarked_ticks_only_inside_svg(demo_services, body, flagged):
    report = demo_services.validate(KPI_SPEC, kpi_page(SHOW_REVENUE, '<p id="revenue"></p>' + body))
    assert [WHERE.match(i.message)[1] for i in report.errors] == flagged


# Bindings decide what a number may match

TWO_BLOCKS = {
    "spec_version": 0,
    "dashboard": {"id": "two-blocks", "title": "Revenue"},
    "data": [
        {"id": "kpi", "metrics": ["revenue"], "window": {"last": 12, "grain": "week"},
         "filters": []},
        {"id": "weekly", "metrics": ["revenue"], "time_grain": "week",
         "window": {"last": 12, "grain": "week"}, "filters": []},
    ],
}  # fmt: skip
TWO_BLOCKS_PAGE = """<!doctype html><html><body>
<table id="weeks"><tbody id="rows"></tbody><tfoot><tr><td id="total"></td></tr></tfoot></table>
<p id="note"></p>
<script>
  prumi.on("weekly", (b) => {
    document.getElementById("rows").innerHTML = b.rows.map(
      (r) => `<tr><td>${prumi.format(r.revenue, "revenue")}</td></tr>`).join("");
  });
  prumi.on("kpi", (b) => {
    document.getElementById("total").textContent = prumi.format(b.rows[0].revenue, "revenue");
  });
</script></body></html>"""


def two_blocks(bindings):
    return {**TWO_BLOCKS, "presentation": {"bindings": bindings}}


def test_a_number_outside_every_bound_element_is_unbound(demo_services):
    report = demo_services.validate(two_blocks({"#rows": "weekly"}), TWO_BLOCKS_PAGE)
    [issue] = report.errors
    assert issue.code == "R8.unbound_number"
    assert WHERE.match(issue.message)[1] == "td#total"
    assert issue.hint.startswith("Bind this element to the block it shows")


def test_a_binding_with_a_list_of_blocks_accepts_numbers_from_each(demo_services):
    one_block = demo_services.validate(two_blocks({"#weeks": "weekly"}), TWO_BLOCKS_PAGE)
    [issue] = one_block.errors
    assert issue.code == "R8.unverified_number" and "(weekly)" in issue.message
    both = demo_services.validate(two_blocks({"#weeks": ["weekly", "kpi"]}), TWO_BLOCKS_PAGE)
    assert both.ok, both.issues


def test_the_nearest_bound_ancestor_wins(demo_services):
    # The table is bound to both blocks, but its total cell only to weekly.
    report = demo_services.validate(
        two_blocks({"#weeks": ["weekly", "kpi"], "#total": "weekly"}), TWO_BLOCKS_PAGE
    )
    assert [i.code for i in report.errors] == ["R8.unverified_number"]


def test_a_binding_must_name_known_blocks(demo_services):
    report = demo_services.validate(two_blocks({"#weeks": ["weekly", "nope"]}), TWO_BLOCKS_PAGE)
    [issue] = report.errors
    assert (issue.code, issue.path) == ("spec.unknown_block", "presentation.bindings['#weeks'][1]")


# The hover pass


def test_the_hover_pass_finds_unbound_tooltips(demo_services):
    spec, html = fixture("tooltip")
    report = demo_services.validate(spec, html)
    assert report.errors and {i.code for i in report.errors} == {"R8.unbound_number"}
    for issue in report.errors:
        assert issue.message.startswith("On hover, ") and WHERE.match(issue.message)[1] == "div#tip"
        assert issue.hint.startswith("Render the tooltip inside the bound element")
    spec["presentation"]["bindings"]["#tip"] = "weekly"
    assert demo_services.validate(spec, html).ok


def test_the_hover_pass_adds_about_a_second_at_most(demo_services, monkeypatch):
    import time

    spec, html = fixture("first_run")  # a page with many marks to hover
    demo_services.validate(spec, html)  # warm up

    def timed() -> float:
        started = time.monotonic()
        assert demo_services.validate(spec, html).ok
        return time.monotonic() - started

    with_hover = min(timed() for _ in range(2))
    monkeypatch.setattr(demo_services.checker, "hover_samples", 0)
    without = min(timed() for _ in range(2))
    assert with_hover - without < 1.5


# Where a runtime error happened

THROWS = """<!doctype html>
<html><head><title>x</title></head>
<body><div id="page"><p id="revenue"></p></div>
<script>
  function draw() {
    document.getElementById("missing").textContent = "x";
  }
  draw();
</script></body></html>"""


def runtime_error(demo_services, html):
    [issue] = [i for i in demo_services.validate(KPI_SPEC, html).errors]
    assert issue.code == "html.runtime_error"
    return issue.message


def test_a_runtime_error_gives_its_line_and_column_in_the_html(demo_services):
    message = runtime_error(demo_services, THROWS)
    column = THROWS.splitlines()[5].index(" = ") + 2  # V8 points at the failing assignment
    first, *stack = message.splitlines()
    assert first == (
        f"The page threw an error while rendering, at line 6, column {column} of the HTML: "
        "Cannot set properties of null (setting 'textContent')"
    )
    assert stack == [f"  at draw (page:6:{column})", "  at page:8:3"]


def test_columns_on_the_head_line_ignore_what_prumi_inserts(demo_services):
    html = (
        '<!doctype html><html><head><script>document.getElementById("x").textContent = 1;'
        '</script></head><body><div id="page"></div></body></html>'
    )
    column = html.index(" = ") + 2
    assert f"at line 1, column {column} of the HTML" in runtime_error(demo_services, html)


def test_an_error_in_a_callback_names_the_callback(demo_services):
    page = kpi_page('prumi.on("kpi", (b) => {\n  b.rows[0].nothing.value;\n});')
    message = runtime_error(demo_services, page)
    assert ", at line 2, column " in message.splitlines()[0]
    stack = message.splitlines()[1:]
    assert 1 <= len(stack) <= 5 and stack[0].startswith("  at ") and "page:2:" in stack[0]
    assert any("runtime.js:" in line for line in stack)  # Prumi's own frames, by file name


NARROW = "".join(f'<{{tag}} id="n{n}">${n}</{{tag}}>' for n in (280, 286, 292, 298))


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        # A narrow axis: evenly spaced, three significant digits.
        (f"<svg>{NARROW.format(tag='text')}</svg>", []),
        (f'<div data-prumi-role="axis">{NARROW.format(tag="span")}</div>', []),
        (
            f"<div>{NARROW.format(tag='span')}</div>",
            ["span#n280", "span#n286", "span#n292", "span#n298"],
        ),
        # A made-up label among the ticks is still flagged; the ticks still pass.
        (f'<svg>{NARROW.format(tag="text")}<text id="label">$284.57</text></svg>', ["text#label"]),
        # A single round number needs the marker; a single precise one is never a tick.
        ('<div data-prumi-role="axis"><span>$300</span></div>', []),
        ('<div data-prumi-role="axis"><span id="one">$286</span></div>', ["span#one"]),
    ],
)
def test_evenly_spaced_ticks_at_any_precision(demo_services, body, flagged):
    report = demo_services.validate(KPI_SPEC, kpi_page(SHOW_REVENUE, '<p id="revenue"></p>' + body))
    assert [WHERE.match(i.message)[1] for i in report.errors] == flagged
