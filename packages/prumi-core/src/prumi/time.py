"""Time: grains, window forms, partial periods and comparison windows (spec section 5).

Prumi owns time windows (ADR 0002). Every window is resolved here against `as_of` ("today")
and aligned to whole periods before a query is compiled, so MetricFlow never receives a window
that would silently snap.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

Grain = Literal["day", "week", "month", "quarter", "year"]
GRAINS: tuple[Grain, ...] = ("day", "week", "month", "quarter", "year")
Compare = Literal["previous_period", "previous_year"]

ONE_DAY = dt.timedelta(days=1)


@dataclass(frozen=True)
class DateRange:
    """Inclusive dates."""

    start: dt.date
    end: dt.date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"DateRange start {self.start} is after its end {self.end}")


def today(as_of: dt.date | None) -> dt.date:
    """ "Today": the pinned `as_of`, or the server's local date."""
    return as_of if as_of is not None else dt.date.today()


# Periods


def _add_months(day: dt.date, months: int) -> dt.date:
    """The first of the month `months` after `day`'s month."""
    index = day.year * 12 + day.month - 1 + months
    return dt.date(index // 12, index % 12 + 1, 1)


def period_start(day: dt.date, grain: Grain) -> dt.date:
    """The first day of the period of `grain` that contains `day`. Weeks start on Monday."""
    if grain == "day":
        return day
    if grain == "week":
        return day - dt.timedelta(days=day.weekday())
    if grain == "month":
        return day.replace(day=1)
    if grain == "quarter":
        return dt.date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
    return dt.date(day.year, 1, 1)


def shift_period(start: dt.date, grain: Grain, n: int) -> dt.date:
    """The start of the period `n` periods after the one starting at `start` (n may be < 0)."""
    if grain == "day":
        return start + dt.timedelta(days=n)
    if grain == "week":
        return start + dt.timedelta(weeks=n)
    return _add_months(start, n * {"month": 1, "quarter": 3, "year": 12}[grain])


def period_end(day: dt.date, grain: Grain) -> dt.date:
    """The last day of the period of `grain` that contains `day`."""
    return shift_period(period_start(day, grain), grain, 1) - ONE_DAY


def count_periods(start: dt.date, end: dt.date, grain: Grain) -> int:
    """How many whole periods of `grain` run from `start` (a period start) to `end`."""
    n, cursor = 0, start
    while cursor <= end:
        n, cursor = n + 1, shift_period(cursor, grain, 1)
    return n


def year_earlier(day: dt.date) -> dt.date:
    """The same date one year earlier; Feb 29 becomes Feb 28."""
    if day.month == 2 and day.day == 29:
        return dt.date(day.year - 1, 2, 28)
    return day.replace(year=day.year - 1)


# Window forms, as written in dashboard specs and `query`


class _WindowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LastWindow(_WindowModel):
    """The N complete periods of `grain` before the current one (plus it, if asked)."""

    last: int = Field(ge=1, le=400)
    grain: Grain
    include_current: bool = False


class ToDateWindow(_WindowModel):
    """From the start of the current period of `to_date` to the day before as_of."""

    to_date: Grain


class RangeWindow(_WindowModel):
    """Inclusive dates, snapped inward to whole periods of `grain`."""

    start: dt.date
    end: dt.date
    grain: Grain | None = None  # default: the block's time_grain, else day
    include_current: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> RangeWindow:
        if self.start > self.end:
            raise ValueError("start must not be after end")
        return self


def window_form(value: Any) -> str | None:
    if isinstance(value, dict):
        keys = value.keys()
    elif isinstance(value, _WindowModel):
        keys = type(value).model_fields.keys()
    else:
        return None
    if "last" in keys:
        return "last"
    if "to_date" in keys:
        return "to_date"
    if "start" in keys or "end" in keys:
        return "range"
    return None


Window = Annotated[
    Annotated[LastWindow, Tag("last")]
    | Annotated[ToDateWindow, Tag("to_date")]
    | Annotated[RangeWindow, Tag("range")],
    Discriminator(
        window_form,
        custom_error_type="window_form",
        custom_error_message=(
            "A window is {last: N, grain: G}, {to_date: G} or {start: D1, end: D2}"
        ),
    ),
]


# Resolution


@dataclass(frozen=True)
class TimeIssue:
    code: str
    message: str


class WindowError(ValueError):
    """A window that can't be used: `code` is the validator code (R3.*, R4.*)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ResolvedWindow:
    """Inclusive dates aligned to `grain`. An empty window has `end` = `start` - 1 day."""

    start: dt.date
    end: dt.date
    grain: Grain
    partial: bool  # it includes the current, partial period
    warnings: tuple[TimeIssue, ...] = ()

    @property
    def empty(self) -> bool:
        return self.end < self.start

    @property
    def range(self) -> DateRange | None:
        return None if self.empty else DateRange(self.start, self.end)

    def to_json(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "grain": self.grain,
            "partial": self.partial,
        }


def resolve_window(
    window: LastWindow | ToDateWindow | RangeWindow,
    as_of: dt.date,
    block_grain: Grain | None = None,
) -> ResolvedWindow:
    """The dates a window covers on `as_of`. Raises WindowError (R3.window_too_short).

    A period is complete if its last day is before as_of. The current period, which contains
    as_of, is partial: it runs through the day before as_of, like `to_date`.
    """
    yesterday = as_of - ONE_DAY
    if isinstance(window, LastWindow):
        grain = window.grain
        current = period_start(as_of, grain)
        start, end, partial = shift_period(current, grain, -window.last), current - ONE_DAY, False
        if window.include_current and yesterday >= current:
            end, partial = yesterday, True
        return ResolvedWindow(start, end, grain, partial)

    if isinstance(window, ToDateWindow):
        grain = window.to_date
        start = period_start(as_of, grain)
        warnings: tuple[TimeIssue, ...] = ()
        if yesterday < start:
            warnings = (
                TimeIssue(
                    "R3.empty_window",
                    f"{as_of} is the first day of the {grain}, so the {grain} to date is empty.",
                ),
            )
        return ResolvedWindow(start, yesterday, grain, True, warnings)

    return _resolve_range(window, as_of, block_grain)


def _resolve_range(
    window: RangeWindow, as_of: dt.date, block_grain: Grain | None
) -> ResolvedWindow:
    grain: Grain = window.grain or block_grain or "day"
    current = period_start(as_of, grain)
    yesterday = as_of - ONE_DAY

    # Inward to whole periods.
    start = window.start
    if period_start(start, grain) != start:
        start = period_end(start, grain) + ONE_DAY
    end = window.end
    if period_end(end, grain) != end:
        end = period_start(end, grain) - ONE_DAY
    # Complete periods only, unless the current one is asked for and the range covers it
    # through yesterday.
    end = min(end, current - ONE_DAY)
    partial = False
    if (
        window.include_current
        and yesterday >= current
        and window.end >= yesterday
        and start <= current
    ):
        end, partial = yesterday, True

    if start > end:
        raise WindowError(
            "R3.window_too_short",
            f"The window {window.start} to {window.end} holds no complete {grain}"
            + (" before the current one" if window.end >= current else "")
            + ". Use a window that covers at least one whole period, e.g. "
            f"{{last: 1, grain: {grain}}}.",
        )
    warnings: tuple[TimeIssue, ...] = ()
    if (start, end) != (window.start, window.end):
        warnings = (
            TimeIssue(
                "R3.window_snapped",
                f"The window {window.start} to {window.end} was snapped to whole, complete "
                f"{grain}s: {start} to {end}.",
            ),
        )
    return ResolvedWindow(start, end, grain, partial, warnings)


# Grain compatibility (R4)

# time_grain -> the window grains whose periods it divides exactly.
_FITS: dict[Grain, frozenset[Grain]] = {
    "day": frozenset(GRAINS),
    "week": frozenset({"week"}),
    "month": frozenset({"month", "quarter", "year"}),
    "quarter": frozenset({"quarter", "year"}),
    "year": frozenset({"year"}),
}


def grain_fits(time_grain: Grain, window_grain: Grain) -> bool:
    """Whether grouping by `time_grain` splits periods of `window_grain` exactly."""
    return window_grain in _FITS[time_grain]


def check_grain(time_grain: Grain, window: ResolvedWindow) -> None:
    """Raises WindowError (R4.grain_mismatch) if the group-by would cut periods."""
    if not grain_fits(time_grain, window.grain):
        fitting = ", ".join(g for g in GRAINS if grain_fits(time_grain, g))
        raise WindowError(
            "R4.grain_mismatch",
            f"A {time_grain} time grain doesn't divide a window of whole {window.grain}s, so "
            f"the first and last {time_grain}s would hold partial data. Use a window in "
            f"{fitting}s, or a time_grain that divides {window.grain}s.",
        )


# Comparisons (R4)


def comparison_window(
    window: LastWindow | ToDateWindow | RangeWindow,
    resolved: ResolvedWindow,
    compare: Compare,
) -> ResolvedWindow:
    """The previous window to compare `resolved` with. Raises R3.partial_in_comparison."""
    if getattr(window, "include_current", False):
        raise WindowError(
            "R3.partial_in_comparison",
            "A window with include_current: true can't be compared: the current period is "
            "partial and the previous one isn't. Compare a partial period with {to_date: G}, "
            "which aligns both sides, or drop include_current.",
        )
    grain, start, end = resolved.grain, resolved.start, resolved.end
    to_date = isinstance(window, ToDateWindow)

    if compare == "previous_period":
        if to_date:
            prev_start = shift_period(start, grain, -1)
            prev_end = min(prev_start + (end - start), period_end(prev_start, grain))
        else:
            prev_start = shift_period(start, grain, -count_periods(start, end, grain))
            prev_end = start - ONE_DAY
    elif to_date:
        prev_start, prev_end = year_earlier(start), year_earlier(end)
    elif grain in ("day", "week"):
        # 52 weeks keeps weekdays aligned.
        shift = dt.timedelta(days=364)
        prev_start, prev_end = start - shift, end - shift
    else:
        prev_start = year_earlier(start)
        prev_end = period_end(year_earlier(period_start(end, grain)), grain)

    if resolved.empty:
        prev_end = prev_start - ONE_DAY
    return ResolvedWindow(prev_start, prev_end, grain, partial=False)
