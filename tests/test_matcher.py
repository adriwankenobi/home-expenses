from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.config import (
    BankStatementsConfig,
    Category,
    Config,
    ManualMapping,
    SplitGroup,
)
from home_expenses.matcher import MatchResult, match
from home_expenses.models import AlertKind
from tests.factories import make_invoice, make_transaction


def _config(
    *,
    categories: dict[str, Category] | None = None,
    manual_mappings: dict[str, ManualMapping] | None = None,
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
    txn = make_transaction(
        date=date(2026, 5, 18), description="PEPE ENERGY INVOICE", amount=Decimal("11.11")
    )
    inv = make_invoice(amount=Decimal("11.11"), invoice_date=date(2026, 5, 10), parser="pepeenergy")
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("PEPE ENERGY INVOICE",),
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
        description="PEPE ENERGY INVOICE",
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
                patterns=("PEPE ENERGY INVOICE",),
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
        description="PEPE ENERGY INVOICE",
        amount=Decimal("11.11"),
    )
    inv1 = make_invoice(
        amount=Decimal("11.11"),
        invoice_date=date(2026, 5, 5),
        content_hash="h1",
    )
    inv2 = make_invoice(
        amount=Decimal("11.11"),
        invoice_date=date(2026, 5, 10),
        content_hash="h2",
    )
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("PEPE ENERGY INVOICE",),
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
        description="TAX INVOICE",
        amount=Decimal("50.00"),
    )
    cfg = _config(
        categories={
            "taxes": Category(
                name="taxes",
                recurrence="yearly",
                patterns=("TAX INVOICE",),
            )
        }
    )
    result = match([txn], {}, cfg)
    assert len(result.items) == 1
    assert result.items[0].category == "taxes"
    assert result.items[0].invoice is None
    # no alert: this category has no invoice_folder
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


def test_invoice_does_not_attach_on_coincidental_amount_when_no_pattern_or_mapping() -> None:
    # The txn description doesn't match the IBI pattern and isn't in
    # manual_mappings. Even though its amount equals the invoice amount,
    # the matcher must NOT attach the invoice — it would silently steal
    # an unrelated payment.
    txn = make_transaction(
        date=date(2026, 5, 15), description="UNRELATED TXN", amount=Decimal("250.00")
    )
    inv = make_invoice(
        amount=Decimal("250.00"),
        invoice_date=None,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
    )
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
                patterns=("IBI INVOICE",),
            )
        }
    )
    result = match([txn], {"ibi": [inv]}, cfg)
    # No pattern, no mapping → txn ends up unmatched, invoice stays orphan.
    assert result.items == ()
    assert any(a.kind is AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_manual_mapping_attaches_invoice_when_amount_and_period_align() -> None:
    # Manual mapping is the user's escape hatch: a description that doesn't
    # match the IBI pattern can still be pinned to IBI via the mapping, and
    # when it lands, the matcher attaches the invoice as if it were a
    # regular pattern match.
    txn = make_transaction(
        date=date(2026, 5, 15), description="ODDLY NAMED IBI 2026", amount=Decimal("250.00")
    )
    inv = make_invoice(
        amount=Decimal("250.00"),
        invoice_date=None,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
    )
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
                patterns=("IBI INVOICE",),
            )
        },
        manual_mappings={"ODDLY NAMED IBI 2026": ManualMapping(category="ibi")},
    )
    result = match([txn], {"ibi": [inv]}, cfg)
    assert len(result.items) == 1
    assert result.items[0].category == "ibi"
    assert result.items[0].invoice is inv
    assert all(a.kind is not AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_manual_mapping_used_as_last_resort() -> None:
    txn = make_transaction(description="MYSTERIOUS THING", amount=Decimal("3"))
    cfg = _config(
        categories={
            "other": Category(name="other", recurrence="none"),
        },
        manual_mappings={"MYSTERIOUS THING": ManualMapping(category="other")},
    )
    result = match([txn], {}, cfg)
    assert len(result.items) == 1
    assert result.items[0].category == "other"


def test_manual_mapping_applies_display_overrides_to_item() -> None:
    cfg = _config(
        categories={"fee": Category(name="fee", recurrence="quarterly")},
        manual_mappings={
            "UNIQUE ANNUAL CHARGE": ManualMapping(
                category="fee",
                recurrence="yearly",
                period_contains_payment=True,
            ),
        },
    )
    txn = make_transaction(
        date=date(2024, 9, 15),
        description="UNIQUE ANNUAL CHARGE",
        amount=Decimal("100.00"),
    )
    result = match([txn], {}, cfg)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.category == "fee"
    assert item.display_recurrence == "yearly"
    assert item.display_period_contains_payment is True


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
    txn = make_transaction(description="INVOICE ABC EXTRA", amount=Decimal("1"))
    cfg = _config(
        categories={
            "a": Category(name="a", recurrence="none", patterns=("INVOICE ABC",)),
            "b": Category(name="b", recurrence="none", patterns=("ABC EXTRA",)),
        }
    )
    result = match([txn], {}, cfg)
    assert any(a.kind is AlertKind.AMBIGUOUS_PATTERN_MATCH for a in result.alerts)


def test_per_category_window_override() -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="ELECTRICITY INVOICE",
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
                patterns=("ELECTRICITY INVOICE",),
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


def test_invoice_without_date_matches_payment_inside_period() -> None:
    # Invoice has no invoice_date: the matcher falls back to
    # period bounds. A payment dated inside the period must match.
    txn = make_transaction(
        date=date(2024, 10, 15), description="IBI INVOICE", amount=Decimal("250.00")
    )
    inv = make_invoice(
        amount=Decimal("250.00"),
        invoice_date=None,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
                patterns=("IBI INVOICE",),
            )
        }
    )
    result = match([txn], {"ibi": [inv]}, cfg)
    assert len(result.items) == 1
    assert result.items[0].invoice is inv


