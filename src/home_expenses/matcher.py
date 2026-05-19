"""Match transactions to invoices, then to patterns, then to manual mappings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from home_expenses.config import Config
from home_expenses.models import (
    Alert,
    AlertKind,
    Invoice,
    Item,
    Transaction,
)


@dataclass(frozen=True)
class MatchResult:
    items: tuple[Item, ...]
    alerts: tuple[Alert, ...]
    unmatched_transactions: tuple[Transaction, ...]


def match(
    transactions: Iterable[Transaction],
    invoices_by_category: Mapping[str, Iterable[Invoice]],
    config: Config,
) -> MatchResult:
    transactions = list(transactions)
    inv_lists: dict[str, list[Invoice]] = {k: list(v) for k, v in invoices_by_category.items()}
    consumed: set[tuple[str, str]] = set()
    items: list[Item] = []
    alerts: list[Alert] = []
    unmatched: list[Transaction] = []

    for txn in transactions:
        category_name, invoice = _try_invoice_match(txn, inv_lists, config, alerts)
        if invoice is not None and category_name is not None:
            consumed.add((category_name, invoice.content_hash))
            items.append(Item(transaction=txn, category=category_name, invoice=invoice))
            continue

        pattern_cat = _try_pattern_match(txn, config, alerts)
        if pattern_cat is None:
            pattern_cat = config.manual_mappings.get(txn.description)
        if pattern_cat is None:
            alerts.append(
                Alert(
                    kind=AlertKind.UNCLASSIFIED_EXPENSE,
                    message=f"unclassified transaction: {txn.description}",
                    payload={
                        "date": txn.date.isoformat(),
                        "description": txn.description,
                        "amount": str(txn.amount),
                    },
                )
            )
            unmatched.append(txn)
            continue

        items.append(Item(transaction=txn, category=pattern_cat, invoice=None))
        cat_def = config.categories.get(pattern_cat)
        if cat_def is not None and cat_def.invoice_folder is not None:
            alerts.append(
                Alert(
                    kind=AlertKind.EXPENSE_MISSING_INVOICE,
                    message=f"expense in '{pattern_cat}' has no matched invoice",
                    payload={
                        "date": txn.date.isoformat(),
                        "description": txn.description,
                        "amount": str(txn.amount),
                        "category": pattern_cat,
                    },
                )
            )

    for cat_name, invs in inv_lists.items():
        for inv in invs:
            if (cat_name, inv.content_hash) not in consumed:
                alerts.append(
                    Alert(
                        kind=AlertKind.ORPHAN_INVOICE,
                        message=f"invoice not matched to any expense: {inv.source_path}",
                        payload={
                            "source_path": inv.source_path,
                            "category": cat_name,
                            "amount": str(inv.amount),
                            "invoice_date": inv.invoice_date.isoformat(),
                        },
                    )
                )

    return MatchResult(
        items=tuple(items),
        alerts=tuple(alerts),
        unmatched_transactions=tuple(unmatched),
    )


def _try_invoice_match(
    txn: Transaction,
    inv_lists: Mapping[str, list[Invoice]],
    config: Config,
    alerts: list[Alert],
) -> tuple[str | None, Invoice | None]:
    candidates: list[tuple[str, Invoice]] = []
    for cat_name, invs in inv_lists.items():
        cat = config.categories.get(cat_name)
        window = (
            cat.match_window_days
            if cat is not None and cat.match_window_days is not None
            else config.match_window_days_default
        )
        for inv in invs:
            if inv.amount != txn.amount:
                continue
            delta = (txn.date - inv.invoice_date).days
            if 0 <= delta <= window:
                candidates.append((cat_name, inv))
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0]
    alerts.append(
        Alert(
            kind=AlertKind.AMBIGUOUS_INVOICE_MATCH,
            message=f"transaction has multiple invoice candidates: {txn.description}",
            payload={
                "date": txn.date.isoformat(),
                "description": txn.description,
                "amount": str(txn.amount),
                "candidates": [
                    {"category": c, "source_path": i.source_path} for c, i in candidates
                ],
            },
        )
    )
    return None, None


def _try_pattern_match(
    txn: Transaction,
    config: Config,
    alerts: list[Alert],
) -> str | None:
    desc_upper = txn.description.upper()
    hits: list[str] = []
    for cat_name, cat in config.categories.items():
        for pat in cat.patterns:
            if pat.upper() in desc_upper:
                hits.append(cat_name)
                break
    if not hits:
        return None
    if len(set(hits)) > 1:
        alerts.append(
            Alert(
                kind=AlertKind.AMBIGUOUS_PATTERN_MATCH,
                message=(
                    f"transaction matches patterns from multiple categories: {txn.description}"
                ),
                payload={
                    "date": txn.date.isoformat(),
                    "description": txn.description,
                    "amount": str(txn.amount),
                    "candidate_categories": sorted(set(hits)),
                },
            )
        )
        return None
    return hits[0]


__all__ = ["MatchResult", "match"]
