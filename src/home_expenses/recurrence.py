"""Recurring-expense missed-period detection."""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from home_expenses.config import Category
from home_expenses.models import Alert, AlertKind, Item


@dataclass(frozen=True)
class _Period:
    label: str  # e.g. "2026-01", "2026-MA", "2026-Q2", "2026"
    start: date
    end: date


def check_recurrence(
    *,
    items: Iterable[Item],
    categories: Mapping[str, Category],
    statement_range: tuple[date, date],
    today: date,
) -> list[Alert]:
    items = list(items)
    alerts: list[Alert] = []
    for name, cat in categories.items():
        if cat.recurrence == "none":
            continue
        for period in _periods_for(
            cat.recurrence,
            statement_range[0],
            statement_range[1],
            today,
        ):
            count = sum(
                1
                for it in items
                if it.category == name and period.start <= it.transaction.date <= period.end
            )
            if count == 0:
                alerts.append(
                    Alert(
                        kind=AlertKind.RECURRING_MISSED,
                        message=(f"recurring category '{name}' has no items in {period.label}"),
                        payload={"category": name, "period": period.label},
                    )
                )
    return alerts


def _periods_for(
    recurrence: str,
    range_start: date,
    range_end: date,
    today: date,
) -> Iterable[_Period]:
    if recurrence == "monthly":
        yield from _monthly_periods(range_start, range_end, today)
    elif recurrence == "bimonthly":
        yield from _bimonthly_periods(range_start, range_end, today)
    elif recurrence == "quarterly":
        yield from _quarterly_periods(range_start, range_end, today)
    elif recurrence == "yearly":
        yield from _yearly_periods(range_start, range_end, today)


def _is_complete(period_end: date, today: date) -> bool:
    return period_end < today


def _overlaps(p_start: date, p_end: date, r_start: date, r_end: date) -> bool:
    return p_start <= r_end and p_end >= r_start


def _monthly_periods(range_start: date, range_end: date, today: date) -> Iterable[_Period]:
    y, m = range_start.year, range_start.month
    while date(y, m, 1) <= range_end:
        last = calendar.monthrange(y, m)[1]
        start, end = date(y, m, 1), date(y, m, last)
        if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
            yield _Period(label=f"{y:04d}-{m:02d}", start=start, end=end)
        m += 1
        if m == 13:
            m = 1
            y += 1


def _bimonthly_periods(range_start: date, range_end: date, today: date) -> Iterable[_Period]:
    pairs = [
        ("JF", 1, 2),
        ("MA", 3, 4),
        ("MJ", 5, 6),
        ("JA", 7, 8),
        ("SO", 9, 10),
        ("ND", 11, 12),
    ]
    for y in range(range_start.year, range_end.year + 1):
        for label, m1, m2 in pairs:
            last = calendar.monthrange(y, m2)[1]
            start, end = date(y, m1, 1), date(y, m2, last)
            if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
                yield _Period(label=f"{y:04d}-{label}", start=start, end=end)


def _quarterly_periods(range_start: date, range_end: date, today: date) -> Iterable[_Period]:
    quarters = [(1, 1, 3, 31), (2, 4, 6, 30), (3, 7, 9, 30), (4, 10, 12, 31)]
    for y in range(range_start.year, range_end.year + 1):
        for q, m1, m2, d2 in quarters:
            start, end = date(y, m1, 1), date(y, m2, d2)
            if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
                yield _Period(label=f"{y:04d}-Q{q}", start=start, end=end)


def _yearly_periods(range_start: date, range_end: date, today: date) -> Iterable[_Period]:
    for y in range(range_start.year, range_end.year + 1):
        start, end = date(y, 1, 1), date(y, 12, 31)
        if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
            yield _Period(label=f"{y:04d}", start=start, end=end)
