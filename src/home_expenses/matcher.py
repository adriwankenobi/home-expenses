"""Match transactions to invoices, then to patterns, then to manual mappings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from home_expenses.config import Category, Config, SplitGroup, resolve_manual_mapping
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
    deferred_mixed: dict[int, tuple[SplitGroup, list[Transaction]]] = {}
    # Resolve same-amount ambiguity globally before the per-transaction pass.
    # Maps id(txn) -> (category, invoice) for transactions whose match was
    # determined by chronological zip across a (category, amount) bucket.
    pre_assigned = _resolve_chronological_buckets(transactions, inv_lists, config)
    # Reify chrono-zip's promises into `consumed` before the main loop iterates.
    # Without this, a not-pre-assigned txn appearing earlier in input order than
    # its pre-assigned peers would see their invoices as still available and
    # could spuriously emit AMBIGUOUS_INVOICE_MATCH or steal an invoice.
    for cat_name, inv in pre_assigned.values():
        consumed.add((cat_name, inv.content_hash))

    for txn in transactions:
        group = _split_group_for(txn, config)
        if group is not None:
            if group.kind == "all_invoiced":
                allowed = frozenset(m.name for m in group.members)
                chrono = pre_assigned.get(id(txn))
                if chrono is not None and chrono[0] in allowed:
                    chrono_cat, chrono_inv = chrono
                    consumed.add((chrono_cat, chrono_inv.content_hash))
                    items.append(
                        Item(transaction=txn, category=chrono_cat, invoice=chrono_inv)
                    )
                    continue
                matched_cat, matched_inv = _try_invoice_match(
                    txn, inv_lists, config, alerts, consumed, restrict_to=allowed
                )
                if matched_inv is not None and matched_cat is not None:
                    consumed.add((matched_cat, matched_inv.content_hash))
                    items.append(Item(transaction=txn, category=matched_cat, invoice=matched_inv))
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
            if group.kind == "none_invoiced":
                # rank-based: defer until we have the full period bucket
                entry = deferred.setdefault(id(group), (group, []))
                entry[1].append(txn)
                continue
            # kind == "mixed": defer for the mixed resolver below.
            entry = deferred_mixed.setdefault(id(group), (group, []))
            entry[1].append(txn)
            continue

        # Existing logic for non-split-group transactions.
        chrono = pre_assigned.get(id(txn))
        if chrono is not None:
            category_name, invoice = chrono
            consumed.add((category_name, invoice.content_hash))
            mapping = resolve_manual_mapping(txn.description, txn.amount, config)
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

        matched_cat, matched_inv = _try_invoice_match(
            txn, inv_lists, config, alerts, consumed
        )
        if matched_inv is not None and matched_cat is not None:
            consumed.add((matched_cat, matched_inv.content_hash))
            # If a manual mapping pinned this txn to that category, carry
            # its display overrides (recurrence / period_contains_payment)
            # through to the resulting Item.
            mapping = resolve_manual_mapping(txn.description, txn.amount, config)
            items.append(
                Item(
                    transaction=txn,
                    category=matched_cat,
                    invoice=matched_inv,
                    display_recurrence=(
                        mapping.recurrence
                        if mapping is not None and mapping.category == matched_cat
                        else None
                    ),
                    display_period_contains_payment=(
                        mapping.period_contains_payment
                        if mapping is not None and mapping.category == matched_cat
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

        mapping = resolve_manual_mapping(txn.description, txn.amount, config)
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
                from_manual_mapping=True,
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

    # Mixed pass: assign deferred mixed-group transactions per period bucket.
    for _, (group, txns) in deferred_mixed.items():
        active_invoiced_all = [m for m in group.members if m.invoice_folder is not None]
        noninv_members = [m for m in group.members if m.invoice_folder is None]
        implicit_catchall = _implicit_catchall_for(group, noninv_members, config)

        # Per-transaction routing kicks in when:
        # - group.recurrence == "none" (no periods to bucket by), OR
        # - there are 2+ non-invoiced members (leftover routing is decided per
        #   manual_mapping by amount, not by period bucket-size invariants).
        # The per-period bucket path (further below) only handles the
        # "1 non-invoiced + real recurrence" case where the sanity check
        # `len(remaining) == 1` is meaningful.
        if group.recurrence == "none" or len(noninv_members) >= 2:
            # Per-transaction routing. Try the group's invoiced members by
            # amount; on miss, route the leftover via _route_mixed_leftover.
            for t in txns:
                active_invoiced = [
                    m for m in active_invoiced_all
                    if m.start_date is None or m.start_date <= t.date
                ]
                active_invoiced_names = frozenset(m.name for m in active_invoiced)
                pre = pre_assigned.get(id(t))
                if pre is not None and pre[0] in active_invoiced_names:
                    pre_cat, pre_inv = pre
                    items.append(
                        Item(transaction=t, category=pre_cat, invoice=pre_inv)
                    )
                    continue
                inv_cat, inv_match = _try_invoice_match(
                    t, inv_lists, config, alerts, consumed,
                    restrict_to=active_invoiced_names,
                )
                if inv_match is not None and inv_cat is not None:
                    consumed.add((inv_cat, inv_match.content_hash))
                    items.append(
                        Item(transaction=t, category=inv_cat, invoice=inv_match)
                    )
                    continue
                # Leftover: route to a non-invoiced member.
                routed = _route_mixed_leftover(
                    t, noninv_members, group, implicit_catchall, config
                )
                if routed is not None:
                    items.append(routed)
                else:
                    alerts.append(
                        Alert(
                            kind=AlertKind.EXPENSE_MISSING_INVOICE,
                            message=(
                                f"mixed split-group leftover for "
                                f"{', '.join(m.name for m in noninv_members)!r} "
                                f"has no resolvable destination"
                            ),
                            payload={
                                "date": t.date.isoformat(),
                                "description": t.description,
                                "amount": str(t.amount),
                                "group": [m.name for m in group.members],
                                "non_invoiced_members": [m.name for m in noninv_members],
                            },
                        )
                    )
                    unmatched.append(t)
            continue

        mixed_buckets: dict[str, tuple[Period, list[Transaction]]] = {}
        for t in txns:
            p = period_for(t.date, group.recurrence)
            mixed_buckets.setdefault(p.label, (p, []))[1].append(t)

        for _label, (period, bucket) in mixed_buckets.items():
            active = [
                m for m in group.members
                if m.start_date is None or m.start_date <= period.end
            ]
            active_invoiced = [m for m in active if m.invoice_folder is not None]
            active_invoiced_names = frozenset(m.name for m in active_invoiced)
            active_noninv = [m for m in active if m.invoice_folder is None]

            matched: list[Item] = []
            matched_member_names: set[str] = set()
            remaining: list[Transaction] = []
            for t in bucket:
                pre = pre_assigned.get(id(t))
                if pre is not None and pre[0] in active_invoiced_names:
                    # The pre-loop earlier already added this invoice to
                    # `consumed`; we just realize the pre-assignment here.
                    pre_cat, pre_inv = pre
                    matched.append(
                        Item(transaction=t, category=pre_cat, invoice=pre_inv)
                    )
                    matched_member_names.add(pre_cat)
                    continue
                inv_cat, inv_match = _try_invoice_match(
                    t,
                    inv_lists,
                    config,
                    alerts,
                    consumed,
                    restrict_to=active_invoiced_names,
                )
                if inv_match is not None and inv_cat is not None:
                    consumed.add((inv_cat, inv_match.content_hash))
                    matched.append(
                        Item(transaction=t, category=inv_cat, invoice=inv_match)
                    )
                    matched_member_names.add(inv_cat)
                    continue
                remaining.append(t)

            # Missing-invoice alerts for active invoiced members without a
            # match in this period. Emit before the bucket check so the user
            # always sees the per-member signal alongside any bucket-level
            # alert. Payload is per-(member, period) — there is no single
            # transaction to attach, so it omits date/description/amount that
            # the per-transaction EXPENSE_MISSING_INVOICE alerts carry.
            for m in active_invoiced:
                if m.name in matched_member_names:
                    continue
                alerts.append(
                    Alert(
                        kind=AlertKind.EXPENSE_MISSING_INVOICE,
                        message=(
                            f"expense in '{m.name}' has no matched invoice "
                            f"in {period.label}"
                        ),
                        payload={
                            "category": m.name,
                            "period": period.label,
                        },
                    )
                )

            # Stronger per-side check: matched must cover all active invoiced
            # members AND remaining must equal the number of non-invoiced
            # active members. The weaker `len(bucket) == len(active)` check
            # would silently route extra leftovers to a single catch-all in
            # cases where an invoiced member's invoice was actually missing.
            if (
                len(matched) != len(active_invoiced)
                or len(remaining) != len(active_noninv)
            ):
                # Invoice matches are deterministic (amount equality + window),
                # so keep them. Only the ambiguous leftovers stay unmatched.
                alerts.append(
                    Alert(
                        kind=AlertKind.AMBIGUOUS_SPLIT_BUCKET,
                        message=(
                            f"mixed split-group bucket mismatch in "
                            f"{period.label}: matched {len(matched)}/"
                            f"{len(active_invoiced)} invoiced, "
                            f"leftover {len(remaining)}/{len(active_noninv)} "
                            f"non-invoiced"
                        ),
                        payload={
                            "group": [m.name for m in group.members],
                            "period": period.label,
                            "matched": len(matched),
                            "expected_matched": len(active_invoiced),
                            "leftovers": len(remaining),
                            "expected_leftovers": len(active_noninv),
                            "transactions": [
                                {
                                    "date": t.date.isoformat(),
                                    "description": t.description,
                                    "amount": str(t.amount),
                                }
                                for t in remaining
                            ],
                        },
                    )
                )
                items.extend(matched)
                unmatched.extend(remaining)
                continue

            items.extend(matched)

            # This branch only runs for mixed groups with a real recurrence
            # AND exactly one non-invoiced member (the 2+ noninv and rec=none
            # cases take the per-transaction path above). When the lone
            # non-invoiced member is not yet active in this period the bucket
            # check above (len(remaining) == 0) has already passed.
            if not active_noninv:
                continue
            for t in remaining:
                routed = _route_mixed_leftover(
                    t, active_noninv, group, implicit_catchall, config
                )
                if routed is not None:
                    items.append(routed)
                else:
                    # The single-noninv case routes unconditionally when the
                    # member is active, so this branch is only reached if the
                    # member's start_date excluded the txn. Treat as unmatched.
                    unmatched.append(t)

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

    # Amount-specific manual_mapping entries that didn't actually route any
    # transaction are flagged so users can spot typos, stale entries, or
    # mappings whose intended txn never appeared. "Routed" means the entry
    # produced an Item via the manual_mapping override path (Item.from_manual_mapping
    # is True). Entries whose (description, amount) coincides with an
    # invoice-matched transaction are still flagged — the invoice match
    # would have routed it without the mapping, so the mapping was not
    # load-bearing. No-amount fallback entries are skipped (their purpose
    # is to absorb whatever's left; not firing is normal).
    routed_via_mapping: set[tuple[str, Decimal, str]] = {
        (it.transaction.description, it.transaction.amount, it.category)
        for it in items
        if it.from_manual_mapping
    }
    for desc, mm_entries in config.manual_mappings.items():
        for mm_entry in mm_entries:
            if mm_entry.amount is None:
                continue
            if (desc, mm_entry.amount, mm_entry.category) in routed_via_mapping:
                continue
            alerts.append(
                Alert(
                    kind=AlertKind.UNUSED_MANUAL_MAPPING,
                    message=(
                        f"manual_mapping '{desc}' amount={mm_entry.amount} → "
                        f"'{mm_entry.category}' is unused (no transaction was "
                        f"routed via this entry)"
                    ),
                    payload={
                        "description": desc,
                        "amount": str(mm_entry.amount),
                        "category": mm_entry.category,
                    },
                )
            )

    return MatchResult(
        items=tuple(items),
        alerts=tuple(alerts),
        unmatched_transactions=tuple(unmatched),
    )


def _implicit_catchall_for(
    group: SplitGroup,
    noninv_members: list[Category],
    config: Config,
) -> Category | None:
    """Identify the implicit catch-all non-invoiced member of a mixed group.

    Returns the single non-invoiced member that is NOT referenced by any
    manual_mapping entry whose key matches one of the group's patterns. If
    zero or two-or-more such members exist, returns None (no implicit
    catch-all; routing requires explicit manual_mapping resolution).

    Only applies to 2+ noninv groups — for 1-noninv groups the single
    member is itself the catch-all via the existing one-noninv branch.
    """
    if len(noninv_members) < 2:
        return None
    referenced: set[str] = set()
    noninv_names = {m.name for m in noninv_members}
    patterns_upper = {p.upper() for p in group.patterns}
    for key, entries in config.manual_mappings.items():
        # Manual_mapping keys are full descriptions; consider a key relevant to
        # this group if any of the group's patterns is a substring of the key
        # (matches how _split_group_for routes a transaction to the group).
        key_upper = key.upper()
        if not any(pat in key_upper for pat in patterns_upper):
            continue
        for entry in entries:
            if entry.category in noninv_names:
                referenced.add(entry.category)
    unreferenced = [m for m in noninv_members if m.name not in referenced]
    if len(unreferenced) == 1:
        return unreferenced[0]
    return None


def _route_mixed_leftover(
    txn: Transaction,
    noninv_members: list[Category],
    group: SplitGroup,
    implicit_catchall: Category | None,
    config: Config,
) -> Item | None:
    """Route a leftover (no invoice matched) in a mixed split group.

    Returns the resulting Item or None if the leftover couldn't be routed
    (caller should alert and append to ``unmatched``).

    - One non-invoiced member: route to it unconditionally, subject to its
      ``start_date``. Carries manual_mapping display overrides when the
      mapping points at the same category.
    - Two or more non-invoiced members: the leftover must be resolved via
      ``resolve_manual_mapping`` to one of the group's non-invoiced members
      (and that member must be active for this transaction). If no entry
      resolves but the group has an ``implicit_catchall`` (exactly one
      non-invoiced member is not referenced by any manual_mapping entry
      keyed by the group's patterns), route to it. Otherwise return None.
    """
    # Manual_mapping is the authoritative routing override: if it points to
    # any member of this group (invoiced or non-invoiced), trust the user
    # and route there. For an invoiced target this produces an Item without
    # invoice (the user has authored the routing and is saying "no invoice
    # expected here"); the from_manual_mapping flag exempts it from the
    # runner's "items in invoiced categories must have invoices" filter.
    mapping = resolve_manual_mapping(txn.description, txn.amount, config)
    if mapping is not None:
        mapped_target: Category | None = next(
            (m for m in group.members if m.name == mapping.category),
            None,
        )
        if mapped_target is not None and (
            mapped_target.start_date is None
            or txn.date >= mapped_target.start_date
        ):
            return Item(
                transaction=txn,
                category=mapped_target.name,
                invoice=None,
                display_recurrence=mapping.recurrence,
                display_period_contains_payment=mapping.period_contains_payment,
                from_manual_mapping=True,
            )
        # Mapping points outside the group (or to a not-yet-active member):
        # fall through to default routing.

    if len(noninv_members) == 1:
        single_target = noninv_members[0]
        if single_target.start_date is not None and txn.date < single_target.start_date:
            return None
        return Item(
            transaction=txn,
            category=single_target.name,
            invoice=None,
        )

    # 2+ non-invoiced members and no explicit mapping: fall back to the
    # implicit catch-all if the group has one (exactly one non-invoiced
    # member not referenced by any manual_mapping entry keyed by the group's
    # patterns).
    if implicit_catchall is not None and (
        implicit_catchall.start_date is None
        or txn.date >= implicit_catchall.start_date
    ):
        return Item(
            transaction=txn,
            category=implicit_catchall.name,
            invoice=None,
        )
    return None


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


def _resolve_chronological_buckets(
    transactions: list[Transaction],
    inv_lists: Mapping[str, list[Invoice]],
    config: Config,
) -> dict[int, tuple[str, Invoice]]:
    """Resolve same-amount ambiguity via min-lag chronological pairing.

    For each (category, amount) bucket with ≥2 transactions and ≥2 invoices,
    pick min(N_txns, N_invs) pairs that minimize total |txn.date − inv anchor|
    subject to every pair satisfying the category's window. The chosen pairs
    are always a sorted-zip of the picked subsets — for a fixed subset, sorted
    pairing is optimal (for txns t1≤t2 and invs i1≤i2, swapping to (t1→i1,
    t2→i2) never increases total lag and never widens any individual pair).
    The remaining items on the longer side stay unassigned and fall through
    (txns to per-transaction matching, invoices to the orphan check).
    """
    assignments: dict[int, tuple[str, Invoice]] = {}
    for cat_name, invs in inv_lists.items():
        cat = config.categories.get(cat_name)
        if cat is None:
            continue
        window = (
            cat.match_window_days
            if cat.match_window_days is not None
            else config.match_window_days_default
        )
        cat_txns: list[Transaction] = []
        for txn in transactions:
            desc_upper = txn.description.upper()
            mapping = resolve_manual_mapping(txn.description, txn.amount, config)
            pattern_hit = any(p.upper() in desc_upper for p in cat.patterns)
            mapped_here = mapping is not None and mapping.category == cat_name
            if pattern_hit or mapped_here:
                cat_txns.append(txn)
        buckets: dict[Decimal, tuple[list[Transaction], list[Invoice]]] = {}
        for t in cat_txns:
            buckets.setdefault(t.amount, ([], []))[0].append(t)
        for inv in invs:
            if inv.amount in buckets:
                buckets[inv.amount][1].append(inv)
        for txns, candidate_invs in buckets.values():
            # Exclude transactions already assigned by an earlier category's
            # iteration. Without this, a split group whose invoices are
            # spread across categories would have each category retry
            # positions 0..N-1 of the sorted-txn list, silently failing
            # the window check for later categories whose invoices are
            # newer than the earliest transactions.
            available_txns = [t for t in txns if id(t) not in assignments]
            if len(available_txns) < 2 or len(candidate_invs) < 2:
                continue
            pairs = _min_lag_pairing(available_txns, candidate_invs, window)
            if pairs is not None:
                for t, i in pairs:
                    assignments[id(t)] = (cat_name, i)
    return assignments


def _min_lag_pairing(
    txns: list[Transaction],
    invs: list[Invoice],
    window: int,
) -> list[tuple[Transaction, Invoice]] | None:
    sorted_txns = sorted(txns, key=lambda t: t.date)
    sorted_invs = sorted(invs, key=_invoice_sort_key)
    n = min(len(sorted_txns), len(sorted_invs))
    best_pairs: list[tuple[Transaction, Invoice]] | None = None
    best_sum = -1
    if len(sorted_txns) <= len(sorted_invs):
        # Choose which n invoices to pair with all txns (sorted-zip within
        # the subset is optimal — see docstring of _resolve_chronological_buckets).
        for idx_subset in combinations(range(len(sorted_invs)), n):
            inv_sub = [sorted_invs[i] for i in idx_subset]
            pairs = list(zip(sorted_txns, inv_sub, strict=True))
            if not all(_pair_in_window(t, i, window) for t, i in pairs):
                continue
            total = sum(_pair_lag(t, i) for t, i in pairs)
            if best_pairs is None or total < best_sum:
                best_sum = total
                best_pairs = pairs
    else:
        # Choose which n txns to pair with all invoices (symmetric case).
        for idx_subset in combinations(range(len(sorted_txns)), n):
            tx_sub = [sorted_txns[i] for i in idx_subset]
            pairs = list(zip(tx_sub, sorted_invs, strict=True))
            if not all(_pair_in_window(t, i, window) for t, i in pairs):
                continue
            total = sum(_pair_lag(t, i) for t, i in pairs)
            if best_pairs is None or total < best_sum:
                best_sum = total
                best_pairs = pairs
    return best_pairs


def _pair_lag(txn: Transaction, inv: Invoice) -> int:
    if inv.invoice_date is not None:
        return abs((txn.date - inv.invoice_date).days)
    if txn.date < inv.period.start:
        return (inv.period.start - txn.date).days
    if txn.date > inv.period.end:
        return (txn.date - inv.period.end).days
    return 0


def _invoice_sort_key(inv: Invoice) -> tuple[date, date]:
    # Primary key: invoice_date (or period.start when no invoice_date).
    # Secondary key: period.start, so invoices sharing an invoice_date sort
    # by the period they cover instead of input order. Without this the
    # chronological-zip pairing would drift between runs based on filesystem
    # load order whenever multiple invoices share an invoice_date.
    primary = inv.invoice_date if inv.invoice_date is not None else inv.period.start
    return (primary, inv.period.start)


def _pair_in_window(txn: Transaction, inv: Invoice, window: int) -> bool:
    if inv.invoice_date is not None:
        delta = (txn.date - inv.invoice_date).days
        return 0 <= delta <= window
    latest = inv.period.end + timedelta(days=window)
    return inv.period.start <= txn.date <= latest


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
    mapping = resolve_manual_mapping(txn.description, txn.amount, config)
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
            # Skip invoices already claimed by an earlier transaction (either
            # directly matched or pre-assigned via chronological zip). Without
            # this guard a single invoice could be paired with multiple txns.
            if (cat_name, inv.content_hash) in consumed:
                continue
            if mapped_here:
                # User explicitly pinned this (description, amount) to this
                # category via manual_mapping. Trust the override and bypass
                # the window check — they've taken responsibility for the
                # match, and a window misalignment is exactly why someone
                # would write a manual mapping. Amount equality and the
                # not-consumed check above still apply.
                candidates.append((cat_name, inv))
            elif inv.invoice_date is not None:
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
    candidate_str = ", ".join(f"{c}:{i.source_path}" for c, i in candidates)
    alerts.append(
        Alert(
            kind=AlertKind.AMBIGUOUS_INVOICE_MATCH,
            message=(
                f"transaction {txn.date.isoformat()} '{txn.description}' "
                f"(amount={txn.amount}) has {len(candidates)} invoice candidates: "
                f"{candidate_str}"
            ),
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
