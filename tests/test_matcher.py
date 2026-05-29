from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.config import (
    BankStatementsConfig,
    Category,
    Config,
    ManualMapping,
    SplitGroup,
    SplitGroupKind,
)
from home_expenses.matcher import MatchResult, match
from home_expenses.models import AlertKind
from tests.factories import make_invoice, make_transaction


def _config(
    *,
    categories: dict[str, Category] | None = None,
    manual_mappings: Mapping[str, ManualMapping | tuple[ManualMapping, ...]] | None = None,
) -> Config:
    wrapped: dict[str, tuple[ManualMapping, ...]] = {
        k: (v if isinstance(v, tuple) else (v,))
        for k, v in (manual_mappings or {}).items()
    }
    return Config(
        currency="EUR",
        bank_statements=BankStatementsConfig(dir=Path("/tmp"), glob="*.csv"),
        match_window_days_default=30,
        cache_dir=Path("/tmp/cache"),
        categories=categories or {},
        manual_mappings=wrapped,
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


def test_manual_mapping_bypasses_window_for_invoice_match() -> None:
    # An invoice exists with matching amount in the category's folder but
    # its invoice_date is far from the bank charge (outside match_window).
    # Without a manual_mapping the txn would route to the category but
    # without an invoice (the existing test above covers that). With a
    # manual_mapping pinning (description, amount) to this category, the
    # window check is bypassed and the invoice is attached.
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="PEPE ENERGY INVOICE",
        amount=Decimal("11.11"),
    )
    inv = make_invoice(
        amount=Decimal("11.11"),
        invoice_date=date(2025, 1, 1),  # outside the default 30-day window
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
        },
        manual_mappings={
            "PEPE ENERGY INVOICE": ManualMapping(
                category="electricity", amount=Decimal("11.11")
            ),
        },
    )
    result = match([txn], {"electricity": [inv]}, cfg)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.category == "electricity"
    assert item.invoice is inv
    assert all(
        a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts
    )


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
        source_path="/tmp/inv1.pdf",
    )
    inv2 = make_invoice(
        amount=Decimal("11.11"),
        invoice_date=date(2026, 5, 10),
        content_hash="h2",
        source_path="/tmp/inv2.pdf",
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
    ambig = [a for a in result.alerts if a.kind is AlertKind.AMBIGUOUS_INVOICE_MATCH]
    assert len(ambig) == 1
    msg = ambig[0].message
    # Message must identify the transaction (date, description, amount) and
    # list every candidate (category + source path) so the user can resolve it.
    assert "2026-05-18" in msg
    assert "PEPE ENERGY INVOICE" in msg
    assert "11.11" in msg
    assert "/tmp/inv1.pdf" in msg
    assert "/tmp/inv2.pdf" in msg
    assert "electricity" in msg
    # also fires missing-invoice because pattern matched but no invoice attached
    assert any(a.kind is AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)
    # Ambiguous candidates remain pending; they MUST NOT be flagged orphan.
    assert all(a.kind is not AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_chronological_zip_resolves_same_amount_pair() -> None:
    # Two transactions share an amount; each invoice fits the window of one
    # transaction but the earlier invoice also fits the earlier transaction's
    # wider window. Single-pass greedy matching would flag the earlier
    # transaction ambiguous. Chronological zip pairs them in order.
    tx_a = make_transaction(
        date=date(2024, 9, 2),
        description="CCPP INVOICE",
        amount=Decimal("150.00"),
    )
    tx_b = make_transaction(
        date=date(2024, 9, 5),
        description="CCPP INVOICE",
        amount=Decimal("150.00"),
    )
    inv_a = make_invoice(
        amount=Decimal("150.00"),
        invoice_date=date(2024, 4, 30),
        period_start=date(2023, 11, 15),
        period_end=date(2024, 2, 9),
        content_hash="hA",
        source_path="/tmp/invA.pdf",
    )
    inv_b = make_invoice(
        amount=Decimal("150.00"),
        invoice_date=date(2024, 7, 30),
        period_start=date(2024, 5, 15),
        period_end=date(2024, 7, 17),
        content_hash="hB",
        source_path="/tmp/invB.pdf",
    )
    cfg = _config(
        categories={
            "building": Category(
                name="building",
                recurrence="quarterly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("CCPP INVOICE",),
                match_window_days=125,
            )
        }
    )
    result = match([tx_a, tx_b], {"building": [inv_a, inv_b]}, cfg)
    assert len(result.items) == 2
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[tx_a].invoice is inv_a
    assert by_txn[tx_b].invoice is inv_b
    assert all(a.kind is not AlertKind.AMBIGUOUS_INVOICE_MATCH for a in result.alerts)
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)
    assert all(a.kind is not AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_chronological_zip_three_transactions_three_invoices() -> None:
    # Generalisation: N>=2 case must work for any N, not just 2.
    # Window is wide enough that each transaction in isolation would see
    # every invoice as a candidate; only the cross-bucket chronological zip
    # produces an unambiguous assignment.
    txns = [
        make_transaction(
            date=d, description="CCPP INVOICE", amount=Decimal("99.00")
        )
        for d in (date(2024, 4, 1), date(2024, 7, 1), date(2024, 10, 1))
    ]
    invs = [
        make_invoice(
            amount=Decimal("99.00"),
            invoice_date=inv_d,
            content_hash=h,
            source_path=p,
        )
        for inv_d, h, p in [
            (date(2024, 3, 15), "h1", "/tmp/i1.pdf"),
            (date(2024, 6, 15), "h2", "/tmp/i2.pdf"),
            (date(2024, 9, 15), "h3", "/tmp/i3.pdf"),
        ]
    ]
    cfg = _config(
        categories={
            "building": Category(
                name="building",
                recurrence="quarterly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("CCPP INVOICE",),
                match_window_days=365,
            )
        }
    )
    result = match(txns, {"building": invs}, cfg)
    assert len(result.items) == 3
    pairs = {item.transaction: item.invoice for item in result.items}
    assert pairs[txns[0]] is invs[0]
    assert pairs[txns[1]] is invs[1]
    assert pairs[txns[2]] is invs[2]
    assert all(a.kind is not AlertKind.AMBIGUOUS_INVOICE_MATCH for a in result.alerts)


def test_chronological_zip_tiebreaks_by_period_start_when_invoice_dates_equal() -> None:
    # Two invoices share an invoice_date but cover different periods. The
    # chronological-zip sort must break the tie by period.start so the pairing
    # is deterministic and not dependent on filesystem/load order.
    tx_early = make_transaction(
        date=date(2024, 6, 1),
        description="CCPP INVOICE",
        amount=Decimal("50.00"),
    )
    tx_late = make_transaction(
        date=date(2024, 7, 1),
        description="CCPP INVOICE",
        amount=Decimal("50.00"),
    )
    inv_late_period = make_invoice(
        amount=Decimal("50.00"),
        invoice_date=date(2024, 5, 30),
        period_start=date(2024, 4, 1),
        period_end=date(2024, 4, 30),
        content_hash="h_late_period",
        source_path="/tmp/inv_late.pdf",
    )
    inv_early_period = make_invoice(
        amount=Decimal("50.00"),
        invoice_date=date(2024, 5, 30),
        period_start=date(2024, 2, 1),
        period_end=date(2024, 2, 28),
        content_hash="h_early_period",
        source_path="/tmp/inv_early.pdf",
    )
    cfg = _config(
        categories={
            "building": Category(
                name="building",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("CCPP INVOICE",),
                match_window_days=60,
            )
        }
    )
    # Pass invoices in the OPPOSITE order from period.start so a naive sort
    # (by invoice_date only) would tie-break on input order and pair tx_early
    # with inv_late_period. A correct sort tie-breaks on period.start.
    result = match(
        [tx_early, tx_late],
        {"building": [inv_late_period, inv_early_period]},
        cfg,
    )
    assert len(result.items) == 2
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[tx_early].invoice is inv_early_period
    assert by_txn[tx_late].invoice is inv_late_period


def test_chronological_zip_orphans_worst_fit_when_invoices_outnumber_txns() -> None:
    # Real-world case: 4 invoices share an amount but only 3 txns are visible
    # (the 4th invoice covers a billing period whose payment predates the bank
    # statements). A naive sorted-zip from the start of both lists silently
    # orphans the *newest* invoice, even when the *oldest* is the one without
    # a matching txn. Min-lag assignment picks the subset of invoices whose
    # sorted pairing minimizes total |txn.date - inv.invoice_date|, leaving
    # the worst-fit invoice (here, the oldest) as orphan.
    txns = [
        make_transaction(date=d, description="CCPP INVOICE", amount=Decimal("30.00"))
        for d in (date(2026, 3, 3), date(2026, 4, 13), date(2026, 5, 6))
    ]
    inv_old = make_invoice(
        amount=Decimal("30.00"),
        invoice_date=date(2025, 12, 1),
        content_hash="h_old",
        source_path="/tmp/old.pdf",
    )
    inv_recent = [
        make_invoice(
            amount=Decimal("30.00"),
            invoice_date=d,
            content_hash=h,
            source_path=p,
        )
        for d, h, p in [
            (date(2026, 2, 23), "h1", "/tmp/i1.pdf"),
            (date(2026, 3, 31), "h2", "/tmp/i2.pdf"),
            (date(2026, 4, 21), "h3", "/tmp/i3.pdf"),
        ]
    ]
    cfg = _config(
        categories={
            "building": Category(
                name="building",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("CCPP INVOICE",),
                match_window_days=130,
            )
        }
    )
    result = match(txns, {"building": [inv_old, *inv_recent]}, cfg)
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[txns[0]].invoice is inv_recent[0]
    assert by_txn[txns[1]].invoice is inv_recent[1]
    assert by_txn[txns[2]].invoice is inv_recent[2]
    orphans = [a for a in result.alerts if a.kind is AlertKind.ORPHAN_INVOICE]
    assert len(orphans) == 1
    assert orphans[0].payload["source_path"] == "/tmp/old.pdf"


def test_invoice_never_matched_to_more_than_one_transaction() -> None:
    # Three transactions share an amount/category but only two invoices exist.
    # Chronological zip pairs the first two transactions with the two invoices;
    # the third transaction's _try_invoice_match must NOT claim a third "match"
    # by re-using an already-assigned invoice.
    txns = [
        make_transaction(
            date=d, description="CCPP INVOICE", amount=Decimal("100.00")
        )
        for d in (date(2024, 1, 10), date(2024, 2, 10), date(2024, 2, 15))
    ]
    invs = [
        make_invoice(
            amount=Decimal("100.00"),
            invoice_date=inv_d,
            content_hash=h,
            source_path=p,
        )
        for inv_d, h, p in [
            (date(2024, 1, 1), "h1", "/tmp/i1.pdf"),
            (date(2024, 2, 1), "h2", "/tmp/i2.pdf"),
        ]
    ]
    cfg = _config(
        categories={
            "building": Category(
                name="building",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("CCPP INVOICE",),
                match_window_days=20,
            )
        }
    )
    result = match(txns, {"building": invs}, cfg)
    # Each invoice may appear in at most one item.
    invoices_used = [item.invoice for item in result.items if item.invoice is not None]
    assert len(invoices_used) == len(set(id(i) for i in invoices_used)), (
        f"invoice matched to multiple transactions: {invoices_used}"
    )


def test_chronological_zip_reserves_invoices_before_main_loop_runs() -> None:
    # Real-world ordering bug: when a not-pre-assigned txn appears EARLIER in
    # the input list than the pre-assigned txns whose invoices it would also
    # match within the window, per-txn matching saw those invoices as still
    # available (consumed was populated lazily during the main loop) and
    # emitted AMBIGUOUS_INVOICE_MATCH. The chrono-zip promise must be reified
    # into `consumed` BEFORE the main loop iterates.
    txn_late = make_transaction(
        date=date(2025, 6, 1),
        description="CCPP INVOICE",
        amount=Decimal("35.00"),
    )
    txn_early1 = make_transaction(
        date=date(2025, 1, 20),
        description="CCPP INVOICE",
        amount=Decimal("35.00"),
    )
    txn_early2 = make_transaction(
        date=date(2025, 3, 20),
        description="CCPP INVOICE",
        amount=Decimal("35.00"),
    )
    inv1 = make_invoice(
        amount=Decimal("35.00"),
        invoice_date=date(2025, 1, 15),
        content_hash="h1",
        source_path="/tmp/i1.pdf",
    )
    inv2 = make_invoice(
        amount=Decimal("35.00"),
        invoice_date=date(2025, 3, 15),
        content_hash="h2",
        source_path="/tmp/i2.pdf",
    )
    cfg = _config(
        categories={
            "building": Category(
                name="building",
                recurrence="monthly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("CCPP INVOICE",),
                match_window_days=200,
            )
        }
    )
    # Critical: txn_late appears FIRST in the input list. Chrono-zip pairs
    # txn_early1↔inv1 and txn_early2↔inv2; both invoices must be reserved
    # before txn_late's per-txn matching runs, otherwise it sees both as
    # candidates and emits AMBIGUOUS.
    result = match(
        [txn_late, txn_early1, txn_early2],
        {"building": [inv1, inv2]},
        cfg,
    )
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[txn_early1].invoice is inv1
    assert by_txn[txn_early2].invoice is inv2
    # txn_late must NOT see inv1/inv2 as candidates — they belong to the
    # pre-assigned earlier txns. The bug surfaces as a spurious AMBIGUOUS alert.
    ambig = [
        a.message for a in result.alerts if a.kind is AlertKind.AMBIGUOUS_INVOICE_MATCH
    ]
    assert ambig == [], f"unexpected ambiguous alerts: {ambig}"


def test_chronological_zip_falls_back_when_pairing_violates_window() -> None:
    # Two txns, two invoices, but the chronological pairing for the earlier
    # txn would exceed the window. No valid full assignment exists, so the
    # resolution must NOT silently fabricate matches — fall back to existing
    # per-transaction matching behavior.
    tx_a = make_transaction(
        date=date(2024, 9, 2),
        description="CCPP INVOICE",
        amount=Decimal("150.00"),
    )
    tx_b = make_transaction(
        date=date(2024, 9, 5),
        description="CCPP INVOICE",
        amount=Decimal("150.00"),
    )
    # inv_a is far too old for tx_a (delta = 125 days, window = 30).
    inv_a = make_invoice(
        amount=Decimal("150.00"),
        invoice_date=date(2024, 4, 30),
        content_hash="hA",
        source_path="/tmp/invA.pdf",
    )
    inv_b = make_invoice(
        amount=Decimal("150.00"),
        invoice_date=date(2024, 8, 25),
        content_hash="hB",
        source_path="/tmp/invB.pdf",
    )
    cfg = _config(
        categories={
            "building": Category(
                name="building",
                recurrence="quarterly",
                invoice_folder=Path("/tmp"),
                invoice_parser="pepeenergy",
                patterns=("CCPP INVOICE",),
                match_window_days=30,
            )
        }
    )
    result = match([tx_a, tx_b], {"building": [inv_a, inv_b]}, cfg)
    # The chronological zip would pair (tx_a, inv_a) which is out of window,
    # so it must not be used. Tx_a should not be silently assigned inv_a.
    by_txn = {item.transaction: item for item in result.items}
    if tx_a in by_txn:
        assert by_txn[tx_a].invoice is not inv_a


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
    *,
    categories: dict[str, Category],
    manual_mappings: Mapping[str, ManualMapping | tuple[ManualMapping, ...]] | None = None,
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
        has_inv_count = sum(
            1 for m in members_in_order if m.invoice_folder is not None
        )
        kind: SplitGroupKind
        if has_inv_count == len(members_in_order):
            kind = "all_invoiced"
        elif has_inv_count == 0:
            kind = "none_invoiced"
        else:
            kind = "mixed"
        split_groups_list.append(
            SplitGroup(
                members=members_in_order,
                recurrence=members_in_order[0].recurrence,
                kind=kind,
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


def test_split_group_recurrence_none_routes_by_amount(tmp_path: Path) -> None:
    # Two distinct one-off categories share a transaction description but
    # each has its own invoice with a different amount. Recurrence "none" is
    # legal here because amount alone disambiguates which category each
    # transaction belongs to — the rank-fallback path never fires.
    a_dir = tmp_path / "inv_a"
    a_dir.mkdir()
    b_dir = tmp_path / "inv_b"
    b_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="none",
            invoice_folder=a_dir,
            invoice_parser="pepeenergy",
            patterns=("ONEOFF INVOICE",),
        ),
        "b": Category(
            name="b",
            recurrence="none",
            invoice_folder=b_dir,
            invoice_parser="pepeenergy",
            patterns=("ONEOFF INVOICE",),
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    txn_a = make_transaction(
        date=date(2026, 5, 18),
        description="ONEOFF INVOICE ACME",
        amount=Decimal("42.00"),
    )
    txn_b = make_transaction(
        date=date(2026, 6, 18),
        description="ONEOFF INVOICE ACME",
        amount=Decimal("99.00"),
    )
    inv_a = make_invoice(
        amount=Decimal("42.00"), invoice_date=date(2026, 5, 10), parser="pepeenergy"
    )
    inv_b = make_invoice(
        amount=Decimal("99.00"), invoice_date=date(2026, 6, 10), parser="pepeenergy"
    )
    result = match([txn_a, txn_b], {"a": [inv_a], "b": [inv_b]}, cfg)
    assert len(result.items) == 2
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[txn_a].category == "a"
    assert by_txn[txn_a].invoice is inv_a
    assert by_txn[txn_b].category == "b"
    assert by_txn[txn_b].invoice is inv_b
    assert all(a.kind is not AlertKind.AMBIGUOUS_INVOICE_MATCH for a in result.alerts)
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)


def test_split_group_4_txns_4_invoices_all_in_cat_a_window_130(tmp_path: Path) -> None:
    # Mirrors the user-reported scenario exactly:
    # - Two categories share the pattern (split group).
    # - All 4 transactions and all 4 invoices belong only to cat 'a'.
    # - cat 'b' has invoices at a different amount (irrelevant filler).
    # - 4 transactions of equal amount on 2 distinct dates (May 13 ×2, Sep 5 ×2).
    # - 4 invoices: three with invoice_date 2024-04-30 (different periods),
    #   one with invoice_date 2024-05-28.
    # - match_window_days = 130 on cat 'a'.
    # Expectation: all 4 match via chronological-zip, no AMBIGUOUS/MISSING/ORPHAN.
    a_dir = tmp_path / "inv_a"
    a_dir.mkdir()
    b_dir = tmp_path / "inv_b"
    b_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="none",
            invoice_folder=a_dir,
            invoice_parser="pepeenergy",
            patterns=("CCPP INVOICE",),
            match_window_days=130,
        ),
        "b": Category(
            name="b",
            recurrence="none",
            invoice_folder=b_dir,
            invoice_parser="pepeenergy",
            patterns=("CCPP INVOICE",),
            match_window_days=130,
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    amt = Decimal("75.00")
    t1 = make_transaction(date=date(2024, 5, 13), description="CCPP INVOICE", amount=amt)
    t2 = make_transaction(date=date(2024, 5, 13), description="CCPP INVOICE", amount=amt)
    t3 = make_transaction(date=date(2024, 9, 5), description="CCPP INVOICE", amount=amt)
    t4 = make_transaction(date=date(2024, 9, 5), description="CCPP INVOICE", amount=amt)
    i1 = make_invoice(
        amount=amt,
        invoice_date=date(2024, 4, 30),
        period_start=date(2023, 11, 15),
        period_end=date(2024, 2, 9),
        content_hash="h1",
        source_path="/tmp/i1.pdf",
    )
    i2 = make_invoice(
        amount=amt,
        invoice_date=date(2024, 4, 30),
        period_start=date(2024, 2, 9),
        period_end=date(2024, 3, 11),
        content_hash="h2",
        source_path="/tmp/i2.pdf",
    )
    i3 = make_invoice(
        amount=amt,
        invoice_date=date(2024, 4, 30),
        period_start=date(2024, 3, 11),
        period_end=date(2024, 4, 11),
        content_hash="h3",
        source_path="/tmp/i3.pdf",
    )
    i4 = make_invoice(
        amount=amt,
        invoice_date=date(2024, 5, 28),
        period_start=date(2024, 4, 11),
        period_end=date(2024, 5, 15),
        content_hash="h4",
        source_path="/tmp/i4.pdf",
    )
    # Irrelevant cat 'b' invoice at a different amount.
    b_irrelevant = make_invoice(
        amount=Decimal("99.00"),
        invoice_date=date(2024, 1, 1),
        content_hash="hBirr",
        source_path="/tmp/b_irr.pdf",
    )
    result = match(
        [t1, t2, t3, t4],
        {"a": [i1, i2, i3, i4], "b": [b_irrelevant]},
        cfg,
    )
    assert len(result.items) == 4, (
        f"expected 4 matches, got {len(result.items)} matches; alerts={result.alerts}"
    )
    matched_invs = {item.invoice for item in result.items}
    assert matched_invs == {i1, i2, i3, i4}, f"matched={matched_invs}"
    # The only orphan should be the irrelevant cat 'b' filler invoice, not
    # any of the 4 in cat 'a' that the user reported as orphan in production.
    orphan_cats = [
        a.payload.get("category")
        for a in result.alerts
        if a.kind is AlertKind.ORPHAN_INVOICE
    ]
    assert orphan_cats == ["b"], f"unexpected orphans: {orphan_cats}"


def test_split_group_chronological_zip_when_invoices_spread_across_cats(
    tmp_path: Path,
) -> None:
    # Two categories share a pattern. 4 transactions share an amount; the
    # 4 invoices that match that amount are spread 2/2 across the two
    # categories. The chronological-zip pre-pass should pair T1/T2 with
    # cat 'a's invoices and T3/T4 with cat 'b's invoices — not try to
    # pair T1/T2 against both cats' invoice lists (which would silently
    # fail when one of cat B's invoices is newer than T2).
    a_dir = tmp_path / "inv_a"
    a_dir.mkdir()
    b_dir = tmp_path / "inv_b"
    b_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="none",
            invoice_folder=a_dir,
            invoice_parser="pepeenergy",
            patterns=("CCPP INVOICE",),
            match_window_days=130,
        ),
        "b": Category(
            name="b",
            recurrence="none",
            invoice_folder=b_dir,
            invoice_parser="pepeenergy",
            patterns=("CCPP INVOICE",),
            match_window_days=130,
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    amt = Decimal("75.00")
    t1 = make_transaction(date=date(2024, 5, 13), description="CCPP INVOICE", amount=amt)
    t2 = make_transaction(date=date(2024, 5, 13), description="CCPP INVOICE", amount=amt)
    t3 = make_transaction(date=date(2024, 9, 5), description="CCPP INVOICE", amount=amt)
    t4 = make_transaction(date=date(2024, 9, 5), description="CCPP INVOICE", amount=amt)
    inv_a1 = make_invoice(
        amount=Decimal("75.00"),
        invoice_date=date(2024, 4, 30),
        period_start=date(2023, 11, 15),
        period_end=date(2024, 2, 9),
        content_hash="hA1",
        source_path="/tmp/A1.pdf",
    )
    inv_a2 = make_invoice(
        amount=Decimal("75.00"),
        invoice_date=date(2024, 4, 30),
        period_start=date(2024, 2, 9),
        period_end=date(2024, 3, 11),
        content_hash="hA2",
        source_path="/tmp/A2.pdf",
    )
    inv_b1 = make_invoice(
        amount=Decimal("75.00"),
        invoice_date=date(2024, 4, 30),
        period_start=date(2024, 3, 11),
        period_end=date(2024, 4, 11),
        content_hash="hB1",
        source_path="/tmp/B1.pdf",
    )
    inv_b2 = make_invoice(
        amount=Decimal("75.00"),
        invoice_date=date(2024, 5, 28),
        period_start=date(2024, 4, 11),
        period_end=date(2024, 5, 15),
        content_hash="hB2",
        source_path="/tmp/B2.pdf",
    )
    result = match(
        [t1, t2, t3, t4],
        {"a": [inv_a1, inv_a2], "b": [inv_b1, inv_b2]},
        cfg,
    )
    assert len(result.items) == 4, f"expected 4 matches, got alerts={result.alerts}"
    by_txn = {item.transaction: item for item in result.items}
    # T1, T2 should be matched to cat 'a's invoices.
    assert by_txn[t1].category == "a"
    assert by_txn[t2].category == "a"
    # T3, T4 should be matched to cat 'b's invoices.
    assert by_txn[t3].category == "b"
    assert by_txn[t4].category == "b"
    # No alerts at all.
    assert all(a.kind is not AlertKind.AMBIGUOUS_INVOICE_MATCH for a in result.alerts)
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)
    assert all(a.kind is not AlertKind.ORPHAN_INVOICE for a in result.alerts)


def test_split_group_chronological_zip_resolves_same_amount_bucket(tmp_path: Path) -> None:
    # Two categories share a pattern (split group), each with its own invoice
    # folder. All 4 transactions and matching invoices land in category 'a';
    # category 'b' has invoices but at a different amount. Single-pass
    # restricted matching would flag the early txn as having 2 candidates,
    # but the chronological-zip pre-pass can resolve the bucket unambiguously.
    a_dir = tmp_path / "inv_a"
    a_dir.mkdir()
    b_dir = tmp_path / "inv_b"
    b_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="none",
            invoice_folder=a_dir,
            invoice_parser="pepeenergy",
            patterns=("CCPP INVOICE",),
            match_window_days=125,
        ),
        "b": Category(
            name="b",
            recurrence="none",
            invoice_folder=b_dir,
            invoice_parser="pepeenergy",
            patterns=("CCPP INVOICE",),
            match_window_days=125,
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    tx_a = make_transaction(
        date=date(2024, 9, 2),
        description="CCPP INVOICE",
        amount=Decimal("150.00"),
    )
    tx_b = make_transaction(
        date=date(2024, 9, 5),
        description="CCPP INVOICE",
        amount=Decimal("150.00"),
    )
    inv_a = make_invoice(
        amount=Decimal("150.00"),
        invoice_date=date(2024, 4, 30),
        content_hash="hA",
        source_path="/tmp/invA.pdf",
    )
    inv_b = make_invoice(
        amount=Decimal("150.00"),
        invoice_date=date(2024, 7, 30),
        content_hash="hB",
        source_path="/tmp/invB.pdf",
    )
    # Cat 'b' has an unrelated invoice at a different amount (irrelevant to
    # this bucket, but keeps the split group's kind == "all_invoiced" path live).
    inv_b_unrelated = make_invoice(
        amount=Decimal("99.00"),
        invoice_date=date(2024, 1, 1),
        content_hash="hBunrel",
        source_path="/tmp/invB_unrel.pdf",
    )
    result = match(
        [tx_a, tx_b],
        {"a": [inv_a, inv_b], "b": [inv_b_unrelated]},
        cfg,
    )
    assert len(result.items) == 2
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[tx_a].category == "a"
    assert by_txn[tx_a].invoice is inv_a
    assert by_txn[tx_b].category == "a"
    assert by_txn[tx_b].invoice is inv_b
    assert all(a.kind is not AlertKind.AMBIGUOUS_INVOICE_MATCH for a in result.alerts)
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


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


# --- mixed split groups ---


def _cats_mixed_one_invoiced_one_catchall(tmp_path: Path) -> dict[str, Category]:
    """Single-invoiced + single-catch-all mixed group, monthly recurrence."""
    inv_dir = tmp_path / "inv_a"
    inv_dir.mkdir()
    return {
        "a": Category(
            name="a",
            recurrence="monthly",
            invoice_folder=inv_dir,
            invoice_parser="pepeenergy",
            patterns=("SHARED PATTERN",),
        ),
        "catchall": Category(
            name="catchall",
            recurrence="monthly",
            patterns=("SHARED PATTERN",),
        ),
    }


def test_mixed_split_group_happy_path(tmp_path: Path) -> None:
    # One invoice matches transaction A; transaction B in the same period has
    # no invoice and routes to the catch-all.
    cats = _cats_mixed_one_invoiced_one_catchall(tmp_path)
    cfg = _cfg_with_split_groups(categories=cats)
    txn_a = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED PATTERN ACME",
        amount=Decimal("42.00"),
    )
    txn_b = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED PATTERN ACME",
        amount=Decimal("13.50"),
    )
    inv_a = make_invoice(
        amount=Decimal("42.00"), invoice_date=date(2026, 5, 5), parser="pepeenergy"
    )

    result = match([txn_a, txn_b], {"a": [inv_a]}, cfg)

    assert len(result.items) == 2
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[txn_a].category == "a"
    assert by_txn[txn_a].invoice is inv_a
    assert by_txn[txn_b].category == "catchall"
    assert by_txn[txn_b].invoice is None
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


