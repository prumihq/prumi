"""The authoring guide agents read before writing a dashboard (spec section 18, AC 32)."""

import json
import re
from pathlib import Path

from prumi.mcp import AUTHORING_GUIDE
from prumi.spec.schema import SCHEMA_PATH

RUNTIME = Path(__file__).parents[2] / "prumi-runtime" / "src" / "runtime.js"
GUIDE = AUTHORING_GUIDE.read_text(encoding="utf-8")


def fenced(after_heading: str, language: str) -> str:
    """The first ```language block after a heading."""
    section = GUIDE.split(after_heading, 1)[1]
    return re.search(rf"```{language}\n(.*?)```", section, re.S).group(1)


def test_the_example_validates_on_the_demo(demo_services):
    spec = json.loads(fenced("### Example spec", "json"))
    html = fenced("### Example HTML", "html")
    report = demo_services.validate(spec, html)
    assert report.ok, report.errors
    assert not report.warnings, report.warnings


def test_the_guide_names_every_runtime_function():
    api = RUNTIME.read_text().split("window.prumi = Object.freeze({", 1)[1].split("\n  });", 1)[0]
    functions = re.findall(r"^    (\w+):", api, re.M)
    assert set(functions) == {
        "on", "onError", "setFilter", "getFilter", "filterValues", "onFilters", "click",
        "format", "label",
    }  # fmt: skip
    for name in functions:
        assert f"`prumi.{name}(" in GUIDE, name


def test_the_inlined_schema_is_the_committed_one():
    inlined = GUIDE.split("<!-- schema:start -->", 1)[1].split("<!-- schema:end -->", 1)[0]
    assert inlined == "\n```json\n" + SCHEMA_PATH.read_text(encoding="utf-8") + "```\n", (
        "The guide's schema is stale: run `uv run python -m prumi.spec.schema`."
    )


def test_the_guide_describes_the_result_object():
    for key in ("_partial", "_previous", "_delta", "_delta_pct", "others", "total", "window",
                "freshness", "rows: []"):  # fmt: skip
        assert key in GUIDE, key
