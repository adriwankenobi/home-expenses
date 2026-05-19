from __future__ import annotations

from datetime import date

from home_expenses.config import Category, Recurrence
from home_expenses.models import AlertKind, Item
from home_expenses.recurrence import check_recurrence
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