def test_mixed_split_group_invoice_missing_bucket_mismatch(tmp_path: Path) -> None:
    # Invoice for member 'a' did not arrive this period; both bucket txns are
    # leftovers but only 1 non-invoiced active member exists → bucket size
    # mismatch alert; leftovers stay unmatched.
    cats = _cats_mixed_one_invoiced_one_catchall(tmp_path)
    cfg = _cfg_with_split_groups(categories=cats)
    txn_a = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED PATTERN ACME",
        amount=Decimal("42.00"),
    )
    txn_b = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED PATTERN ACME",
        amount=Decimal("13.50"),
    )

    result = match([txn_a, txn_b], {"a": []}, cfg)

    assert any(
        a.kind is AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts
    )
    assert any(
        a.kind is AlertKind.EXPENSE_MISSING_INVOICE
        and a.payload.get("category") == "a"
        for a in result.alerts
    )
    assert len(result.items) == 0
    assert set(result.unmatched_transactions) == {txn_a, txn_b}


def test_mixed_split_group_orphan_invoice_when_no_amount_match(tmp_path: Path) -> None:
    # Single transaction in the period; an invoice exists for member 'a' but
    # its amount doesn't match. Bucket size 1 vs 2 active members fails →
    # AMBIGUOUS_SPLIT_BUCKET; the unmatched invoice raises ORPHAN_INVOICE.
    cats = _cats_mixed_one_invoiced_one_catchall(tmp_path)
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED PATTERN ACME",
        amount=Decimal("13.50"),
    )
    inv = make_invoice(
        amount=Decimal("42.00"), invoice_date=date(2026, 5, 5), parser="pepeenergy"
    )

    result = match([txn], {"a": [inv]}, cfg)

    assert any(a.kind is AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)
    assert any(a.kind is AlertKind.ORPHAN_INVOICE for a in result.alerts)
    assert any(
        a.kind is AlertKind.EXPENSE_MISSING_INVOICE
        and a.payload.get("category") == "a"
        for a in result.alerts
    )
    assert len(result.items) == 0
    assert result.unmatched_transactions == (txn,)


