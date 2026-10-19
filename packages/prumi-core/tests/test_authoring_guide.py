"""The authoring guide agents read before writing a dashboard."""

import json
import re
from pathlib import Path

from prumi.mcp import AUTHORING_GUIDE
from prumi.spec.schema import SCHEMA_PATH

GUIDE = AUTHORING_GUIDE.read_text(encoding="utf-8")


def fenced(after_heading: str, language: str) -> str:
    """The first ```language block after a heading."""
    section = GUIDE.split(after_heading, 1)[1]
    return re.search(rf"```{language}\n(.*?)```", section, re.S).group(1)


def test_the_example_validates_on_the_demo(demo_services):
    spec = json.loads(fenced("### Example spec", "json"))
    html = fenced("### Example HTML", "html")
    assert "prumi.change(" in html  # changes, with arrows from the sign shown
    report = demo_services.validate(spec, html)
    assert report.ok, report.errors
    assert not report.warnings, report.warnings


def test_the_guide_names_every_runtime_member(runtime_members):
    assert set(runtime_members) == {
        "on", "onError", "setFilter", "getFilter", "filterValues", "onFilters", "click",
        "format", "change", "locale", "label",
    }  # fmt: skip
    for name in runtime_members:
        named = f"`prumi.{name}`" if name == "locale" else f"`prumi.{name}("  # a string
        assert named in GUIDE, name


def test_the_inlined_schema_is_the_committed_one():
    inlined = GUIDE.split("<!-- schema:start -->", 1)[1].split("<!-- schema:end -->", 1)[0]
    assert inlined == "\n```json\n" + SCHEMA_PATH.read_text(encoding="utf-8") + "```\n", (
        "The guide's schema is stale: run `uv run python -m prumi.spec.schema`."
    )


def test_the_guide_describes_the_result_object():
    for key in ("_partial", "_previous", "_delta", "_delta_pct", "others", "total", "window",
                "freshness", "rows: []"):  # fmt: skip
        assert key in GUIDE, key


def test_the_guide_covers_numbers_theme_and_editing():
    for text in (
        "5,000 rows", "one grand total for the whole window",
        "Layout math is fine", 'data-prumi-role="axis"', "no `<canvas>`", "--prumi-series-1",
        "--prumi-radius", "{compact: true}", "html_edits", "base", "edit.no_match",
        "take one screenshot of the draft at desktop width; don't read the page's text",
        "Send the HTML straight to `validate_spec`",
        # Every number belongs to a bound block; what can't be read isn't allowed.
        "Every element that shows numbers must be bound", '"#table": ["regions", "regions_total"]',
        "R8.unbound_number", "R8.unverified_number", "R8.copied_number", "Tooltips",
        '<div id="tip" hidden></div>', "html.iframe", "html.closed_shadow_root", "html.image",
        "needs_attention",
        # Exempt patterns, sorting, comparisons on totals, check_states, fixing a failed page.
        "`Oct 14, 2026`", "from 1900 to 2199", "Last 26 weeks", "Store 12",
        "ordering or sorting by a value computed in JavaScript",
        "as long as the computed value isn't shown", "`$280, $286, $292`",
        "They carry no comparison values in this release", "check_states", "With the filters",
        "also one whose validation failed", "base=candidate_id, html_edits=",
        "a dashboard id for its current version",
        # The layout report.
        "Read the layout warnings", "layout.text_overlap", "layout.text_clipped",
        "layout.too_wide", "layout.small_text", "layout.empty_block", 'layout_devices: ["phone"]',
        "ask the user to look at the draft link",
    ):  # fmt: skip
        assert text in GUIDE, text


WORDING = (
    "Prumi never sends company data to your agent: `query` shows the shape of the data, and "
    "the numbers appear only in the dashboard."
)


def test_the_guide_instructions_and_readme_say_no_company_data_reaches_the_agent():
    from prumi.mcp import INSTRUCTIONS

    readme = (Path(__file__).parents[3] / "README.md").read_text(encoding="utf-8")
    for text in (GUIDE, INSTRUCTIONS, readme):
        assert WORDING in text
        assert "200 rows" not in text and "200-row" not in text


def test_the_guide_instructions_and_readme_give_the_one_locale_rule():
    from prumi.mcp import INSTRUCTIONS

    readme = (Path(__file__).parents[3] / "README.md").read_text(encoding="utf-8")
    for text in (GUIDE, INSTRUCTIONS, readme):
        assert "`prumi.format`" in text and "`prumi.locale`" in text
    for text in (GUIDE, INSTRUCTIONS):
        assert "`toLocaleString()`" in text and "`Intl.*(undefined, …)`" in text
    assert "R8.browser_locale" in GUIDE
    assert "`locale` under `[server]`" in readme
    # The guide's own code never formats with the browser's language.
    assert "undefined," not in GUIDE.replace("`Intl.*(undefined, …)`", "")
    assert ".toLocale" not in GUIDE