def test_invoice_without_date_matches_payment_just_after_period_end() -> None:
    # Payment falls after period.end but within the default match window.
    txn = make_transaction(
        date=date(2025, 1, 20), description="IBI INVOICE", amount=Decimal("250.00")
    )
    inv = make_invoice(
        amount=Decimal("250.00"),
        invoice_date=None,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
                patterns=("IBI INVOICE",),
            )
        }
    )
    result = match([txn], {"ibi": [inv]}, cfg)
    assert len(result.items) == 1
    assert result.items[0].invoice is inv


def test_invoice_without_date_does_not_match_payment_before_period_start() -> None:
    # A payment before the period.start must NOT match.
    txn = make_transaction(
        date=date(2023, 12, 15), description="IBI INVOICE", amount=Decimal("250.00")
    )
    inv = make_invoice(
        amount=Decimal("250.00"),
        invoice_date=None,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
    )
    cfg = _config(
        categories={
            "ibi": Category(
                name="ibi",
                recurrence="yearly",
                invoice_folder=Path("/tmp"),
                invoice_parser="ibi",
                patterns=("IBI INVOICE",),
            )
        }
    )
    result = match([txn], {"ibi": [inv]}, cfg)
    # Pattern still matches, so the txn becomes an Item — but the invoice
    # stays unattached because the txn date is before period.start.
    assert len(result.items) == 1
    assert result.items[0].invoice is None
    assert any(a.kind is AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_start_date_suppresses_expense_missing_invoice() -> None:
    # A pattern-matched transaction before the category's start_date should
    # NOT trigger expense_missing_invoice (the user wasn't yet contracted).
    txn = make_transaction(
        date=date(2023, 1, 15),
        description="PEPE ENERGY INVOICE",
        amount=Decimal("11.11"),
    )
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("PEPE ENERGY INVOICE",),
                start_date=date(2024, 1, 1),
            )
        }
    )
    result = match([txn], {}, cfg)
    assert len(result.items) == 1
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