def test_mixed_split_group_catchall_not_yet_active(tmp_path: Path) -> None:
    # Catch-all has start_date later than the period: only invoiced member is
    # active → behaves like all-invoiced; no leftover phase. Single invoice
    # match, no AMBIGUOUS_SPLIT_BUCKET, no MISSING_INVOICE.
    inv_dir = tmp_path / "inv_a"
    inv_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="monthly",
            invoice_folder=inv_dir,
            invoice_parser="pepeenergy",
            patterns=("SHARED PATTERN",),
        ),
        "catchall": Category(
            name="catchall",
            recurrence="monthly",
            patterns=("SHARED PATTERN",),
            start_date=date(2027, 1, 1),
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED PATTERN ACME",
        amount=Decimal("42.00"),
    )
    inv = make_invoice(
        amount=Decimal("42.00"), invoice_date=date(2026, 5, 5), parser="pepeenergy"
    )

    result = match([txn], {"a": [inv]}, cfg)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.category == "a"
    assert item.invoice is inv
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


def test_mixed_split_group_invoiced_not_yet_active(tmp_path: Path) -> None:
    # Invoiced member has start_date later than the period: only catch-all is
    # active. Single leftover routes to it; no missing-invoice alert.
    inv_dir = tmp_path / "inv_a"
    inv_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="monthly",
            invoice_folder=inv_dir,
            invoice_parser="pepeenergy",
            patterns=("SHARED PATTERN",),
            start_date=date(2027, 1, 1),
        ),
        "catchall": Category(
            name="catchall",
            recurrence="monthly",
            patterns=("SHARED PATTERN",),
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED PATTERN ACME",
        amount=Decimal("13.50"),
    )

    result = match([txn], {"a": []}, cfg)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.category == "catchall"
    assert item.invoice is None
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)
    assert all(a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts)


