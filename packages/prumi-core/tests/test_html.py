"""HTML checks: R8 fixtures, size limit, tokenizer and URL rules (spec section 8, AC 15)."""

import json
from pathlib import Path

import pytest
from prumi.spec import DashboardSpec
from prumi.validate.html import (
    DEFAULT_HOSTS,
    AllowedHosts,
    check_html,
    data_like_numbers,
    find_dataset,
)

R8 = Path(__file__).parent / "fixtures" / "r8"
EXPECTED = json.loads((R8 / "expected.json").read_text())

# One block, bound to the fixtures' <div id="kpi">.
SPEC = DashboardSpec.model_validate(
    {
        "spec_version": 0,
        "dashboard": {"id": "r8", "title": "R8"},
        "filters": [{"id": "period", "type": "time", "default": {"last": 4, "grain": "week"}}],
        "data": [{"id": "kpi", "metrics": ["revenue"], "filters": ["period"]}],
        "presentation": {"bindings": {"#kpi": "kpi"}},
    }
)


def codes(html: str, hosts: AllowedHosts = DEFAULT_HOSTS) -> list[str]:
    return [issue.code for issue in check_html(SPEC, html, hosts)]


def test_every_r8_fixture_has_an_expectation():
    assert sorted(p.name for p in R8.glob("*.html")) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_r8_fixtures(name):
    assert codes((R8 / name).read_text()) == EXPECTED[name]


def test_too_large():
    html = "<!doctype html><div id='kpi'></div><p>" + "x" * (2 * 1024 * 1024) + "</p>"
    [issue] = check_html(SPEC, html)
    assert (issue.code, issue.severity, issue.path) == ("html.too_large", "error", "html")


# The dataset heuristic


@pytest.mark.parametrize(
    "code",
    [
        "const d = [1,2,3,4,5,6,7,8,9,10,11,12];",
        "const d = {a: [1, 2, 3, 4, 5, 6], b: [7, 8, 9, 10, 11, 12]};",  # nested ones count
        "draw([[1, 10], [2, 20], [3, 30], [4, 40], [5, 50], [6, 60]]);",
        'const rows = [{r: "N", v: 1.5}, {r: "S", v: 2}, {r: "E", v: 3}, {r: "W", v: 4},'
        ' {r: "C", v: 5}, {r: "X", v: 6}];',
    ],
)
def test_datasets(code):
    assert find_dataset(code) is not None


@pytest.mark.parametrize(
    "code",
    [
        "// [1,2,3,4,5,6,7,8,9,10,11,12]\nconst a = 1;",  # a comment
        'const s = "1,2,3,4,5,6,7,8,9,10,11,12";',  # a string
        "for (let i = 0; i < 12; i++) { x[i] = y[i] * 2 + 3 - 4 / 5 + 6 + 7 + 8 + 9 + 10 + 11; }",
        "const names = ['a','b','c','d','e','f','g','h','i','j','k','l','m','n','o','p','q','r',"
        " 's','t', 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];",  # 12 of 32 values: under 40%
        'const series = [{name: "a", color: "#111", width: 2}, {name: "b", color: "#222",'
        ' width: 2}, {name: "c", color: "#333", width: 2}, {name: "d", color: "#444", width: 2},'
        ' {name: "e", color: "#555", width: 2}, {name: "f", color: "#666", width: 2}];',
    ],
)
def test_not_datasets(code):
    assert find_dataset(code) is None


def test_object_keys_are_not_values():
    # 12 numeric keys, 12 string values: no numbers among the values.
    code = "const m = {" + ", ".join(f'{i}: "v{i}"' for i in range(12)) + "};"
    assert find_dataset(code) is None


@pytest.mark.parametrize(
    ("text", "count"),
    [("1,234 and 12.5% and $3.4M", 3), ("2026, 12 weeks, 5 regions", 0), ("€3 0.25 7k", 3)],
)
def test_data_like_numbers(text, count):
    assert data_like_numbers(text) == count


# Hosts and versions


def page(head: str) -> str:
    return f'<html><head>{head}</head><body><div id="kpi"></div></body></html>'


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        ('<script src="/runtime.js"></script>', []),  # Prumi's own runtime, relative
        ('<script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js"></script>', []),
        ('<script src="//evil.example.com/x@1.0.0/x.js"></script>', ["html.script_host"]),
        (
            '<script src="https://unpkg.com/react@18" integrity="sha384-abc"></script>',
            ["html.unpinned_script"],
        ),
        ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">', []),
        ('<link rel="stylesheet" href="https://styles.example.com/a.css">', ["html.script_host"]),
        ('<link rel="preload" as="font" href="https://fonts.gstatic.com/s/inter.woff2">', []),
        (
            '<link rel="preload" as="font" href="https://fonts.example.com/a.woff2">',
            ["html.script_host"],
        ),
        ('<style>@import url("https://styles.example.com/a.css");</style>', ["html.script_host"]),
        (
            "<style>@font-face { font-family: X; src: url(https://fonts.example.com/x.woff2); }"
            " body { background: url(data:image/png;base64,AAAA); }</style>",
            ["html.script_host"],
        ),
        ('<style>@font-face { src: url("https://fonts.gstatic.com/s/x.woff2"); }</style>', []),
    ],
)
def test_external_urls(head, expected):
    assert codes(page(head)) == expected


def test_allowed_hosts_come_from_the_config():
    head = '<script src="https://cdn.internal.example/lib@1.0.0/lib.js"></script>'
    assert codes(page(head)) == ["html.script_host"]
    assert codes(page(head), AllowedHosts(script=("cdn.internal.example",))) == []
