from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.config import (
    BankStatementsConfig,
    Category,
    Config,
)
from home_expenses.runner import _has_expected_invoice
from tests.factories import make_invoice, make_item, make_transaction


def _config(
    *,
    categories: dict[str, Category] | None = None,
) -> Config:
    return Config(
        currency="EUR",
        bank_statements=BankStatementsConfig(dir=Path("/tmp"), glob="*.csv"),
        match_window_days_default=30,
        cache_dir=Path("/tmp/cache"),
        categories=categories or {},
        manual_mappings={},
    )


def test_item_with_invoice_is_visible() -> None:
    item = make_item(invoice=make_invoice())
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
            ),
        }
    )
    assert _has_expected_invoice(item, cfg) is True


def test_item_without_invoice_in_invoice_expecting_category_is_hidden() -> None:
    txn = make_transaction(date=date(2026, 5, 15))
    item = make_item(transaction=txn, category="ibi", invoice=None)
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
            ),
        }
    )
    assert _has_expected_invoice(item, cfg) is False


def test_item_without_invoice_in_invoiceless_category_is_visible() -> None:
    item = make_item(category="other", invoice=None)
    cfg = _config(
        categories={
            "other": Category(name="other", recurrence="monthly"),
        }
    )
    assert _has_expected_invoice(item, cfg) is True


def test_item_before_start_date_without_invoice_is_still_hidden() -> None:
    # Pre-start_date pattern matches with no invoice are hidden too, even
    # though the matcher doesn't raise EXPENSE_MISSING_INVOICE for them.
    # Goal: any uninvoiced item in an invoice-expecting category is hidden.
    txn = make_transaction(date=date(2023, 5, 15))
    item = make_item(transaction=txn, category="ibi", invoice=None)
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
                start_date=date(2024, 1, 1),
            ),
        }
    )
    assert _has_expected_invoice(item, cfg) is False


def test_item_on_or_after_start_date_without_invoice_is_hidden() -> None:
    txn = make_transaction(date=date(2024, 6, 15))
    item = make_item(transaction=txn, category="ibi", invoice=None)
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
                start_date=date(2024, 1, 1),
            ),
        }
    )
    assert _has_expected_invoice(item, cfg) is False


def test_unknown_category_is_visible() -> None:
    item = make_item(category="ghost", invoice=None)
    cfg = _config(categories={})
    # No category def means we have no signal that an invoice was expected;
    # leave the item visible so it's not silently dropped.
    assert _has_expected_invoice(item, cfg) is True


def test_item_amounts_dont_appear_in_test_data() -> None:
    # Cheap sanity: factories must not leak realistic amounts. Decimal('10.00')
    # is the test default; any test surfacing real values would be a leak.
    item = make_item()
    assert item.transaction.amount == Decimal("10.00")


def test_full_pipeline_filter_mirrors_user_ibi_setup() -> None:
    # 5 pattern-matched IBI txns in 2026, all >= start_date 2026-Q1.
    # 2 have invoices that match; 3 don't. After the matcher + runner
    # filter, only the 2 with invoices reach the report model.
    from home_expenses.matcher import match

    ibi = Category(
        name="IBI",
        recurrence="quarterly",
        invoice_folder=Path("/tmp"),
        invoice_parser="ibi",
        patterns=("IBI INVOICE",),
        start_date=date(2026, 1, 1),
        period_contains_payment=True,
    )
    cfg = _config(categories={"IBI": ibi})

    txns = [
        make_transaction(
            date=date(2026, 2, 15), description="IBI INVOICE 1", amount=Decimal("100.00")
        ),
        make_transaction(
            date=date(2026, 5, 10), description="IBI INVOICE 2", amount=Decimal("200.00")
        ),
        make_transaction(
            date=date(2026, 8, 20), description="IBI INVOICE 3", amount=Decimal("300.00")
        ),
        make_transaction(
            date=date(2026, 11, 5), description="IBI INVOICE 4", amount=Decimal("400.00")
        ),
        make_transaction(
            date=date(2026, 12, 1), description="IBI INVOICE 5", amount=Decimal("500.00")
        ),
    ]
    invs = [
        make_invoice(
            amount=Decimal("100.00"),
            invoice_date=None,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            content_hash="h1",
        ),
        make_invoice(
            amount=Decimal("200.00"),
            invoice_date=None,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            content_hash="h2",
        ),
    ]
    result = match(txns, {"IBI": invs}, cfg)

    # Matcher creates 5 items (one per pattern-matched txn); 2 have invoices.
    assert len(result.items) == 5
    with_invoice = [it for it in result.items if it.invoice is not None]
    without_invoice = [it for it in result.items if it.invoice is None]
    assert len(with_invoice) == 2
    assert len(without_invoice) == 3

    # The runner's filter should drop the 3 invoice-less items.
    visible = tuple(it for it in result.items if _has_expected_invoice(it, cfg))
    assert len(visible) == 2
    assert all(it.invoice is not None for it in visible)