def test_mixed_split_group_catchall_carries_manual_mapping_overrides(
    tmp_path: Path,
) -> None:
    # A leftover routed to the single catch-all member can still pick up
    # display_recurrence / display_period_contains_payment overrides from a
    # manual_mapping pinning that description to the catch-all.
    cats = _cats_mixed_one_invoiced_one_catchall(tmp_path)
    manual = {
        "SHARED PATTERN SPECIAL": ManualMapping(
            category="catchall",
            recurrence="yearly",
            period_contains_payment=True,
        )
    }
    cfg = _cfg_with_split_groups(categories=cats, manual_mappings=manual)
    txn_a = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED PATTERN ACME",
        amount=Decimal("42.00"),
    )
    txn_b = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED PATTERN SPECIAL",
        amount=Decimal("13.50"),
    )
    inv_a = make_invoice(
        amount=Decimal("42.00"), invoice_date=date(2026, 5, 5), parser="pepeenergy"
    )

    result = match([txn_a, txn_b], {"a": [inv_a]}, cfg)

    assert len(result.items) == 2
    by_txn = {item.transaction: item for item in result.items}
    catchall_item = by_txn[txn_b]
    assert catchall_item.category == "catchall"
    assert catchall_item.display_recurrence == "yearly"
    assert catchall_item.display_period_contains_payment is True


