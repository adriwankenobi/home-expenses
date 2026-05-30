"""Unit tests for the merged Period column formatter (`format_period`)."""

from __future__ import annotations

from datetime import date

from home_expenses.report.render import format_period


def test_full_calendar_year() -> None:
    assert format_period(date(2025, 1, 1), date(2025, 12, 31)) == "2025"


def test_full_calendar_month_31_days() -> None:
    assert format_period(date(2025, 1, 1), date(2025, 1, 31)) == "January 2025"


def test_full_calendar_month_30_days() -> None:
    assert format_period(date(2026, 4, 1), date(2026, 4, 30)) == "April 2026"


def test_full_february_non_leap() -> None:
    assert format_period(date(2025, 2, 1), date(2025, 2, 28)) == "February 2025"


def test_full_february_leap() -> None:
    assert format_period(date(2024, 2, 1), date(2024, 2, 29)) == "February 2024"


def test_partial_range_within_month() -> None:
    assert format_period(date(2025, 3, 13), date(2025, 3, 23)) == "13/03/2025 to 23/03/2025"


def test_partial_range_across_months() -> None:
    assert format_period(date(2025, 3, 13), date(2025, 5, 23)) == "13/03/2025 to 23/05/2025"


def test_single_day() -> None:
    assert format_period(date(2025, 3, 13), date(2025, 3, 13)) == "13/03/2025"


def test_almost_full_month_is_a_range() -> None:
    # start on the 2nd, not the 1st -> not a calendar month
    assert format_period(date(2025, 1, 2), date(2025, 1, 31)) == "02/01/2025 to 31/01/2025"


def test_calendar_aligned_multi_month_is_a_range() -> None:
    # A full quarter is NOT a single month nor a full year -> explicit range
    assert format_period(date(2025, 1, 1), date(2025, 3, 31)) == "01/01/2025 to 31/03/2025"


def test_missing_period_returns_long_dash() -> None:
    assert format_period(None, None) == "—"
    assert format_period(date(2025, 1, 1), None) == "—"
    assert format_period(None, date(2025, 1, 31)) == "—"
