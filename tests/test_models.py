from __future__ import annotations

from datetime import date
from decimal import Decimal

from home_expenses.models import (
    Alert,
    AlertKind,
    Invoice,
    InvoicePeriod,
    Item,
    Transaction,
)


def test_invoice_period_holds_start_and_end() -> None:
    period = InvoicePeriod(start=date(2024, 5, 1), end=date(2024, 5, 31))
    assert period.start.month == 5
    assert period.end.day == 31


def test_invoice_dataclass_fields() -> None:
    inv = Invoice(
        source_path="/tmp/x.pdf",
        content_hash="abc",
        parser="pepeenergy",
        amount=Decimal("11.11"),
        invoice_id="F-001",
        invoice_date=date(2024, 7, 15),
        period=InvoicePeriod(start=date(2024, 5, 1), end=date(2024, 5, 31)),
    )
    assert inv.amount == Decimal("11.11")
    assert inv.parser == "pepeenergy"


def test_transaction_uses_positive_amount() -> None:
    txn = Transaction(
        date=date(2026, 5, 18),
        description="RECIBO PEPE ENERGY",
        amount=Decimal("11.11"),
    )
    assert txn.amount > 0  # parser strips sign


def test_item_optional_invoice() -> None:
    txn = Transaction(date=date(2026, 5, 18), description="X", amount=Decimal("1"))
    item = Item(transaction=txn, category="electricity", invoice=None)
    assert item.invoice is None
    assert item.category == "electricity"


def test_alert_kind_enum() -> None:
    assert AlertKind.UNCLASSIFIED_EXPENSE.value == "unclassified_expense"
    assert AlertKind.RECURRING_MISSED.value == "recurring_missed"


def test_alert_dataclass() -> None:
    alert = Alert(kind=AlertKind.UNCLASSIFIED_EXPENSE, message="x", payload={"a": 1})
    assert alert.kind is AlertKind.UNCLASSIFIED_EXPENSE


def test_alertkind_has_split_values() -> None:
    from home_expenses.models import AlertKind

    assert AlertKind.AMBIGUOUS_SPLIT_BUCKET.value == "ambiguous_split_bucket"
    assert AlertKind.SPLIT_AMOUNT_TIE.value == "split_amount_tie"