def test_mixed_split_group_extra_txn_bucket_mismatch(tmp_path: Path) -> None:
    # Three txns in one monthly period but only 2 active members → bucket size
    # mismatch alert. The invoice-matched txn is still kept as an item; the
    # other two leftovers go to unmatched.
    cats = _cats_mixed_one_invoiced_one_catchall(tmp_path)
    cfg = _cfg_with_split_groups(categories=cats)
    txn_a = make_transaction(
        date=date(2026, 5, 1),
        description="SHARED PATTERN ACME",
        amount=Decimal("42.00"),
    )
    txn_extra1 = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED PATTERN ACME",
        amount=Decimal("13.50"),
    )
    txn_extra2 = make_transaction(
        date=date(2026, 5, 20),
        description="SHARED PATTERN ACME",
        amount=Decimal("7.00"),
    )
    inv_a = make_invoice(
        amount=Decimal("42.00"), invoice_date=date(2026, 5, 1), parser="pepeenergy"
    )

    result = match([txn_a, txn_extra1, txn_extra2], {"a": [inv_a]}, cfg)

    assert any(a.kind is AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)
    by_txn = {item.transaction: item.category for item in result.items}
    assert by_txn.get(txn_a) == "a"
    assert txn_extra1 in result.unmatched_transactions
    assert txn_extra2 in result.unmatched_transactions


