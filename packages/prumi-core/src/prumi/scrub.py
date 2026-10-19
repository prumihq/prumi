"""Cleaning text a page produced, such as a browser's error message, before Prumi keeps it or
shows it to an agent: page code can put data into it (docs/adr/0010)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

_LONG_NUMBER = re.compile(r"\d[\d,.]*\d")


def scrubber(values: Iterable[object]) -> Callable[[str], str]:
    """A function that replaces each string value of 2 or more characters (longest first, so a
    value inside a longer one doesn't leave the rest behind), then any number with 3 or more
    digits, with "…". Shorter values and other types are left to the number rule."""
    ordered = sorted(
        {v for v in values if isinstance(v, str) and len(v) >= 2}, key=lambda v: (-len(v), v)
    )

    def scrub(text: str) -> str:
        for value in ordered:
            text = text.replace(value, "…")
        return _LONG_NUMBER.sub(
            lambda m: "…" if sum(c.isdigit() for c in m[0]) >= 3 else m[0], text
        )

    return scrub
