from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.config import (
    BankStatementsConfig,
    Category,
    Config,
)
from home_expenses.matcher import MatchResult, match
from home_expenses.models import AlertKind
from tests.factories import make_invoice, make_transaction


def _config(
    *,
    categories: dict[str, Category] | None = None,
    manual_mappings: dict[str, str] | None = None,
) -> Config:
    return Config(
        currency="EUR",
        bank_statements=BankStatementsConfig(dir=Path("/tmp"), glob="*.csv"),
        match_window_days_default=30,
        cache_dir=Path("/tmp/cache"),
        categories=categories or {},
        manual_mappings=manual_mappings or {},
    )


def test_exact_invoice_match() -> None:
    txn = make_transaction(date=date(2026, 5, 18), description="X", amount=Decimal("11.11"))
    inv = make_invoice(amount=Decimal("11.11"), invoice_date=date(2026, 5, 10), parser="pepeenergy")
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("RECIBO PEPE ENERGY",),
            )
        }
    )
    result: MatchResult = match([txn], {"electricity": [inv]}, cfg)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.category == "electricity"
    assert item.invoice is inv
    assert len(result.alerts) == 0


def test_invoice_outside_window_falls_to_pattern() -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="RECIBO PEPE ENERGY",
        amount=Decimal("11.11"),
    )
    inv = make_invoice(
        amount=Decimal("11.11"),
        invoice_date=date(2025, 1, 1),  # ancient
    )
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("RECIBO PEPE ENERGY",),
            )
        }
    )
    result = match([txn], {"electricity": [inv]}, cfg)
    assert len(result.items) == 1
    assert result.items[0].invoice is None
    assert any(a.kind is AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


def test_ambiguous_invoice_match_falls_through_to_pattern() -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="RECIBO PEPE ENERGY",
        amount=Decimal("11.11"),
    )
    inv1 = make_invoice(
        amount=Decimal("11.11"),
        invoice_date=date(2026, 5, 5),
        invoice_id="A",
        content_hash="h1",
    )
    inv2 = make_invoice(
        amount=Decimal("11.11"),
        invoice_date=date(2026, 5, 10),
        invoice_id="B",
        content_hash="h2",
    )
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("RECIBO PEPE ENERGY",),
            )
        }
    )
    result = match([txn], {"electricity": [inv1, inv2]}, cfg)
    assert len(result.items) == 1
    assert result.items[0].invoice is None
    assert any(a.kind is AlertKind.AMBIGUOUS_INVOICE_MATCH for a in result.alerts)
    # also fires missing-invoice because pattern matched but no invoice attached
    assert any(a.kind is AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)
    # Ambiguous candidates remain pending; they MUST NOT be flagged orphan.
    assert all(a.kind is not AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_pattern_match_no_invoice_folder() -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="RECIBO AYTO",
        amount=Decimal("50.00"),
    )
    cfg = _config(
        categories={
            "taxes": Category(
                name="taxes",
                recurrence="yearly",
                patterns=("RECIBO AYTO",),
            )
        }
    )
    result = match([txn], {}, cfg)
    assert len(result.items) == 1
    assert result.items[0].category == "taxes"
    assert result.items[0].invoice is None
    # no alert: this category has no invoice_folder
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


def test_manual_mapping_used_as_last_resort() -> None:
    txn = make_transaction(description="MYSTERIOUS THING", amount=Decimal("3"))
    cfg = _config(
        categories={
            "other": Category(name="other", recurrence="none"),
        },
        manual_mappings={"MYSTERIOUS THING": "other"},
    )
    result = match([txn], {}, cfg)
    assert len(result.items) == 1
    assert result.items[0].category == "other"


def test_unclassified_transactions_are_silently_dropped() -> None:
    # The matcher now ignores transactions that don't match any configured
    # category. No alert is generated and the txn does not become an Item.
    txn = make_transaction(description="UNKNOWN", amount=Decimal("3"))
    cfg = _config()
    result = match([txn], {}, cfg)
    assert result.items == ()
    assert all(a.kind is not AlertKind.UNCLASSIFIED_EXPENSE for a in result.alerts)
    # Still surfaced in the unmatched list for any callers that want it.
    assert len(result.unmatched_transactions) == 1


def test_orphan_invoice_alert() -> None:
    inv = make_invoice(amount=Decimal("99.99"))
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
            )
        }
    )
    result = match([], {"electricity": [inv]}, cfg)
    assert any(a.kind is AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_ambiguous_pattern_match_alerts() -> None:
    # Two categories both substring-match the same Concepto.
    txn = make_transaction(description="RECIBO ABC EXTRA", amount=Decimal("1"))
    cfg = _config(
        categories={
            "a": Category(name="a", recurrence="none", patterns=("RECIBO ABC",)),
            "b": Category(name="b", recurrence="none", patterns=("ABC EXTRA",)),
        }
    )
    result = match([txn], {}, cfg)
    assert any(a.kind is AlertKind.AMBIGUOUS_PATTERN_MATCH for a in result.alerts)


def test_per_category_window_override() -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="RECIBO ELECTRICITY",
        amount=Decimal("10"),
    )
    # invoice is 20 days before txn; default window 30 would match,
    # but the category override is 5 → out of window
    inv = make_invoice(amount=Decimal("10"), invoice_date=date(2026, 4, 28))
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("RECIBO ELECTRICITY",),
                match_window_days=5,
            )
        }
    )
    result = match([txn], {"electricity": [inv]}, cfg)
    # Transaction is categorized via pattern, but the invoice did not attach
    # because it falls outside the per-category window override.
    assert len(result.items) == 1
    assert result.items[0].category == "electricity"
    assert result.items[0].invoice is None
    # The out-of-window invoice is unconsumed and must surface as an orphan.
    assert any(a.kind is AlertKind.ORPHAN_INVOICE for a in result.alerts)