def test_expense_missing_invoice_fires_on_or_after_start_date() -> None:
    txn = make_transaction(
        date=date(2024, 1, 1),
        description="PEPE ENERGY INVOICE",
        amount=Decimal("11.11"),
    )
    cfg = _config(
        categories={
            "electricity": Category(
                name="electricity",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("PEPE ENERGY INVOICE",),
                start_date=date(2024, 1, 1),
            )
        }
    )
    result = match([txn], {}, cfg)
    assert any(a.kind is AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


# --- split groups: invoice-based ---


def _split_pair_with_invoices(tmp: Path) -> dict[str, Category]:
    a_dir = tmp / "inv_a"
    a_dir.mkdir()
    b_dir = tmp / "inv_b"
    b_dir.mkdir()
    return {
        "a": Category(
            name="a",
            recurrence="quarterly",
            invoice_folder=a_dir,
            invoice_parser="pepeenergy",
            patterns=("QUARTERLY INVOICE",),
        ),
        "b": Category(
            name="b",
            recurrence="quarterly",
            invoice_folder=b_dir,
            invoice_parser="pepeenergy",
            patterns=("QUARTERLY INVOICE",),
        ),
    }


def _cfg_with_split_groups(
    *, categories: dict[str, Category], manual_mappings: dict[str, ManualMapping] | None = None
) -> Config:
    """_config(...) plus deriving SplitGroup tuples from shared patterns."""
    from collections import defaultdict

    owners: dict[str, list[str]] = defaultdict(list)
    for name, cat in categories.items():
        for p in cat.patterns:
            owners[p].append(name)

    patterns_by_peer_set: dict[frozenset[str], list[str]] = defaultdict(list)
    for pat, owner_list in owners.items():
        if len(owner_list) >= 2:
            patterns_by_peer_set[frozenset(owner_list)].append(pat)

    split_groups_list: list[SplitGroup] = []
    for peer_set, shared_patterns in patterns_by_peer_set.items():
        members_in_order = tuple(categories[n] for n in categories if n in peer_set)
        split_groups_list.append(
            SplitGroup(
                members=members_in_order,
                recurrence=members_in_order[0].recurrence,
                has_invoices=all(m.invoice_folder is not None for m in members_in_order),
                patterns=tuple(shared_patterns),
            )
        )
    split_groups = tuple(split_groups_list)

    cfg = _config(categories=categories, manual_mappings=manual_mappings)
    return Config(
        currency=cfg.currency,
        bank_statements=cfg.bank_statements,
        match_window_days_default=cfg.match_window_days_default,
        cache_dir=cfg.cache_dir,
        categories=cfg.categories,
        manual_mappings=cfg.manual_mappings,
        split_groups=split_groups,
    )


def test_split_group_invoice_match_resolves_to_member(tmp_path: Path) -> None:
    cats = _split_pair_with_invoices(tmp_path)
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="QUARTERLY INVOICE ACME",
        amount=Decimal("22.22"),
    )
    inv_b = make_invoice(
        amount=Decimal("22.22"), invoice_date=date(2026, 5, 10), parser="pepeenergy"
    )
    inv_a = make_invoice(
        amount=Decimal("99.00"), invoice_date=date(2026, 5, 10), parser="pepeenergy"
    )
    result = match([txn], {"a": [inv_a], "b": [inv_b]}, cfg)
    assert len(result.items) == 1
    assert result.items[0].category == "b"
    assert result.items[0].invoice is inv_b
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)


def test_split_group_invoice_no_match_emits_missing_per_active_member(tmp_path: Path) -> None:
    cats = _split_pair_with_invoices(tmp_path)
    # b is gated by start_date in the future relative to txn.date
    cats["b"] = Category(
        name="b",
        recurrence=cats["b"].recurrence,
        invoice_folder=cats["b"].invoice_folder,
        invoice_parser=cats["b"].invoice_parser,
        patterns=cats["b"].patterns,
        start_date=date(2027, 1, 1),
    )
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="QUARTERLY INVOICE ACME",
        amount=Decimal("22.22"),
    )
    result = match([txn], {"a": [], "b": []}, cfg)
    # No item produced; one missing-invoice alert for active member 'a' only.
    assert len(result.items) == 0
    missing = [a for a in result.alerts if a.kind is AlertKind.EXPENSE_MISSING_INVOICE]
    assert len(missing) == 1
    assert missing[0].payload["category"] == "a"
    # txn is in unmatched
    assert txn in result.unmatched_transactions


