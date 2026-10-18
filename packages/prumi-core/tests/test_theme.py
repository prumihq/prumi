"""The default theme: every variable in both modes, readable text, loaded by frame and shell."""

import re
from pathlib import Path

import pytest

THEME = (Path(__file__).parents[2] / "prumi-runtime" / "src" / "theme.css").read_text()
VARIABLES = {
    "--prumi-bg", "--prumi-surface", "--prumi-text", "--prumi-text-muted", "--prumi-border",
    "--prumi-accent", "--prumi-positive", "--prumi-negative",
    *(f"--prumi-series-{n}" for n in range(1, 9)),
    "--prumi-font", "--prumi-font-mono", "--prumi-radius",
}  # fmt: skip
# Colors used for text, against the backgrounds they're used on.
TEXT = ("--prumi-text", "--prumi-text-muted", "--prumi-accent", "--prumi-positive",
        "--prumi-negative")  # fmt: skip
BACKGROUNDS = ("--prumi-bg", "--prumi-surface")


def declarations(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--prumi-[\w-]+)\s*:\s*([^;]+);", block))


def modes() -> dict[str, dict[str, str]]:
    dark_start = THEME.index("@media (prefers-color-scheme: dark)")
    light = declarations(THEME[:dark_start])
    # Dark mode overrides the colors; fonts and radius stay.
    dark = {**light, **declarations(THEME[dark_start:])}
    return {"light": light, "dark": dark}


def luminance(color: str) -> float:
    channels = [int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_every_variable_is_defined_in_both_modes():
    light = declarations(THEME[: THEME.index("@media")])
    dark = declarations(THEME[THEME.index("@media") :])
    assert set(light) == VARIABLES
    # Dark mode redefines every color; fonts and radius are the same in both.
    assert set(dark) == VARIABLES - {"--prumi-font", "--prumi-font-mono", "--prumi-radius"}


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("text", TEXT)
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_text_colors_are_readable(mode, text, background):
    colors = modes()[mode]
    assert contrast(colors[text], colors[background]) >= 4.5
