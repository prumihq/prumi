"""The layout report: what the server check measures on its render, and what it never says."""

import json
import os
import re
from pathlib import Path

import pytest
from prumi.validate import page as page_check

pytestmark = pytest.mark.browser

FIXTURES = Path(__file__).parent / "fixtures" / "layout"
SPEC = json.loads((FIXTURES / "spec.json").read_text())
CODES = [
    "layout.empty_block",
    "layout.small_text",
    "layout.text_clipped",
    "layout.text_overlap",
    "layout.too_wide",
]


@pytest.fixture(autouse=True)
def chromium(demo_services):
    try:
        demo_services.checker.start()
    except page_check.BrowserStartupError:
        if os.environ.get("PRUMI_REQUIRE_BROWSER"):
            raise
        pytest.skip("Headless Chromium can't start; run `uv run prumi install-chromium`.")


def layout(report):
    return [i for i in report.issues if i.code.startswith("layout.")]


def test_each_layout_problem_is_reported_once(demo_services):
    report = demo_services.validate(SPEC, (FIXTURES / "problems.html").read_text())
    found = layout(report)
    assert sorted(i.code for i in found) == CODES, found
    assert report.ok  # warnings don't block publishing
    assert all(i.severity == "warning" and i.hint for i in found)
    [overlap] = [i for i in found if i.code == "layout.text_overlap"]
    assert re.search(
        r"div\.labels:nth-of-type\(1\) > span:nth-of-type\(1\) \(x 10, y \d+, \d+×\d+\)",
        overlap.message,
    )
    [empty] = [i for i in found if i.code == "layout.empty_block"]
    assert empty.message.startswith("div#empty (") and "bound to kpi" in empty.message


def test_a_clean_page_has_no_layout_warnings(demo_services):
    report = demo_services.validate(SPEC, (FIXTURES / "clean.html").read_text())
    assert report.ok and layout(report) == [], report.issues


def test_layout_issues_never_contain_the_pages_text_or_attributes(demo_services):
    """Every text node and attribute holds a sentinel; no layout issue may repeat it."""
    html = (FIXTURES / "problems.html").read_text()
    html = re.sub(r">([^<>{}]*[A-Za-z][^<>{}]*)<", r">\1 Qzx731<", html)  # every text node
    html = html.replace(
        '<div class="wide">', '<div class="wide" data-note="Qzx731" title="Qzx731">'
    )
    found = layout(demo_services.validate(SPEC, html))
    assert sorted(i.code for i in found) == CODES
    for issue in found:
        assert "Qzx" not in issue.message and "731" not in issue.message, issue.message
        for word in ("North", "South", "Revenue", "Source", "channel"):
            assert word not in issue.message, issue.message


def test_the_phone_render_adds_its_own_warnings(demo_services):
    html = (
        (FIXTURES / "clean.html")
        .read_text()
        .replace("<p>North America</p>", '<div style="width: 600px">North America</div>')
    )
    desktop = demo_services.validate(SPEC, html)
    assert layout(desktop) == []
    phone = demo_services.validate(SPEC, html, layout_devices=["phone"])
    [issue] = layout(phone)
    assert issue.code == "layout.too_wide"
    assert issue.message.startswith("On a phone (390×844): The page is ")
    report, published = demo_services.publish(SPEC, html)
    assert published is not None and report.ok
