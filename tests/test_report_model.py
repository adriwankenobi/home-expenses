from __future__ import annotations

from datetime import date

from home_expenses.models import Alert, AlertKind
from home_expenses.report.model import build_report_model
from tests.factories import make_item, make_transaction


def test_years_are_unique_and_sorted_descending() -> None:
    items = [
        make_item(transaction=make_transaction(date=date(2024, 6, 1))),
        make_item(transaction=make_transaction(date=date(2026, 6, 1))),
        make_item(transaction=make_transaction(date=date(2025, 6, 1))),
        make_item(transaction=make_transaction(date=date(2026, 1, 1))),
    ]
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
    )
    assert model.years == (2026, 2025, 2024)


def test_currency_propagated() -> None:
    model = build_report_model(
        items=[],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
    )
    assert model.currency == "EUR"


def test_items_preserved() -> None:
    items = [make_item(category="a"), make_item(category="b")]
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
    )
    categories = [it.category for it in model.items]
    assert categories == ["a", "b"]


def test_alerts_preserved() -> None:
    alerts = [
        Alert(kind=AlertKind.UNCLASSIFIED_EXPENSE, message="x"),
    ]
    model = build_report_model(
        items=[],
        alerts=alerts,
        currency="EUR",
        generated_at=date(2026, 5, 19),
    )
    assert len(model.alerts) == 1