def test_split_group_invoice_ambiguous_when_two_members_match(tmp_path: Path) -> None:
    cats = _split_pair_with_invoices(tmp_path)
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="QUARTERLY INVOICE ACME",
        amount=Decimal("33.33"),
    )
    inv_a = make_invoice(
        amount=Decimal("33.33"), invoice_date=date(2026, 5, 10), parser="pepeenergy"
    )
    inv_b = make_invoice(
        amount=Decimal("33.33"), invoice_date=date(2026, 5, 10), parser="pepeenergy"
    )
    result = match([txn], {"a": [inv_a], "b": [inv_b]}, cfg)

    assert len(result.items) == 0
    ambig = [a for a in result.alerts if a.kind is AlertKind.AMBIGUOUS_INVOICE_MATCH]
    assert len(ambig) == 1
    assert txn in result.unmatched_transactions


# --- split groups: rank-based ---


def _split_pair_no_invoices() -> dict[str, Category]:
    return {
        "high": Category(
            name="high",
            recurrence="quarterly",
            patterns=("QUARTERLY INVOICE",),
        ),
        "low": Category(
            name="low",
            recurrence="quarterly",
            patterns=("QUARTERLY INVOICE",),
        ),
    }


def test_split_group_rank_assigns_higher_amount_to_first_member() -> None:
    cfg = _cfg_with_split_groups(categories=_split_pair_no_invoices())
    big = make_transaction(
        date=date(2026, 5, 18),
        description="QUARTERLY INVOICE X",
        amount=Decimal("80.00"),
    )
    small = make_transaction(
        date=date(2026, 5, 20),
        description="QUARTERLY INVOICE X",
        amount=Decimal("20.00"),
    )
    result = match([big, small], {}, cfg)

    by_cat = {it.category: it for it in result.items}
    assert by_cat["high"].transaction is big
    assert by_cat["low"].transaction is small
    assert all(
        a.kind not in (AlertKind.AMBIGUOUS_SPLIT_BUCKET, AlertKind.SPLIT_AMOUNT_TIE)
        for a in result.alerts
    )


def test_split_group_rank_only_one_member_active_routes_lone_txn() -> None:
    cats = _split_pair_no_invoices()
    cats["low"] = Category(
        name="low",
        recurrence="quarterly",
        patterns=cats["low"].patterns,
        start_date=date(2027, 1, 1),  # not yet active in 2026-Q2
    )
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="QUARTERLY INVOICE Y",
        amount=Decimal("50.00"),
    )
    result = match([txn], {}, cfg)

    assert len(result.items) == 1
    assert result.items[0].category == "high"
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)
    assert not result.unmatched_transactions


def test_split_group_rank_too_few_txns_alerts_and_leaves_unmatched() -> None:
    cfg = _cfg_with_split_groups(categories=_split_pair_no_invoices())
    # Two members both active; only one txn in the period.
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="QUARTERLY INVOICE Z",
        amount=Decimal("40.00"),
    )
    result = match([txn], {}, cfg)

    assert len(result.items) == 0
    ambig = [a for a in result.alerts if a.kind is AlertKind.AMBIGUOUS_SPLIT_BUCKET]
    assert len(ambig) == 1
    assert ambig[0].payload["period"] == "2026-Q2"
    assert ambig[0].payload["expected"] == 2
    assert ambig[0].payload["actual"] == 1
    assert txn in result.unmatched_transactions


def test_split_group_rank_too_many_txns_alerts() -> None:
    cfg = _cfg_with_split_groups(categories=_split_pair_no_invoices())
    txns = [
        make_transaction(
            date=date(2026, 5, 1),
            description="QUARTERLY INVOICE A",
            amount=Decimal("10.00"),
        ),
        make_transaction(
            date=date(2026, 5, 10),
            description="QUARTERLY INVOICE B",
            amount=Decimal("20.00"),
        ),
        make_transaction(
            date=date(2026, 5, 20),
            description="QUARTERLY INVOICE C",
            amount=Decimal("30.00"),
        ),
    ]
    result = match(txns, {}, cfg)

    assert len(result.items) == 0
    ambig = [a for a in result.alerts if a.kind is AlertKind.AMBIGUOUS_SPLIT_BUCKET]
    assert len(ambig) == 1
    assert ambig[0].payload["actual"] == 3
    assert all(t in result.unmatched_transactions for t in txns)


