from __future__ import annotations

from datetime import date

import pytest

from home_expenses.config import Category, Recurrence
from home_expenses.models import AlertKind, Item
from home_expenses.recurrence import Period, check_recurrence, period_for
from tests.factories import make_item, make_transaction


def _cat(name: str, recurrence: Recurrence) -> Category:
    return Category(name=name, recurrence=recurrence)


def test_monthly_missing_period_alerts() -> None:
    items = [
        make_item(
            transaction=make_transaction(date=date(2026, 1, 10)),
            category="elec",
        ),
        # no item in Feb 2026
        make_item(
            transaction=make_transaction(date=date(2026, 3, 5)),
            category="elec",
        ),
    ]
    alerts = check_recurrence(
        items=items,
        categories={"elec": _cat("elec", "monthly")},
        statement_range=(date(2026, 1, 1), date(2026, 3, 31)),
        today=date(2026, 4, 1),
    )
    assert len(alerts) == 1
    assert alerts[0].kind is AlertKind.RECURRING_MISSED
    assert alerts[0].payload["period"] == "2026-02"


def test_current_period_not_judged() -> None:
    # April 2026 is the current month — no alert even if empty.
    items = [
        make_item(
            transaction=make_transaction(date=date(2026, 1, 10)),
            category="elec",
        )
    ]
    alerts = check_recurrence(
        items=items,
        categories={"elec": _cat("elec", "monthly")},
        statement_range=(date(2026, 1, 1), date(2026, 4, 15)),
        today=date(2026, 4, 15),
    )
    assert all(a.payload.get("period") != "2026-04" for a in alerts)


def test_no_alerts_outside_statement_range() -> None:
    items: list[Item] = []
    alerts = check_recurrence(
        items=items,
        categories={"elec": _cat("elec", "monthly")},
        statement_range=(date(2026, 3, 1), date(2026, 3, 31)),
        today=date(2026, 5, 1),
    )
    # Only March is in range; March is complete; no items.
    assert len(alerts) == 1
    assert alerts[0].payload["period"] == "2026-03"


def test_recurrence_none_never_alerts() -> None:
    alerts = check_recurrence(
        items=[],
        categories={"x": _cat("x", "none")},
        statement_range=(date(2024, 1, 1), date(2026, 12, 31)),
        today=date(2027, 1, 1),
    )
    assert alerts == []


def test_bimonthly_grid() -> None:
    items = [
        make_item(
            transaction=make_transaction(date=date(2026, 1, 15)),
            category="water",
        )
    ]
    alerts = check_recurrence(
        items=items,
        categories={"water": _cat("water", "bimonthly")},
        statement_range=(date(2026, 1, 1), date(2026, 6, 30)),
        today=date(2026, 7, 1),
    )
    # Jan-Feb satisfied; Mar-Apr missing; May-Jun missing.
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2026-MA", "2026-MJ"]


def test_yearly() -> None:
    items = [
        make_item(
            transaction=make_transaction(date=date(2024, 3, 1)),
            category="tax",
        )
    ]
    alerts = check_recurrence(
        items=items,
        categories={"tax": _cat("tax", "yearly")},
        statement_range=(date(2023, 1, 1), date(2025, 12, 31)),
        today=date(2026, 1, 1),
    )
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2023", "2025"]


def test_start_date_suppresses_alerts_before_it() -> None:
    # Statement range covers Jan-Apr 2026. Category started in March, so
    # Jan and Feb must NOT alert; March alerts (no item in March).
    cat = Category(
        name="elec",
        recurrence="monthly",
        start_date=date(2026, 3, 1),
    )
    alerts = check_recurrence(
        items=[],
        categories={"elec": cat},
        statement_range=(date(2026, 1, 1), date(2026, 4, 30)),
        today=date(2026, 5, 1),
    )
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2026-03", "2026-04"]


def test_no_start_date_means_no_filter() -> None:
    cat = Category(name="elec", recurrence="monthly")
    alerts = check_recurrence(
        items=[],
        categories={"elec": cat},
        statement_range=(date(2026, 1, 1), date(2026, 2, 28)),
        today=date(2026, 3, 1),
    )
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2026-01", "2026-02"]


def test_period_for_monthly_midmonth() -> None:
    p = period_for(date(2026, 3, 17), "monthly")
    assert p == Period(label="2026-03", start=date(2026, 3, 1), end=date(2026, 3, 31))


def test_period_for_bimonthly_pairs_march_april() -> None:
    p = period_for(date(2026, 4, 5), "bimonthly")
    assert p == Period(label="2026-MA", start=date(2026, 3, 1), end=date(2026, 4, 30))