def test_mixed_split_group_recurrence_none_routes_by_amount(tmp_path: Path) -> None:
    # Two invoiced members + one catch-all, recurrence="none". Two transactions
    # match invoices by amount and go to their invoiced categories; a third
    # transaction has no matching invoice and goes to the catch-all. No
    # bucket-size check (rec=none doesn't bucket).
    a_dir = tmp_path / "inv_a"
    a_dir.mkdir()
    b_dir = tmp_path / "inv_b"
    b_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="none",
            invoice_folder=a_dir,
            invoice_parser="pepeenergy",
            patterns=("ONEOFF",),
        ),
        "b": Category(
            name="b",
            recurrence="none",
            invoice_folder=b_dir,
            invoice_parser="pepeenergy",
            patterns=("ONEOFF",),
        ),
        "catchall": Category(
            name="catchall",
            recurrence="none",
            patterns=("ONEOFF",),
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    txn_a = make_transaction(
        date=date(2026, 5, 18),
        description="ONEOFF VENDOR",
        amount=Decimal("42.00"),
    )
    txn_b = make_transaction(
        date=date(2026, 6, 18),
        description="ONEOFF VENDOR",
        amount=Decimal("99.00"),
    )
    txn_catchall = make_transaction(
        date=date(2026, 7, 18),
        description="ONEOFF VENDOR",
        amount=Decimal("17.00"),
    )
    inv_a = make_invoice(
        amount=Decimal("42.00"),
        invoice_date=date(2026, 5, 10),
        parser="pepeenergy",
    )
    inv_b = make_invoice(
        amount=Decimal("99.00"),
        invoice_date=date(2026, 6, 10),
        parser="pepeenergy",
    )

    result = match(
        [txn_a, txn_b, txn_catchall],
        {"a": [inv_a], "b": [inv_b]},
        cfg,
    )

    assert len(result.items) == 3
    by_txn = {item.transaction: item for item in result.items}
    assert by_txn[txn_a].category == "a"
    assert by_txn[txn_a].invoice is inv_a
    assert by_txn[txn_b].category == "b"
    assert by_txn[txn_b].invoice is inv_b
    assert by_txn[txn_catchall].category == "catchall"
    assert by_txn[txn_catchall].invoice is None
    assert all(a.kind is not AlertKind.AMBIGUOUS_SPLIT_BUCKET for a in result.alerts)


def test_mixed_split_group_recurrence_none_catchall_inactive_unmatched(
    tmp_path: Path,
) -> None:
    # Catch-all has start_date later than the transaction date and no invoice
    # matches its amount → unmatched.
    a_dir = tmp_path / "inv_a"
    a_dir.mkdir()
    cats = {
        "a": Category(
            name="a",
            recurrence="none",
            invoice_folder=a_dir,
            invoice_parser="pepeenergy",
            patterns=("ONEOFF",),
        ),
        "catchall": Category(
            name="catchall",
            recurrence="none",
            patterns=("ONEOFF",),
            start_date=date(2027, 1, 1),
        ),
    }
    cfg = _cfg_with_split_groups(categories=cats)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="ONEOFF VENDOR",
        amount=Decimal("13.50"),
    )

    result = match([txn], {"a": []}, cfg)

    assert len(result.items) == 0
    assert txn in result.unmatched_transactions


# --- manual_mappings with amount filter ---


def test_manual_mapping_amount_routes_to_different_categories(tmp_path: Path) -> None:
    # Two transactions share a description but have different amounts. The
    # list-form manual_mapping routes each to a different category.
    cats = {
        "alpha": Category(name="alpha", recurrence="monthly", patterns=()),
        "beta": Category(name="beta", recurrence="monthly", patterns=()),
    }
    manual = {
        "SHARED DESC": (
            ManualMapping(category="alpha", amount=Decimal("75.00")),
            ManualMapping(category="beta", amount=Decimal("120.00")),
        )
    }
    cfg = _config(categories=cats, manual_mappings=manual)

    txn_a = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED DESC",
        amount=Decimal("75.00"),
    )
    txn_b = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED DESC",
        amount=Decimal("120.00"),
    )

    result = match([txn_a, txn_b], {}, cfg)

    assert len(result.items) == 2
    by_txn = {it.transaction: it for it in result.items}
    assert by_txn[txn_a].category == "alpha"
    assert by_txn[txn_b].category == "beta"


def test_manual_mapping_fallback_catches_non_amount_match(tmp_path: Path) -> None:
    # A list-form mapping with a no-amount fallback entry catches transactions
    # whose amount doesn't match any of the earlier amount-filtered entries.
    cats = {
        "alpha": Category(name="alpha", recurrence="monthly", patterns=()),
        "fallback_cat": Category(name="fallback_cat", recurrence="monthly", patterns=()),
    }
    manual = {
        "SHARED DESC": (
            ManualMapping(category="alpha", amount=Decimal("75.00")),
            ManualMapping(category="fallback_cat"),
        )
    }
    cfg = _config(categories=cats, manual_mappings=manual)

    txn_match = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED DESC",
        amount=Decimal("75.00"),
    )
    txn_other = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED DESC",
        amount=Decimal("999.99"),
    )

    result = match([txn_match, txn_other], {}, cfg)

    assert len(result.items) == 2
    by_txn = {it.transaction: it for it in result.items}
    assert by_txn[txn_match].category == "alpha"
    assert by_txn[txn_other].category == "fallback_cat"


