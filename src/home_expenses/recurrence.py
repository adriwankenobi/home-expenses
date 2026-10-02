"""Recurring-expense missed-period detection."""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from home_expenses.config import Category
from home_expenses.models import Alert, AlertKind, Item


@dataclass(frozen=True)
class Period:
    label: str  # e.g. "2026-01", "2026-MA", "2026-Q2", "2026"
    start: date
    end: date


_BIMONTHLY_PAIRS: tuple[tuple[str, int, int], ...] = (
    ("JF", 1, 2),
    ("MA", 3, 4),
    ("MJ", 5, 6),
    ("JA", 7, 8),
    ("SO", 9, 10),
    ("ND", 11, 12),
)


def period_for(d: date, recurrence: str) -> Period:
    """Return the period of `recurrence` granularity that contains `d`."""
    if recurrence == "monthly":
        last = calendar.monthrange(d.year, d.month)[1]
        return Period(
            label=f"{d.year:04d}-{d.month:02d}",
            start=date(d.year, d.month, 1),
            end=date(d.year, d.month, last),
        )
    if recurrence == "bimonthly":
        for label, m1, m2 in _BIMONTHLY_PAIRS:
            if d.month in (m1, m2):
                last = calendar.monthrange(d.year, m2)[1]
                return Period(
                    label=f"{d.year:04d}-{label}",
                    start=date(d.year, m1, 1),
                    end=date(d.year, m2, last),
                )
        raise AssertionError("unreachable")  # pragma: no cover
    if recurrence == "quarterly":
        q = (d.month - 1) // 3 + 1
        m1 = (q - 1) * 3 + 1
        m2 = m1 + 2
        last = calendar.monthrange(d.year, m2)[1]
        return Period(
            label=f"{d.year:04d}-Q{q}",
            start=date(d.year, m1, 1),
            end=date(d.year, m2, last),
        )
    if recurrence == "yearly":
        return Period(
            label=f"{d.year:04d}",
            start=date(d.year, 1, 1),
            end=date(d.year, 12, 31),
        )
    raise ValueError(f"period_for: unsupported recurrence {recurrence!r}")


def assigned_period(d: date, recurrence: str, edge_days: int = 0) -> Period:
    """Return the period a payment dated `d` is attributed to.

    Normally that is the period containing `d`. When `edge_days > 0`, a
    payment landing in the last `edge_days` days of its containing period is
    attributed to the *following* period instead — the "charged a day or two
    early for next period" case.

    The shift is unconditional: it does not look at what other payments
    exist. A payment genuinely belonging to its own period but dated inside
    the edge window is pushed forward too, leaving its own period empty and
    surfacing a RECURRING_MISSED alert. That is the intended feedback loop.

    The JS ``syntheticPeriodFor`` in the report template mirrors this shift;
    keep the two in sync.
    """
    p = period_for(d, recurrence)
    if edge_days > 0 and (p.end - d).days < edge_days:
        return period_for(p.end + timedelta(days=1), recurrence)
    return p


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
            if cat.start_date is not None and period.end < cat.start_date:
                continue
            count = sum(
                1
                for it in items
                # A refund is not a payment, so it cannot cover a period.
                if not it.transaction.is_credit
                and it.category == name
                and assigned_period(it.transaction.date, cat.recurrence, cat.period_edge_days).label
                == period.label
            )
            if count == 0:
                alerts.append(
                    Alert(
                        kind=AlertKind.RECURRING_MISSED,
                        message=(f"recurring category '{cat.name}' has no items in {period.label}"),
                        payload={"category": name, "period": period.label},
                    )
                )
    return alerts


def _periods_for(
    recurrence: str,
    range_start: date,
    range_end: date,
    today: date,
) -> Iterable[Period]:
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


def _monthly_periods(range_start: date, range_end: date, today: date) -> Iterable[Period]:
    y, m = range_start.year, range_start.month
    while date(y, m, 1) <= range_end:
        last = calendar.monthrange(y, m)[1]
        start, end = date(y, m, 1), date(y, m, last)
        if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
            yield Period(label=f"{y:04d}-{m:02d}", start=start, end=end)
        m += 1
        if m == 13:
            m = 1
            y += 1


def _bimonthly_periods(range_start: date, range_end: date, today: date) -> Iterable[Period]:
    for y in range(range_start.year, range_end.year + 1):
        for label, m1, m2 in _BIMONTHLY_PAIRS:
            last = calendar.monthrange(y, m2)[1]
            start, end = date(y, m1, 1), date(y, m2, last)
            if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
                yield Period(label=f"{y:04d}-{label}", start=start, end=end)


def _quarterly_periods(range_start: date, range_end: date, today: date) -> Iterable[Period]:
    quarters = [(1, 1, 3, 31), (2, 4, 6, 30), (3, 7, 9, 30), (4, 10, 12, 31)]
    for y in range(range_start.year, range_end.year + 1):
        for q, m1, m2, d2 in quarters:
            start, end = date(y, m1, 1), date(y, m2, d2)
            if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
                yield Period(label=f"{y:04d}-Q{q}", start=start, end=end)


def _yearly_periods(range_start: date, range_end: date, today: date) -> Iterable[Period]:
    for y in range(range_start.year, range_end.year + 1):
        start, end = date(y, 1, 1), date(y, 12, 31)
        if _is_complete(end, today) and _overlaps(start, end, range_start, range_end):
            yield Period(label=f"{y:04d}", start=start, end=end)