def test_period_for_quarterly_q3() -> None:
    p = period_for(date(2026, 8, 15), "quarterly")
    assert p == Period(label="2026-Q3", start=date(2026, 7, 1), end=date(2026, 9, 30))


def test_period_for_yearly() -> None:
    p = period_for(date(2026, 12, 31), "yearly")
    assert p == Period(label="2026", start=date(2026, 1, 1), end=date(2026, 12, 31))


def test_period_for_rejects_none() -> None:
    with pytest.raises(ValueError, match="recurrence"):
        period_for(date(2026, 1, 1), "none")


# --- period_edge_days: trailing-edge attribution ---


def _edge_cat(name: str, recurrence: Recurrence, edge_days: int) -> Category:
    return Category(name=name, recurrence=recurrence, period_edge_days=edge_days)


def test_edge_charge_counts_toward_next_period() -> None:
    # May's fee was charged early, on the last day of April. With a 2-day
    # trailing edge it counts toward May, so neither month looks empty.
    items = [
        make_item(transaction=make_transaction(date=date(2026, 4, 5)), category="fee"),
        make_item(transaction=make_transaction(date=date(2026, 4, 30)), category="fee"),
        make_item(transaction=make_transaction(date=date(2026, 6, 5)), category="fee"),
    ]
    alerts = check_recurrence(
        items=items,
        categories={"fee": _edge_cat("fee", "monthly", 2)},
        statement_range=(date(2026, 4, 1), date(2026, 6, 30)),
        today=date(2026, 8, 20),
    )
    assert alerts == []


def test_edge_charge_leaves_its_own_period_empty() -> None:
    # The shift is unconditional: a lone charge on the last day of April
    # counts toward May, so April is reported missing.
    items = [
        make_item(transaction=make_transaction(date=date(2026, 4, 30)), category="fee"),
    ]
    alerts = check_recurrence(
        items=items,
        categories={"fee": _edge_cat("fee", "monthly", 2)},
        statement_range=(date(2026, 4, 1), date(2026, 5, 31)),
        today=date(2026, 8, 20),
    )
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2026-04"]


def test_charge_just_outside_edge_window_counts_normally() -> None:
    # April 28 is 2 full days before the period end — outside a 2-day edge.
    items = [
        make_item(transaction=make_transaction(date=date(2026, 4, 5)), category="fee"),
        make_item(transaction=make_transaction(date=date(2026, 4, 28)), category="fee"),
    ]
    alerts = check_recurrence(
        items=items,
        categories={"fee": _edge_cat("fee", "monthly", 2)},
        statement_range=(date(2026, 4, 1), date(2026, 5, 31)),
        today=date(2026, 8, 20),
    )
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2026-05"]


def test_edge_window_is_trailing_only() -> None:
    # A charge on the FIRST day of May stays in May; the window never
    # reaches backwards into the previous period.
    items = [
        make_item(transaction=make_transaction(date=date(2026, 5, 1)), category="fee"),
    ]
    alerts = check_recurrence(
        items=items,
        categories={"fee": _edge_cat("fee", "monthly", 2)},
        statement_range=(date(2026, 4, 1), date(2026, 5, 31)),
        today=date(2026, 8, 20),
    )
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2026-04"]


def test_edge_days_zero_keeps_plain_date_attribution() -> None:
    items = [
        make_item(transaction=make_transaction(date=date(2026, 4, 5)), category="fee"),
        make_item(transaction=make_transaction(date=date(2026, 4, 30)), category="fee"),
    ]
    alerts = check_recurrence(
        items=items,
        categories={"fee": _edge_cat("fee", "monthly", 0)},
        statement_range=(date(2026, 4, 1), date(2026, 5, 31)),
        today=date(2026, 8, 20),
    )
    periods = sorted(a.payload["period"] for a in alerts)
    assert periods == ["2026-05"]


def test_edge_charge_shifts_quarterly_period_across_year_boundary() -> None:
    # Dec 31 with a 2-day edge belongs to Q1 of the following year.
    items = [
        make_item(transaction=make_transaction(date=date(2026, 10, 5)), category="fee"),
        make_item(transaction=make_transaction(date=date(2026, 12, 31)), category="fee"),
    ]
    alerts = check_recurrence(
        items=items,
        categories={"fee": _edge_cat("fee", "quarterly", 2)},
        statement_range=(date(2026, 10, 1), date(2027, 3, 31)),
        today=date(2027, 8, 20),
    )
    assert alerts == []
