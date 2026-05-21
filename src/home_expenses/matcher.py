"""Match transactions to invoices, then to patterns, then to manual mappings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from home_expenses.config import Config, SplitGroup
from home_expenses.models import (
    Alert,
    AlertKind,
    Invoice,
    Item,
    Transaction,
)
from home_expenses.recurrence import Period, period_for


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
    deferred: dict[int, tuple[SplitGroup, list[Transaction]]] = {}

    for txn in transactions:
        group = _split_group_for(txn, config)
        if group is not None:
            if group.has_invoices:
                allowed = frozenset(m.name for m in group.members)
                cat_name, invoice = _try_invoice_match(
                    txn, inv_lists, config, alerts, consumed, restrict_to=allowed
                )
                if invoice is not None and cat_name is not None:
                    consumed.add((cat_name, invoice.content_hash))
                    items.append(Item(transaction=txn, category=cat_name, invoice=invoice))
                    continue
                for m in group.members:
                    if m.start_date is None or txn.date >= m.start_date:
                        alerts.append(
                            Alert(
                                kind=AlertKind.EXPENSE_MISSING_INVOICE,
                                message=f"expense in '{m.name}' has no matched invoice",
                                payload={
                                    "date": txn.date.isoformat(),
                                    "description": txn.description,
                                    "amount": str(txn.amount),
                                    "category": m.name,
                                },
                            )
                        )
                unmatched.append(txn)
                continue
            # rank-based: defer until we have the full period bucket
            entry = deferred.setdefault(id(group), (group, []))
            entry[1].append(txn)
            continue

        # Existing logic for non-split-group transactions.
        category_name, invoice = _try_invoice_match(txn, inv_lists, config, alerts, consumed)
        if invoice is not None and category_name is not None:
            consumed.add((category_name, invoice.content_hash))
            # If a manual mapping pinned this txn to that category, carry
            # its display overrides (recurrence / period_contains_payment)
            # through to the resulting Item.
            mapping = config.manual_mappings.get(txn.description)
            items.append(
                Item(
                    transaction=txn,
                    category=category_name,
                    invoice=invoice,
                    display_recurrence=(
                        mapping.recurrence
                        if mapping is not None and mapping.category == category_name
                        else None
                    ),
                    display_period_contains_payment=(
                        mapping.period_contains_payment
                        if mapping is not None and mapping.category == category_name
                        else None
                    ),
                )
            )
            continue

        pattern_cat = _try_pattern_match(txn, config, alerts)
        if pattern_cat is not None:
            items.append(Item(transaction=txn, category=pattern_cat, invoice=None))
            cat_def = config.categories.get(pattern_cat)
            if (
                cat_def is not None
                and cat_def.invoice_folder is not None
                and (cat_def.start_date is None or txn.date >= cat_def.start_date)
            ):
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
            continue

        mapping = config.manual_mappings.get(txn.description)
        if mapping is None:
            # Silently drop transactions that don't match any configured category.
            unmatched.append(txn)
            continue

        items.append(
            Item(
                transaction=txn,
                category=mapping.category,
                invoice=None,
                display_recurrence=mapping.recurrence,
                display_period_contains_payment=mapping.period_contains_payment,
            )
        )

    # Rank pass: assign deferred rank-based split-group transactions.
    for _, (group, txns) in deferred.items():
        buckets: dict[str, tuple[Period, list[Transaction]]] = {}
        for t in txns:
            p = period_for(t.date, group.recurrence)
            buckets.setdefault(p.label, (p, []))[1].append(t)

        for _label, (period, bucket) in buckets.items():
            active = [
                m for m in group.members if m.start_date is None or m.start_date <= period.end
            ]
            if len(bucket) != len(active):
                alerts.append(
                    Alert(
                        kind=AlertKind.AMBIGUOUS_SPLIT_BUCKET,
                        message=(
                            f"split-group bucket size mismatch in {period.label}: "
                            f"expected {len(active)}, got {len(bucket)}"
                        ),
                        payload={
                            "group": [m.name for m in group.members],
                            "period": period.label,
                            "expected": len(active),
                            "actual": len(bucket),
                            "transactions": [
                                {
                                    "date": t.date.isoformat(),
                                    "description": t.description,
                                    "amount": str(t.amount),
                                }
                                for t in bucket
                            ],
                        },
                    )
                )
                unmatched.extend(bucket)
                continue
            amounts = [t.amount for t in bucket]
            if len(set(amounts)) != len(amounts):
                tied = [t for t in bucket if amounts.count(t.amount) > 1]
                alerts.append(
                    Alert(
                        kind=AlertKind.SPLIT_AMOUNT_TIE,
                        message=(
                            f"split-group amount tie in {period.label}: "
                            f"{len(tied)} transactions share an amount"
                        ),
                        payload={
                            "group": [m.name for m in group.members],
                            "period": period.label,
                            "transactions": [
                                {
                                    "date": t.date.isoformat(),
                                    "description": t.description,
                                    "amount": str(t.amount),
                                }
                                for t in tied
                            ],
                        },
                    )
                )
            ranked = sorted(bucket, key=lambda t: (-t.amount, t.date))
            for cat, t in zip(active, ranked, strict=True):
                items.append(Item(transaction=t, category=cat.name, invoice=None))

    for cat_name, invs in inv_lists.items():
        for inv in invs:
            if (cat_name, inv.content_hash) not in consumed:
                period_str = (
                    f"{inv.period.start.isoformat()}→{inv.period.end.isoformat()}"
                )
                alerts.append(
                    Alert(
                        kind=AlertKind.ORPHAN_INVOICE,
                        message=(
                            f"invoice not matched to any expense in '{cat_name}': "
                            f"amount={inv.amount} period={period_str} "
                            f"path={inv.source_path}"
                        ),
                        payload={
                            "source_path": inv.source_path,
                            "category": cat_name,
                            "amount": str(inv.amount),
                            "invoice_date": (
                                inv.invoice_date.isoformat()
                                if inv.invoice_date is not None
                                else None
                            ),
                            "period_start": inv.period.start.isoformat(),
                            "period_end": inv.period.end.isoformat(),
                        },
                    )
                )

    return MatchResult(
        items=tuple(items),
        alerts=tuple(alerts),
        unmatched_transactions=tuple(unmatched),
    )


def _split_group_for(
    txn: Transaction,
    config: Config,
) -> SplitGroup | None:
    desc_upper = txn.description.upper()
    for group in config.split_groups:
        for pat in group.patterns:
            if pat.upper() in desc_upper:
                return group
    return None


def _try_invoice_match(
    txn: Transaction,
    inv_lists: Mapping[str, list[Invoice]],
    config: Config,
    alerts: list[Alert],
    consumed: set[tuple[str, str]],
    *,
    restrict_to: frozenset[str] | None = None,
) -> tuple[str | None, Invoice | None]:
    candidates: list[tuple[str, Invoice]] = []
    desc_upper = txn.description.upper()
    mapping = config.manual_mappings.get(txn.description)
    for cat_name, invs in inv_lists.items():
        if restrict_to is not None and cat_name not in restrict_to:
            continue
        cat = config.categories.get(cat_name)
        # The transaction must already belong to this category — either
        # because one of its patterns matches the description, or because
        # the user manual-mapped this exact description to it. Without
        # this guard a coincidental amount match would silently steal an
        # unrelated invoice.
        pattern_hit = cat is not None and any(p.upper() in desc_upper for p in cat.patterns)
        mapped_here = mapping is not None and mapping.category == cat_name
        if not pattern_hit and not mapped_here:
            continue
        window = (
            cat.match_window_days
            if cat is not None and cat.match_window_days is not None
            else config.match_window_days_default
        )
        for inv in invs:
            if inv.amount != txn.amount:
                continue
            if inv.invoice_date is not None:
                delta = (txn.date - inv.invoice_date).days
                if 0 <= delta <= window:
                    candidates.append((cat_name, inv))
            else:
                # No issue date on the invoice (e.g. IBI): fall back to period
                # bounds, accepting payments inside the period or up to `window`
                # days after period.end.
                latest = inv.period.end + timedelta(days=window)
                if inv.period.start <= txn.date <= latest:
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
    for c, i in candidates:
        consumed.add((c, i.content_hash))
    return None, None


def _try_pattern_match(
    txn: Transaction,
    config: Config,
    alerts: list[Alert],
) -> str | None:
    desc_upper = txn.description.upper()
    hits: list[str] = []
    for name, cat in config.categories.items():
        for pat in cat.patterns:
            if pat.upper() in desc_upper:
                hits.append(name)
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
