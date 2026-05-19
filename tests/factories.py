"""Factory functions for synthetic test data. No real-world values here."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from home_expenses.models import (
    Invoice,
    InvoicePeriod,
    Item,
    Transaction,
)


def make_transaction(
    *,
    date: date = date(2026, 5, 18),
    description: str = "TEST EXPENSE",
    amount: Decimal = Decimal("10.00"),
) -> Transaction:
    return Transaction(date=date, description=description, amount=amount)


def make_invoice(
    *,
    source_path: str = "/tmp/test.pdf",
    content_hash: str = "0" * 64,
    parser: str = "pepeenergy",
    amount: Decimal = Decimal("10.00"),
    invoice_id: str = "TEST-001",
    invoice_date: date = date(2026, 5, 10),
    period_start: date = date(2026, 4, 1),
    period_end: date = date(2026, 4, 30),
) -> Invoice:
    return Invoice(
        source_path=source_path,
        content_hash=content_hash,
        parser=parser,
        amount=amount,
        invoice_id=invoice_id,
        invoice_date=invoice_date,
        period=InvoicePeriod(start=period_start, end=period_end),
    )


def make_item(
    *,
    transaction: Transaction | None = None,
    category: str = "test_category",
    invoice: Invoice | None = None,
) -> Item:
    return Item(
        transaction=transaction or make_transaction(),
        category=category,
        invoice=invoice,
    )