def test_manual_mapping_amount_display_overrides_flow_through(tmp_path: Path) -> None:
    # An entry with display_recurrence / display_period_contains_payment
    # overrides flows those overrides through to the resulting Item when its
    # amount filter matches.
    cats = {
        "alpha": Category(name="alpha", recurrence="quarterly", patterns=()),
    }
    manual = {
        "SHARED DESC": (
            ManualMapping(
                category="alpha",
                amount=Decimal("100.00"),
                recurrence="yearly",
                period_contains_payment=True,
            ),
        )
    }
    cfg = _config(categories=cats, manual_mappings=manual)

    txn = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED DESC",
        amount=Decimal("100.00"),
    )

    result = match([txn], {}, cfg)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.category == "alpha"
    assert item.display_recurrence == "yearly"
    assert item.display_period_contains_payment is True


def test_manual_mapping_unused_alerts(tmp_path: Path) -> None:
    # An amount-specific manual_mapping entry whose (description, amount)
    # pair has no matching transaction is flagged so users can spot typos
    # or stale config entries.
    cats = {
        "alpha": Category(name="alpha", recurrence="monthly", patterns=()),
    }
    manual = {
        "SHARED DESC": (
            ManualMapping(category="alpha", amount=Decimal("75.00")),
            ManualMapping(category="alpha", amount=Decimal("999.99")),  # unused
        )
    }
    cfg = _config(categories=cats, manual_mappings=manual)

    txn = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED DESC",
        amount=Decimal("75.00"),
    )

    result = match([txn], {}, cfg)

    unused = [a for a in result.alerts if a.kind is AlertKind.UNUSED_MANUAL_MAPPING]
    assert len(unused) == 1
    assert unused[0].payload["amount"] == "999.99"
    assert unused[0].payload["description"] == "SHARED DESC"
    assert unused[0].payload["category"] == "alpha"


def test_manual_mapping_no_amount_entry_not_flagged_as_unused(tmp_path: Path) -> None:
    # No-amount fallback entries are not flagged as unused (their purpose
    # is to absorb whatever's left; not firing is normal).
    cats = {
        "alpha": Category(name="alpha", recurrence="monthly", patterns=()),
    }
    manual = {
        "SHARED DESC": (ManualMapping(category="alpha"),)
    }
    cfg = _config(categories=cats, manual_mappings=manual)

    # No transactions at all.
    result = match([], {}, cfg)

    assert all(a.kind is not AlertKind.UNUSED_MANUAL_MAPPING for a in result.alerts)


def test_manual_mapping_flagged_when_only_invoice_matched_txn_shares_pair(
    tmp_path: Path,
) -> None:
    # The (desc, amount) pair has a matching transaction in the bank
    # statements, but that transaction was routed by invoice match (NOT by
    # the manual_mapping). The mapping is still considered unused — it
    # didn't actually drive any routing decision; the user's expected
    # invoiceless txn isn't there.
    cats = _cats_mixed_two_invoiced_two_noninv(tmp_path)
    manual = {
        "SHARED DESC": (
            ManualMapping(category="agua", amount=Decimal("15.00")),
        )
    }
    cfg = _cfg_with_split_groups(categories=cats, manual_mappings=manual)
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED DESC",
        amount=Decimal("15.00"),
    )
    inv = make_invoice(
        amount=Decimal("15.00"),
        invoice_date=date(2026, 5, 10),
        parser="pepeenergy",
    )

    result = match([txn], {"agua": [inv], "gas": []}, cfg)

    # The single txn was invoice-matched — the mapping wasn't load-bearing.
    assert len(result.items) == 1
    assert result.items[0].invoice is inv
    assert result.items[0].from_manual_mapping is False
    # Mapping is flagged as unused.
    unused = [a for a in result.alerts if a.kind is AlertKind.UNUSED_MANUAL_MAPPING]
    assert len(unused) == 1
    assert unused[0].payload["amount"] == "15.00"


def test_manual_mapping_not_flagged_when_mapping_routes_a_mixed_leftover(
    tmp_path: Path,
) -> None:
    # Mixed split group: two txns at the same (desc, amount), one invoice
    # in the group's invoiced member. The first txn invoice-matches; the
    # second falls to leftover routing and is routed via the manual_mapping
    # to the invoiced member (from_manual_mapping=True). The mapping IS
    # load-bearing — no unused alert.
    cats = _cats_mixed_two_invoiced_two_noninv(tmp_path)
    manual = {
        "SHARED DESC": (
            ManualMapping(category="agua", amount=Decimal("15.00")),
        )
    }
    cfg = _cfg_with_split_groups(categories=cats, manual_mappings=manual)
    txn1 = make_transaction(
        date=date(2026, 5, 5),
        description="SHARED DESC",
        amount=Decimal("15.00"),
    )
    txn2 = make_transaction(
        date=date(2026, 5, 20),
        description="SHARED DESC",
        amount=Decimal("15.00"),
    )
    inv = make_invoice(
        amount=Decimal("15.00"),
        invoice_date=date(2026, 5, 1),
        parser="pepeenergy",
    )

    result = match([txn1, txn2], {"agua": [inv], "gas": []}, cfg)

    assert len(result.items) == 2
    routed = [it for it in result.items if it.from_manual_mapping]
    assert len(routed) == 1
    # No unused alert: the mapping routed the second txn.
    assert all(
        a.kind is not AlertKind.UNUSED_MANUAL_MAPPING for a in result.alerts
    )


def test_manual_mapping_amount_no_match_no_routing(tmp_path: Path) -> None:
    # A list-form mapping with only amount-filtered entries (no fallback)
    # does NOT route a transaction whose amount doesn't match.
    cats = {
        "alpha": Category(name="alpha", recurrence="monthly", patterns=()),
    }
    manual = {
        "SHARED DESC": (
            ManualMapping(category="alpha", amount=Decimal("75.00")),
        )
    }
    cfg = _config(categories=cats, manual_mappings=manual)

    txn = make_transaction(
        date=date(2026, 5, 18),
        description="SHARED DESC",
        amount=Decimal("999.99"),
    )

    result = match([txn], {}, cfg)

    assert len(result.items) == 0
    assert txn in result.unmatched_transactions


# --- mixed split groups with 2+ non-invoiced members ---


