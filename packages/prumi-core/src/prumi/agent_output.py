"""What agents may see of a query result: its shape, never its values.

Agent results go to the agent's LLM provider, and Prumi never sends company data there
(docs/adr/0010). Every query result returned to an agent passes through `for_agent`: the
agent gets counts, empties, label lengths and magnitudes to design with, and the numbers and
names appear only in the dashboard, in the viewer's browser. Other agent-facing text, such
as issue messages and check reports, is cleaned where it's built.
"""

from __future__ import annotations

from collections import Counter
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Literal

from pydantic import BaseModel

from prumi.engine import MAX_ROWS, BlockResult
from prumi.issues import Issue

Sign = Literal["positive", "negative", "mixed", "zero"]


class Range(BaseModel):
    min: int
    max: int


class ColumnProfile(BaseModel):
    name: str
    kind: str  # dimension, time or metric
    label: str
    distinct: int | None = None  # dimensions and time
    nulls: int | None = None  # dimensions and metrics
    label_length: Range | None = None  # dimensions: shortest and longest label, in characters
    format: dict[str, Any] | None = None  # metrics
    sign: Sign | None = None  # metrics
    # Metrics: the range of integer-digit counts (4 to 5 means 1,000 to 99,999), the one
    # statistic taken from values, because axes can't be designed without it.
    magnitude: Range | None = None
    decimals: int | None = None  # metrics: the most common number of decimals shown


class QueryProfile(BaseModel):
    block: str
    row_count: int | str  # "more than 5000" past the engine's cap
    window: dict[str, Any] | None = None
    freshness: dict[str, str | None] = {}
    columns: list[ColumnProfile]
    has_total: bool
    has_others: bool
    has_comparison: bool
    warnings: list[Issue] = []


def for_agent(result: BlockResult) -> QueryProfile:
    """`result` as an agent may see it: its profile."""
    rows = result.rows
    columns = []
    for column in result.columns:
        values = [row.get(column.name) for row in rows]
        present = [v for v in values if v is not None]
        profile = ColumnProfile(name=column.name, kind=column.kind, label=column.label)
        if column.kind in ("dimension", "time"):
            profile.distinct = len({str(v) for v in present})
        if column.kind == "dimension":
            profile.nulls = len(values) - len(present)
            lengths = [len(str(v)) for v in present]
            if lengths:
                profile.label_length = Range(min=min(lengths), max=max(lengths))
        if column.kind == "metric":
            numbers = [float(v) for v in present if isinstance(v, int | float)]
            profile.format = column.format
            profile.nulls = len(values) - len(present)
            profile.sign = _sign(numbers)
            if numbers:
                digits = [len(str(int(abs(v)))) for v in numbers]
                profile.magnitude = Range(min=min(digits), max=max(digits))
                profile.decimals = _decimals(numbers, column.format or {})
        columns.append(profile)
    return QueryProfile(
        block=result.block,
        row_count=f"more than {MAX_ROWS}" if result.truncated else len(rows),
        window=result.window,
        freshness=result.freshness,
        columns=columns,
        has_total=result.total is not None,
        has_others=result.others is not None,
        has_comparison=result.comparison_window is not None,
        warnings=result.warnings,
    )


def _sign(numbers: list[float]) -> Sign | None:
    if not numbers:
        return None
    if all(v == 0 for v in numbers):
        return "zero"
    if all(v >= 0 for v in numbers):
        return "positive"
    if all(v <= 0 for v in numbers):
        return "negative"
    return "mixed"


def _decimals(numbers: list[float], fmt: dict[str, Any]) -> int:
    """The most common number of decimals prumi.format shows for these values."""
    kind, fixed = fmt.get("type", "number"), fmt.get("decimals")
    if kind == "integer":
        return 0
    if kind == "currency":
        return 2 if fixed is None else fixed  # Intl's default for the currencies in use
    scale, at_most = (100, 1 if fixed is None else fixed) if kind == "percent" else (1, fixed)
    if at_most is None:
        at_most = 2
    shown = Counter(_shown_decimals(v * scale, at_most) for v in numbers)
    return max(shown, key=lambda d: (shown[d], d))  # a tie: the wider one, for layout


def _shown_decimals(value: float, at_most: int) -> int:
    """Decimals left once `value` is rounded to `at_most` and trailing zeros dropped."""
    rounded = Decimal(repr(value)).quantize(Decimal(1).scaleb(-at_most), ROUND_HALF_EVEN)
    text = format(rounded, "f")
    return len(text.split(".")[1].rstrip("0")) if "." in text else 0
