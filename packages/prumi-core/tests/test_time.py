"""Window resolution, grain compatibility and comparison windows (spec section 5, AC 7, 8)."""

import datetime as dt

import pytest
from prumi.time import (
    GRAINS,
    LastWindow,
    RangeWindow,
    ToDateWindow,
    Window,
    WindowError,
    check_grain,
    comparison_window,
    grain_fits,
    period_end,
    period_start,
    resolve_window,
)
from pydantic import TypeAdapter, ValidationError

AS_OF = dt.date(2026, 10, 15)  # a Thursday; the current week starts Monday 2026-10-12
WINDOW = TypeAdapter(Window)


def d(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def window(value: dict):
    return WINDOW.validate_python(value)


# Periods


@pytest.mark.parametrize(
    ("day", "grain", "start", "end"),
    [
        ("2026-10-15", "day", "2026-10-15", "2026-10-15"),
        ("2026-10-15", "week", "2026-10-12", "2026-10-18"),  # ISO: Monday to Sunday
        ("2026-10-18", "week", "2026-10-12", "2026-10-18"),  # a Sunday
        ("2026-10-12", "week", "2026-10-12", "2026-10-18"),  # a Monday
        ("2027-01-01", "week", "2026-12-28", "2027-01-03"),  # across years
        ("2026-10-15", "month", "2026-10-01", "2026-10-31"),
        ("2028-02-10", "month", "2028-02-01", "2028-02-29"),
        ("2026-10-15", "quarter", "2026-10-01", "2026-12-31"),
        ("2026-05-31", "quarter", "2026-04-01", "2026-06-30"),
        ("2026-10-15", "year", "2026-01-01", "2026-12-31"),
    ],
)
def test_periods(day, grain, start, end):
    assert period_start(d(day), grain) == d(start)
    assert period_end(d(day), grain) == d(end)


# Window resolution (AC 7)


@pytest.mark.parametrize(
    ("form", "block_grain", "start", "end", "grain", "partial", "warning"),
    [
        # {last: N, grain: G}: N complete periods before the current one.
        ({"last": 7, "grain": "day"}, None, "2026-10-08", "2026-10-14", "day", False, None),
        ({"last": 12, "grain": "week"}, None, "2026-07-20", "2026-10-11", "week", False, None),
        ({"last": 1, "grain": "week"}, None, "2026-10-05", "2026-10-11", "week", False, None),
        ({"last": 3, "grain": "month"}, None, "2026-07-01", "2026-09-30", "month", False, None),
        ({"last": 24, "grain": "month"}, None, "2024-10-01", "2026-09-30", "month", False, None),
        ({"last": 2, "grain": "quarter"}, None, "2026-04-01", "2026-09-30", "quarter", False, None),
        ({"last": 1, "grain": "year"}, None, "2025-01-01", "2025-12-31", "year", False, None),
        # include_current adds the current period, through the day before as_of (flagged).
        (
            {"last": 3, "grain": "month", "include_current": True},
            None,
            "2026-07-01",
            "2026-10-14",
            "month",
            True,
            None,
        ),
        (
            {"last": 2, "grain": "week", "include_current": True},
            None,
            "2026-09-28",
            "2026-10-14",
            "week",
            True,
            None,
        ),
        # A day's current period is today, which has no complete day yet.
        (
            {"last": 2, "grain": "day", "include_current": True},
            None,
            "2026-10-13",
            "2026-10-14",
            "day",
            False,
            None,
        ),
        # {to_date: G}: start of the current period to the day before as_of.
        ({"to_date": "week"}, None, "2026-10-12", "2026-10-14", "week", True, None),
        ({"to_date": "month"}, None, "2026-10-01", "2026-10-14", "month", True, None),
        ({"to_date": "quarter"}, None, "2026-10-01", "2026-10-14", "quarter", True, None),
        ({"to_date": "year"}, None, "2026-01-01", "2026-10-14", "year", True, None),
        # Explicit dates: inward to whole periods of G (default: block grain, else day).
        (
            {"start": "2026-03-03", "end": "2026-03-09"},
            None,
            "2026-03-03",
            "2026-03-09",
            "day",
            False,
            None,
        ),
        (
            {"start": "2026-01-01", "end": "2026-06-30", "grain": "month"},
            None,
            "2026-01-01",
            "2026-06-30",
            "month",
            False,
            None,
        ),
        (
            {"start": "2026-01-15", "end": "2026-06-20", "grain": "month"},
            None,
            "2026-02-01",
            "2026-05-31",
            "month",
            False,
            "R3.window_snapped",
        ),
        (
            {"start": "2026-01-15", "end": "2026-06-20"},
            "month",
            "2026-02-01",
            "2026-05-31",
            "month",
            False,
            "R3.window_snapped",
        ),
        (
            {"start": "2026-09-02", "end": "2026-10-04", "grain": "week"},
            None,
            "2026-09-07",
            "2026-10-04",
            "week",
            False,
            "R3.window_snapped",
        ),
        (
            {"start": "2026-01-01", "end": "2026-06-30", "grain": "quarter"},
            None,
            "2026-01-01",
            "2026-06-30",
            "quarter",
            False,
            None,
        ),
        # Periods that aren't complete are dropped...
        (
            {"start": "2026-07-01", "end": "2026-12-31", "grain": "month"},
            None,
            "2026-07-01",
            "2026-09-30",
            "month",
            False,
            "R3.window_snapped",
        ),
        (
            {"start": "2026-10-01", "end": "2026-10-20"},
            None,
            "2026-10-01",
            "2026-10-14",
            "day",
            False,
            "R3.window_snapped",
        ),
        # ...unless include_current: then the current one is kept through the day before as_of.
        (
            {"start": "2026-07-01", "end": "2026-10-31", "grain": "month", "include_current": True},
            None,
            "2026-07-01",
            "2026-10-14",
            "month",
            True,
            "R3.window_snapped",
        ),
        (
            {"start": "2026-07-01", "end": "2026-10-14", "grain": "month", "include_current": True},
            None,
            "2026-07-01",
            "2026-10-14",
            "month",
            True,
            None,
        ),
        # The range stops before yesterday, so the current month isn't covered: dropped.
        (
            {"start": "2026-07-01", "end": "2026-10-10", "grain": "month", "include_current": True},
            None,
            "2026-07-01",
            "2026-09-30",
            "month",
            False,
            "R3.window_snapped",
        ),
    ],
)
def test_resolve_window(form, block_grain, start, end, grain, partial, warning):
    resolved = resolve_window(window(form), AS_OF, block_grain)
    assert (resolved.start, resolved.end) == (d(start), d(end))
    assert (resolved.grain, resolved.partial) == (grain, partial)
    assert [w.code for w in resolved.warnings] == ([warning] if warning else [])
    assert not resolved.empty


def test_to_date_on_the_first_day_of_a_period_is_empty():
    resolved = resolve_window(ToDateWindow(to_date="month"), d("2026-10-01"))
    assert resolved.empty and resolved.range is None
    assert (resolved.start, resolved.partial) == (d("2026-10-01"), True)
    assert [w.code for w in resolved.warnings] == ["R3.empty_window"]


@pytest.mark.parametrize(
    "form",
    [
        {"start": "2026-01-15", "end": "2026-02-10", "grain": "month"},
        {"start": "2026-10-13", "end": "2026-10-14", "grain": "week"},  # the current week
        {"start": "2026-10-01", "end": "2026-12-31", "grain": "quarter"},  # only the current one
        {"start": "2026-10-15", "end": "2026-10-20"},  # today and later
    ],
)
def test_window_too_short(form):
    with pytest.raises(WindowError) as error:
        resolve_window(window(form), AS_OF)
    assert error.value.code == "R3.window_too_short"


@pytest.mark.parametrize(
    "value",
    [
        {"last": 0, "grain": "week"},
        {"last": 401, "grain": "week"},
        {"last": 3, "grain": "fortnight"},
        {"last": 3},
        {"to_date": "month", "include_current": True},
        {"start": "2026-02-01", "end": "2026-01-01"},
        {"window": "last 3 weeks"},
        "last 3 weeks",
    ],
)
def test_invalid_window_forms(value):
    with pytest.raises(ValidationError):
        window(value)


def test_window_forms_parse_to_their_models():
    assert isinstance(window({"last": 400, "grain": "day"}), LastWindow)
    assert isinstance(window({"to_date": "year"}), ToDateWindow)
    assert window({"start": "2026-01-01", "end": "2026-01-31"}) == RangeWindow(
        start=d("2026-01-01"), end=d("2026-01-31")
    )


# Grain compatibility (AC 7)

FITS = {
    # time_grain: window grains it divides exactly
    "day": {"day", "week", "month", "quarter", "year"},
    "week": {"week"},
    "month": {"month", "quarter", "year"},
    "quarter": {"quarter", "year"},
    "year": {"year"},
}


@pytest.mark.parametrize("time_grain", GRAINS)
@pytest.mark.parametrize("window_grain", GRAINS)
def test_grain_compatibility(time_grain, window_grain):
    expected = window_grain in FITS[time_grain]
    assert grain_fits(time_grain, window_grain) is expected
    resolved = resolve_window(LastWindow(last=2, grain=window_grain), AS_OF)
    if expected:
        check_grain(time_grain, resolved)
    else:
        with pytest.raises(WindowError) as error:
            check_grain(time_grain, resolved)
        assert error.value.code == "R4.grain_mismatch"


# Comparison windows (AC 8)


@pytest.mark.parametrize(
    ("form", "as_of", "compare", "start", "end"),
    [
        # previous_period, last / explicit: the same number of periods immediately before.
        ({"last": 7, "grain": "day"}, AS_OF, "previous_period", "2026-10-01", "2026-10-07"),
        ({"last": 12, "grain": "week"}, AS_OF, "previous_period", "2026-04-27", "2026-07-19"),
        ({"last": 3, "grain": "month"}, AS_OF, "previous_period", "2026-04-01", "2026-06-30"),
        ({"last": 2, "grain": "quarter"}, AS_OF, "previous_period", "2025-10-01", "2026-03-31"),
        ({"last": 1, "grain": "year"}, AS_OF, "previous_period", "2024-01-01", "2024-12-31"),
        (
            {"start": "2026-03-03", "end": "2026-03-09"},
            AS_OF,
            "previous_period",
            "2026-02-24",
            "2026-03-02",
        ),
        (
            {"start": "2026-01-15", "end": "2026-06-20", "grain": "month"},  # snapped to Feb-May
            AS_OF,
            "previous_period",
            "2025-10-01",
            "2026-01-31",
        ),
        # previous_period, to_date: the same day offsets, clamped to the period's end.
        ({"to_date": "month"}, AS_OF, "previous_period", "2026-09-01", "2026-09-14"),
        ({"to_date": "month"}, d("2026-03-31"), "previous_period", "2026-02-01", "2026-02-28"),
        ({"to_date": "month"}, d("2028-03-31"), "previous_period", "2028-02-01", "2028-02-29"),
        ({"to_date": "week"}, AS_OF, "previous_period", "2026-10-05", "2026-10-07"),
        ({"to_date": "quarter"}, AS_OF, "previous_period", "2026-07-01", "2026-07-14"),
        ({"to_date": "year"}, AS_OF, "previous_period", "2025-01-01", "2025-10-14"),
        # previous_year, day and week: shifted 364 days (weekdays stay aligned).
        ({"last": 7, "grain": "day"}, AS_OF, "previous_year", "2025-10-09", "2025-10-15"),
        ({"last": 12, "grain": "week"}, AS_OF, "previous_year", "2025-07-21", "2025-10-12"),
        (
            {"start": "2026-03-03", "end": "2026-03-09"},
            AS_OF,
            "previous_year",
            "2025-03-04",
            "2025-03-10",
        ),
        # previous_year, month / quarter / year: the same calendar periods a year earlier.
        ({"last": 3, "grain": "month"}, AS_OF, "previous_year", "2025-07-01", "2025-09-30"),
        (
            {"last": 1, "grain": "month"},
            d("2028-03-15"),
            "previous_year",
            "2027-02-01",
            "2027-02-28",
        ),
        ({"last": 2, "grain": "quarter"}, AS_OF, "previous_year", "2025-04-01", "2025-09-30"),
        ({"last": 1, "grain": "year"}, AS_OF, "previous_year", "2024-01-01", "2024-12-31"),
        # previous_year, to_date: the same dates a year earlier; Feb 29 becomes Feb 28.
        ({"to_date": "month"}, AS_OF, "previous_year", "2025-10-01", "2025-10-14"),
        ({"to_date": "year"}, AS_OF, "previous_year", "2025-01-01", "2025-10-14"),
    ],
)
def test_comparison_windows(form, as_of, compare, start, end):
    model = window(form)
    resolved = resolve_window(model, as_of)
    previous = comparison_window(model, resolved, compare)
    assert (previous.start, previous.end) == (d(start), d(end))
    assert previous.grain == resolved.grain
    assert not previous.partial


def test_feb_29_to_date_compared_with_the_previous_year():
    # as_of 2028-03-01: the year to date ends on Feb 29, which 2027 doesn't have.
    model = ToDateWindow(to_date="year")
    resolved = resolve_window(model, d("2028-03-01"))
    assert (resolved.start, resolved.end) == (d("2028-01-01"), d("2028-02-29"))
    previous = comparison_window(model, resolved, "previous_year")
    assert (previous.start, previous.end) == (d("2027-01-01"), d("2027-02-28"))


def test_an_empty_to_date_window_compares_with_an_empty_window():
    model = ToDateWindow(to_date="month")
    resolved = resolve_window(model, d("2026-10-01"))
    for compare in ("previous_period", "previous_year"):
        assert comparison_window(model, resolved, compare).empty


@pytest.mark.parametrize(
    "form",
    [
        {"last": 3, "grain": "month", "include_current": True},
        {"start": "2026-07-01", "end": "2026-10-31", "grain": "month", "include_current": True},
    ],
)
def test_include_current_cannot_be_compared(form):
    model = window(form)
    with pytest.raises(WindowError) as error:
        comparison_window(model, resolve_window(model, AS_OF), "previous_period")
    assert error.value.code == "R3.partial_in_comparison"
