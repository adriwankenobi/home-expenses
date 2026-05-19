from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.factories import make_invoice, make_item, make_transaction


def test_make_transaction_has_defaults() -> None:
    txn = make_transaction()
    assert isinstance(txn.amount, Decimal)
    assert txn.amount > 0


def test_make_transaction_overrides() -> None:
    txn = make_transaction(date=date(2026, 1, 1), description="X", amount=Decimal("9.99"))
    assert txn.description == "X"
    assert txn.amount == Decimal("9.99")


def test_make_invoice_defaults() -> None:
    inv = make_invoice()
    assert inv.amount > 0
    assert inv.period.start <= inv.period.end


def test_make_item_default_invoice_is_none() -> None:
    item = make_item()
    assert item.invoice is None
    assert item.category == "test_category"