def _cats_mixed_two_invoiced_two_noninv(tmp_path: Path) -> dict[str, Category]:
    """Two invoiced (rec=none) + two non-invoiced (catchall + amount-mapped)."""
    inv_a = tmp_path / "inv_a"
    inv_a.mkdir()
    inv_b = tmp_path / "inv_b"
    inv_b.mkdir()
    return {
        "agua": Category(
            name="agua",
            recurrence="none",
            invoice_folder=inv_a,
            invoice_parser="pepeenergy",
            patterns=("SHARED DESC",),
        ),
        "gas": Category(
            name="gas",
            recurrence="none",
            invoice_folder=inv_b,
            invoice_parser="pepeenergy",
            patterns=("SHARED DESC",),
        ),
        "camaras": Category(
            name="camaras",
            recurrence="none",
            patterns=("SHARED DESC",),
        ),
        "comunidad": Category(
            name="comunidad",
            recurrence="monthly",
            patterns=("SHARED DESC",),
        ),
    }


def test_mixed_split_group_two_noninv_amount_routes_to_mapped_category(
    tmp_path: Path,
) -> None:
    # User scenario: invoiced members disambiguate by amount via real invoices;
    # one specific amount routes to a non-invoiced member ("camaras") via
    # manual_mapping; the no-amount fallback entry catches everything else
    # and routes it to the other non-invoiced member ("comunidad").
    cats = _cats_mixed_two_invoiced_two_noninv(tmp_path)
    manual = {
        "SHARED DESC": (
            ManualMapping(category="camaras", amount=Decimal("50.00")),
            ManualMapping(category="comunidad"),
        )
    }
    cfg = _cfg_with_split_groups(categories=cats, manual_mappings=manual)

    txn_agua = make_transaction(
        date=date(2026, 5, 5),
        description="SHARED DESC",
        amount=Decimal("75.00"),
    )
    txn_gas = make_transaction(
        date=date(2026, 5, 10),
        description="SHARED DESC",
        amount=Decimal("99.00"),
    )
    txn_camaras = make_transaction(
        date=date(2026, 5, 15),
        description="SHARED DESC",
        amount=Decimal("50.00"),
    )
    txn_comunidad = make_transaction(
        date=date(2026, 5, 20),
        description="SHARED DESC",
        amount=Decimal("125.00"),
    )
    inv_agua = make_invoice(
        amount=Decimal("75.00"),
        invoice_date=date(2026, 5, 1),
        parser="pepeenergy",
    )
    inv_gas = make_invoice(
        amount=Decimal("99.00"),
        invoice_date=date(2026, 5, 8),
        parser="pepeenergy",
    )

    result = match(
        [txn_agua, txn_gas, txn_camaras, txn_comunidad],
        {"agua": [inv_agua], "gas": [inv_gas]},
        cfg,
    )

    assert len(result.items) == 4
    by_txn = {it.transaction: it for it in result.items}
    assert by_txn[txn_agua].category == "agua"
    assert by_txn[txn_agua].invoice is inv_agua
    assert by_txn[txn_gas].category == "gas"
    assert by_txn[txn_gas].invoice is inv_gas
    assert by_txn[txn_camaras].category == "camaras"
    assert by_txn[txn_camaras].invoice is None
    assert by_txn[txn_comunidad].category == "comunidad"
    assert by_txn[txn_comunidad].invoice is None
    assert all(
        a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts
    )


def test_mixed_split_group_two_noninv_unmapped_leftover_alerts(
    tmp_path: Path,
) -> None:
    # 2 non-invoiced members but the list-form mapping has only amount-filtered
    # entries (no no-amount fallback). A leftover whose amount doesn't match
    # any entry has nowhere to route → EXPENSE_MISSING_INVOICE + unmatched.
    cats = _cats_mixed_two_invoiced_two_noninv(tmp_path)
    manual = {
        "SHARED DESC": (
            ManualMapping(category="camaras", amount=Decimal("50.00")),
            ManualMapping(category="comunidad", amount=Decimal("125.00")),
        )
    }
    cfg = _cfg_with_split_groups(categories=cats, manual_mappings=manual)

    txn = make_transaction(
        date=date(2026, 5, 20),
        description="SHARED DESC",
        amount=Decimal("999.99"),
    )

    result = match([txn], {"agua": [], "gas": []}, cfg)

    assert len(result.items) == 0
    assert txn in result.unmatched_transactions
    assert any(
        a.kind is AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts
    )


def test_mixed_split_group_two_noninv_implicit_catchall_routes_unmapped(
    tmp_path: Path,
) -> None:
    # Implicit catch-all: exactly one non-invoiced member is NOT referenced
    # by any manual_mapping entry keyed by the group's patterns → that
    # member auto-acts as the catch-all without needing an explicit
    # no-amount fallback entry.
    cats = _cats_mixed_two_invoiced_two_noninv(tmp_path)
    # 'camaras' is the only noninv referenced in the mapping. 'comunidad'
    # is the implicit catch-all.
    manual = {
        "SHARED DESC": (
            ManualMapping(category="camaras", amount=Decimal("50.00")),
        )
    }
    cfg = _cfg_with_split_groups(categories=cats, manual_mappings=manual)

    txn_camaras = make_transaction(
        date=date(2026, 5, 15),
        description="SHARED DESC",
        amount=Decimal("50.00"),
    )
    txn_anything_else = make_transaction(
        date=date(2026, 5, 20),
        description="SHARED DESC",
        amount=Decimal("125.00"),
    )

    result = match([txn_camaras, txn_anything_else], {"agua": [], "gas": []}, cfg)

    assert len(result.items) == 2
    by_txn = {it.transaction: it.category for it in result.items}
    assert by_txn[txn_camaras] == "camaras"
    assert by_txn[txn_anything_else] == "comunidad"
    assert all(
        a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts
    )


def test_mixed_split_group_leftover_mapping_to_invoiced_member_routes_without_invoice(
    tmp_path: Path,
) -> None:
    # Manual_mapping is the authoritative routing override. When a leftover's
    # mapping points to an INVOICED member of the group (no matching invoice
    # in the folder, e.g. a one-off payment that happens to share a pattern
    # and amount with the invoiced flow), the matcher routes the Item to
    # that member without an invoice and marks it from_manual_mapping=True
    # so the runner doesn't filter it out.
    cats = _cats_mixed_two_invoiced_two_noninv(tmp_path)
    manual = {
        "SHARED DESC": (
            ManualMapping(category="agua", amount=Decimal("99.99")),
            ManualMapping(category="comunidad"),
        )
    }
    cfg = _cfg_with_split_groups(categories=cats, manual_mappings=manual)

    txn = make_transaction(
        date=date(2026, 5, 20),
        description="SHARED DESC",
        amount=Decimal("99.99"),
    )

    result = match([txn], {"agua": [], "gas": []}, cfg)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.category == "agua"
    assert item.invoice is None
    assert item.from_manual_mapping is True
    # No missing-invoice alert: the user has explicitly authored this
    # routing and is saying "no invoice expected here".
    assert all(
        a.kind is not AlertKind.EXPENSE_MISSING_INVOICE for a in result.alerts
    )
