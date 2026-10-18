"""Time grains and date ranges (spec section 5)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

Grain = Literal["day", "week", "month", "quarter", "year"]
GRAINS: tuple[Grain, ...] = ("day", "week", "month", "quarter", "year")


@dataclass(frozen=True)
class DateRange:
    """Inclusive dates."""

    start: dt.date
    end: dt.date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"DateRange start {self.start} is after its end {self.end}")
