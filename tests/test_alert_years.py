from __future__ import annotations

from home_expenses.models import Alert, AlertKind
from home_expenses.report.render import alert_years


def test_iso_date_payload() -> None:
    a = Alert(
        kind=AlertKind.EXPENSE_MISSING_INVOICE,
        message="x",
        payload={"date": "2026-09-30", "category": "IBI"},
    )
    assert alert_years(a) == (2026,)


def test_monthly_period_payload() -> None:
    a = Alert(kind=AlertKind.RECURRING_MISSED, message="x", payload={"period": "2024-11"})
    assert alert_years(a) == (2024,)


def test_quarterly_period_payload() -> None:
    a = Alert(kind=AlertKind.RECURRING_MISSED, message="x", payload={"period": "2025-Q2"})
    assert alert_years(a) == (2025,)


def test_yearly_period_payload() -> None:
    a = Alert(kind=AlertKind.RECURRING_MISSED, message="x", payload={"period": "2026"})
    assert alert_years(a) == (2026,)


def test_invoice_period_spanning_two_years_yields_both() -> None:
    a = Alert(
        kind=AlertKind.ORPHAN_INVOICE,
        message="x",
        payload={
            "invoice_date": "2024-04-30",
            "period_start": "2023-11-15",
            "period_end": "2024-02-09",
        },
    )
    assert alert_years(a) == (2023, 2024)


def test_single_year_invoice_period() -> None:
    a = Alert(
        kind=AlertKind.ORPHAN_INVOICE,
        message="x",
        payload={
            "invoice_date": "2025-12-01",
            "period_start": "2025-05-08",
            "period_end": "2025-11-25",
        },
    )
    assert alert_years(a) == (2025,)


def test_nested_acceptance_rule_period_is_used() -> None:
    a = Alert(
        kind=AlertKind.UNUSED_ALERT_ACCEPTANCE,
        message="x",
        payload={
            "kind": "recurring_missed",
            "match": {"category": "Comunidad", "period": "2024-11"},
        },
    )
    assert alert_years(a) == (2024,)


def test_nested_acceptance_rule_period_span_is_used() -> None:
    a = Alert(
        kind=AlertKind.UNUSED_ALERT_ACCEPTANCE,
        message="x",
        payload={
            "kind": "orphan_invoice",
            "match": {"period_start": "2025-05-08", "period_end": "2025-11-25"},
        },
    )
    assert alert_years(a) == (2025,)


def test_year_is_read_from_the_note_when_payload_has_none() -> None:
    a = Alert(
        kind=AlertKind.UNUSED_MANUAL_MAPPING,
        message="x",
        payload={"description": "CCPP", "amount": "15.00", "category": "Agua Caliente"},
        note="Mapping reservado para el ACS de septiembre 2024, aun sin cobrar",
    )
    assert alert_years(a) == (2024,)


def test_note_is_only_a_fallback_not_merged_with_payload() -> None:
    a = Alert(
        kind=AlertKind.RECURRING_MISSED,
        message="x",
        payload={"period": "2024-11"},
        note="pendiente desde 2019 y hasta 2027",
    )
    assert alert_years(a) == (2024,)


def test_note_with_several_years() -> None:
    a = Alert(
        kind=AlertKind.UNUSED_MANUAL_MAPPING,
        message="x",
        payload={"amount": "15.00"},
        note="emitida en 2024, reclamada en 2025",
    )
    assert alert_years(a) == (2024, 2025)


def test_amount_digits_in_payload_are_not_mistaken_for_a_year() -> None:
    a = Alert(
        kind=AlertKind.UNUSED_MANUAL_MAPPING,
        message="x",
        payload={"amount": "2024.50", "category": "X"},
    )
    assert alert_years(a) == ()


def test_no_date_anywhere_yields_no_years() -> None:
    a = Alert(
        kind=AlertKind.UNUSED_MANUAL_MAPPING,
        message="x",
        payload={"description": "CCPP", "amount": "15.00", "category": "Agua Caliente"},
    )
    assert alert_years(a) == ()
