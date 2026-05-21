from __future__ import annotations

from datetime import date

from home_expenses.models import Alert, AlertKind, ReportCategory
from home_expenses.report.model import build_report_model
from tests.factories import make_item, make_transaction


def _cats(*entries: tuple[str, str, bool]) -> dict[str, ReportCategory]:
    """Build a categories_by_name dict from (name, recurrence, pcp) tuples."""
    return {
        name: ReportCategory(name=name, recurrence=rec, period_contains_payment=pcp)
        for name, rec, pcp in entries
    }


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
        categories_by_name=_cats(("test_category", "monthly", False)),
    )
    assert model.years == (2026, 2025, 2024)


def test_currency_propagated() -> None:
    model = build_report_model(
        items=[],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={},
    )
    assert model.currency == "EUR"


def test_items_preserved() -> None:
    items = [make_item(category="a"), make_item(category="b")]
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name=_cats(("a", "monthly", False), ("b", "monthly", False)),
    )
    categories = [it.category for it in model.items]
    assert categories == ["a", "b"]


def test_alerts_preserved() -> None:
    alerts = [Alert(kind=AlertKind.UNCLASSIFIED_EXPENSE, message="x")]
    model = build_report_model(
        items=[],
        alerts=alerts,
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={},
    )
    assert len(model.alerts) == 1


def test_categories_by_name_carries_period_contains_payment() -> None:
    model = build_report_model(
        items=[],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name=_cats(("fee", "yearly", True)),
    )
    rc = model.categories_by_name["fee"]
    assert rc.name == "fee"
    assert rc.recurrence == "yearly"
    assert rc.period_contains_payment is True
