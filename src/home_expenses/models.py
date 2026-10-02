"""Plain dataclasses shared across the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any


@dataclass(frozen=True)
class Transaction:
    """One outgoing line from a bank statement (amount is always positive)."""

    date: date
    description: str
    amount: Decimal


@dataclass(frozen=True)
class InvoicePeriod:
    """The consumption period an invoice covers."""

    start: date
    end: date


@dataclass(frozen=True)
class Invoice:
    """One invoice PDF, after parsing."""

    source_path: str
    content_hash: str
    parser: str
    amount: Decimal
    invoice_date: date | None
    period: InvoicePeriod


@dataclass(frozen=True)
class Item:
    """A categorized transaction, optionally paired with an invoice."""

    transaction: Transaction
    category: str
    invoice: Invoice | None
    display_recurrence: str | None = None
    display_period_contains_payment: bool | None = None
    # True when the routing decision came from a manual_mapping that pinned
    # this (description, amount) to this category. Such items are shown in
    # the report even when the category has an invoice_folder but the item
    # has no invoice attached — the user has authored the routing and taken
    # responsibility for the "no invoice expected here" semantics.
    from_manual_mapping: bool = False


@dataclass(frozen=True)
class BankStatementFile:
    """One parsed statement file."""

    source_path: str
    content_hash: str
    date_range: tuple[date, date]
    transactions: tuple[Transaction, ...]


class AlertKind(StrEnum):
    UNCLASSIFIED_EXPENSE = "unclassified_expense"
    EXPENSE_MISSING_INVOICE = "expense_missing_invoice"
    ORPHAN_INVOICE = "orphan_invoice"
    RECURRING_MISSED = "recurring_missed"
    AMBIGUOUS_INVOICE_MATCH = "ambiguous_invoice_match"
    AMBIGUOUS_PATTERN_MATCH = "ambiguous_pattern_match"
    AMBIGUOUS_SPLIT_BUCKET = "ambiguous_split_bucket"
    SPLIT_AMOUNT_TIE = "split_amount_tie"
    UNUSED_MANUAL_MAPPING = "unused_manual_mapping"
    UNUSED_ALERT_ACCEPTANCE = "unused_alert_acceptance"


@dataclass(frozen=True)
class Alert:
    """One thing to surface in the report's alerts banner."""

    kind: AlertKind
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    # Set by `apply_acceptances` when an accepted_alerts rule matched this
    # alert: the user has seen it and signed it off. Accepted alerts still
    # render, but in green, and do not count towards "open alerts".
    accepted: bool = False
    note: str | None = None


@dataclass(frozen=True)
class ReportCategory:
    """Per-category display metadata consumed by the report renderer."""

    name: str
    recurrence: str
    period_contains_payment: bool
    period_edge_days: int = 0


@dataclass(frozen=True)
class ReportModel:
    """The fully assembled data the renderer consumes."""

    generated_at: date
    currency: str
    items: tuple[Item, ...]
    alerts: tuple[Alert, ...]
    years: tuple[int, ...]
    categories_by_name: dict[str, ReportCategory] = field(default_factory=dict)
