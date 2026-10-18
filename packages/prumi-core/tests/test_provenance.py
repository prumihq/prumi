"""Number provenance: every number a page shows must be a value Prumi delivered.

The server check renders each page in headless Chromium with its blocks' results for the
filters' default values, and reads what it shows. What each fixture in fixtures/provenance/
must get flagged ([code, text], text null for page-wide codes) is in expected.json; an empty
list means it passes.
"""

import json
import os
import re
from pathlib import Path

import pytest
from prumi.cli import main
from prumi.mcp import AUTHORING_GUIDE
from prumi.validate import page as page_check

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures" / "provenance"
RUNTIME = Path(__file__).parents[2] / "prumi-runtime" / "src"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
SHOWN = re.compile(r"The page shows (.+) \(in (.+)\), which isn't a value Prumi delivered\.")
# The number an R8 issue is about, whatever the code.
NUMBER = re.compile(r"(?:The page shows|The page's source has) (.+?) \((?:in|on) ")
FLAGGED = {"html.image", "html.iframe", "html.closed_shadow_root", "html.canvas"}
# Must-fail pages for checks that don't exist yet.
NOT_CHECKED_YET = {
    "copied_today": "numbers copied into the source aren't checked yet",
    "coincident_average": "numbers aren't matched against their binding's blocks yet",
    "image_numbers": "images aren't refused yet",
    "spaced_series_outside_svg": "unmarked ticks are still allowed outside <svg>",
}

KPI_SPEC = {
    "spec_version": 0,
    "dashboard": {"id": "kpi", "title": "Revenue"},
    "data": [{"id": "kpi", "metrics": ["revenue"], "window": {"last": 12, "grain": "week"},
              "filters": []}],
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
    return f"<!doctype html><html><body>{body}<script>{script}</script></body></html>"


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
        if issue.code.startswith("R8.") or issue.code in FLAGGED:
            number = NUMBER.match(issue.message)
            found.append((issue.code, number[1] if number else None))
    return sorted(found, key=str)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(n, marks=pytest.mark.xfail(reason=NOT_CHECKED_YET[n], strict=True))
        if n in NOT_CHECKED_YET
        else n
        for n in sorted(EXPECTED)
    ],
)
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
    assert SHOWN.fullmatch(issue.message).groups() == ("$1,234.56", "div.note > b")


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
    assert [SHOWN.fullmatch(i.message)[1] for i in report.errors] == ["$9,999,999.99"]


def test_a_blocked_request_is_a_warning(demo_services):
    page = kpi_page(SHOW_REVENUE + 'fetch("https://example.com/data.json").catch(() => {});')
    report = demo_services.validate(KPI_SPEC, page)
    assert report.ok
    static, blocked = report.warnings
    assert static.code == "html.network_call"  # found in the source before the page ran
    assert blocked.code == "html.blocked_request"
    assert "https://example.com/data.json" in blocked.message


def test_the_server_check_runs_no_extra_queries(demo_services, monkeypatch):
    runs = []
    original = demo_services.engine.run_block
    monkeypatch.setattr(
        demo_services.engine, "run_block", lambda *a, **k: runs.append(a[1]) or original(*a, **k)
    )
    spec, html = fixture("second_run")
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
    ("text", "readings"),
    [
        ("$2,742,409", [2742409]),
        ("1,234", [1234, 1.234]),
        ("1.234,56 €", [1234.56]),
        ("12,5 %", [12.5]),
        ("2.7M", [2.7]),
    ],
)
def test_numbers_are_read_in_either_locale(demo_services, text, readings):
    def evaluate(browser):
        page = browser.new_page()
        try:
            page.add_script_tag(content=(RUNTIME / "provenance.js").read_text())
            return page.evaluate(
                """(text) => window.prumiProvenance.numbers(text).flatMap(
                    (n) => window.prumiProvenance.readings(n.raw).map((r) => r.value))""",
                text,
            )
        finally:
            page.close()

    assert demo_services.checker._submit(evaluate) == pytest.approx(readings)