def test_split_group_rank_three_way_split() -> None:
    cats = {
        "highest": Category(
            name="highest", recurrence="quarterly", patterns=("QUARTERLY INVOICE",)
        ),
        "middle": Category(name="middle", recurrence="quarterly", patterns=("QUARTERLY INVOICE",)),
        "lowest": Category(name="lowest", recurrence="quarterly", patterns=("QUARTERLY INVOICE",)),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    txns = [
        make_transaction(
            date=date(2026, 5, 1),
            description="QUARTERLY INVOICE A",
            amount=Decimal("100.00"),
        ),
        make_transaction(
            date=date(2026, 5, 10),
            description="QUARTERLY INVOICE B",
            amount=Decimal("30.00"),
        ),
        make_transaction(
            date=date(2026, 5, 20),
            description="QUARTERLY INVOICE C",
            amount=Decimal("60.00"),
        ),
    ]
    result = match(txns, {}, cfg)

    by_cat = {it.category: it for it in result.items}
    assert by_cat["highest"].transaction.amount == Decimal("100.00")
    assert by_cat["middle"].transaction.amount == Decimal("60.00")
    assert by_cat["lowest"].transaction.amount == Decimal("30.00")


def test_split_group_rank_independent_buckets_across_periods() -> None:
    cfg = _cfg_with_split_groups(categories=_split_pair_no_invoices())
    q1_big = make_transaction(
        date=date(2026, 2, 5),
        description="QUARTERLY INVOICE X",
        amount=Decimal("90.00"),
    )
    q1_small = make_transaction(
        date=date(2026, 2, 8),
        description="QUARTERLY INVOICE X",
        amount=Decimal("10.00"),
    )
    q2_big = make_transaction(
        date=date(2026, 5, 5),
        description="QUARTERLY INVOICE X",
        amount=Decimal("70.00"),
    )
    q2_small = make_transaction(
        date=date(2026, 5, 8),
        description="QUARTERLY INVOICE X",
        amount=Decimal("20.00"),
    )
    result = match([q1_big, q1_small, q2_big, q2_small], {}, cfg)

    assert len(result.items) == 4
    by_txn = {it.transaction: it.category for it in result.items}
    assert by_txn[q1_big] == "high"
    assert by_txn[q1_small] == "low"
    assert by_txn[q2_big] == "high"
    assert by_txn[q2_small] == "low"


def test_split_group_rank_amount_tie_alerts_and_assigns_by_date() -> None:
    cfg = _cfg_with_split_groups(categories=_split_pair_no_invoices())
    earlier = make_transaction(
        date=date(2026, 5, 10),
        description="QUARTERLY INVOICE",
        amount=Decimal("50.00"),
    )
    later = make_transaction(
        date=date(2026, 5, 20),
        description="QUARTERLY INVOICE",
        amount=Decimal("50.00"),
    )
    result = match([earlier, later], {}, cfg)

    by_cat = {it.category: it.transaction for it in result.items}
    # Tie-break by date ascending: earlier wins the higher-rank slot ("high").
    assert by_cat["high"] is earlier
    assert by_cat["low"] is later

    ties = [a for a in result.alerts if a.kind is AlertKind.SPLIT_AMOUNT_TIE]
    assert len(ties) == 1
    assert ties[0].payload["period"] == "2026-Q2"
    assert len(ties[0].payload["transactions"]) == 2


def test_split_group_non_shared_pattern_routes_to_sole_owner() -> None:
    cats = {
        "high": Category(
            name="high",
            recurrence="quarterly",
            patterns=("QUARTERLY INVOICE", "ONE TIME EXTRA"),
        ),
        "low": Category(
            name="low",
            recurrence="quarterly",
            patterns=("QUARTERLY INVOICE",),
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    solo = make_transaction(
        date=date(2026, 5, 18),
        description="ONE TIME EXTRA PAYMENT",
        amount=Decimal("99.00"),
    )
    result = match([solo], {}, cfg)

    assert len(result.items) == 1
    assert result.items[0].category == "high"
    assert not result.unmatched_transactions
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)
    assert all(a.kind is not AlertKind.AMBIGUOUS_PATTERN_MATCH for a in result.alerts)
