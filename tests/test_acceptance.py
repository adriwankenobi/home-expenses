from __future__ import annotations

from home_expenses.acceptance import apply_acceptances
from home_expenses.config import AcceptedAlertRule
from home_expenses.models import Alert, AlertKind


def _missed(category: str, period: str) -> Alert:
    return Alert(
        kind=AlertKind.RECURRING_MISSED,
        message=f"recurring category '{category}' has no items in {period}",
        payload={"category": category, "period": period},
    )


def test_exact_rule_marks_alert_accepted_and_carries_note() -> None:
    alert = _missed("Bank Fee", "2026-Q1")
    rule = AcceptedAlertRule(
        kind=AlertKind.RECURRING_MISSED,
        match={"category": "Bank Fee", "period": "2026-Q1"},
        note="waived by the bank",
    )
    out = apply_acceptances((alert,), (rule,))
    assert len(out) == 1
    assert out[0].accepted is True
    assert out[0].note == "waived by the bank"


def test_alert_without_matching_rule_is_untouched() -> None:
    alert = _missed("Bank Fee", "2026-Q1")
    out = apply_acceptances((alert,), ())
    assert out == (alert,)
    assert out[0].accepted is False
    assert out[0].note is None


def test_omitted_field_matches_any_value() -> None:
    alerts = (_missed("Bank Fee", "2026-Q1"), _missed("Bank Fee", "2026-Q2"))
    rule = AcceptedAlertRule(kind=AlertKind.RECURRING_MISSED, match={"category": "Bank Fee"})
    out = apply_acceptances(alerts, (rule,))
    assert [a.accepted for a in out] == [True, True]


def test_rule_does_not_cross_categories() -> None:
    alerts = (_missed("Bank Fee", "2026-Q1"), _missed("Energy", "2026-Q1"))
    rule = AcceptedAlertRule(kind=AlertKind.RECURRING_MISSED, match={"category": "Bank Fee"})
    out = apply_acceptances(alerts, (rule,))
    assert [a.accepted for a in out] == [True, False]


def test_rule_does_not_cross_kinds() -> None:
    alert = _missed("Bank Fee", "2026-Q1")
    rule = AcceptedAlertRule(kind=AlertKind.ORPHAN_INVOICE, match={"category": "Bank Fee"})
    out = apply_acceptances((alert,), (rule,))
    assert out[0].accepted is False


def test_rule_key_absent_from_payload_does_not_match() -> None:
    alert = Alert(kind=AlertKind.RECURRING_MISSED, message="x", payload={"category": "Bank Fee"})
    rule = AcceptedAlertRule(
        kind=AlertKind.RECURRING_MISSED, match={"category": "Bank Fee", "period": "2026-Q1"}
    )
    out = apply_acceptances((alert,), (rule,))
    assert out[0].accepted is False


def test_payload_values_are_compared_as_strings() -> None:
    alert = Alert(kind=AlertKind.ORPHAN_INVOICE, message="x", payload={"amount": "30.00"})
    rule = AcceptedAlertRule(kind=AlertKind.ORPHAN_INVOICE, match={"amount": "30.00"})
    assert apply_acceptances((alert,), (rule,))[0].accepted is True


def test_unmatched_rule_raises_unused_alert_acceptance() -> None:
    alert = _missed("Bank Fee", "2026-Q1")
    stale = AcceptedAlertRule(kind=AlertKind.RECURRING_MISSED, match={"category": "Gone"})
    out = apply_acceptances((alert,), (stale,))
    stale_alerts = [a for a in out if a.kind is AlertKind.UNUSED_ALERT_ACCEPTANCE]
    assert len(stale_alerts) == 1
    assert stale_alerts[0].payload["kind"] == AlertKind.RECURRING_MISSED.value
    assert stale_alerts[0].payload["match"] == {"category": "Gone"}


def test_matched_rule_raises_no_unused_alert() -> None:
    alert = _missed("Bank Fee", "2026-Q1")
    rule = AcceptedAlertRule(kind=AlertKind.RECURRING_MISSED, match={"category": "Bank Fee"})
    out = apply_acceptances((alert,), (rule,))
    assert not [a for a in out if a.kind is AlertKind.UNUSED_ALERT_ACCEPTANCE]


def test_first_matching_rule_wins() -> None:
    alert = _missed("Bank Fee", "2026-Q1")
    first = AcceptedAlertRule(
        kind=AlertKind.RECURRING_MISSED, match={"category": "Bank Fee"}, note="first"
    )
    second = AcceptedAlertRule(
        kind=AlertKind.RECURRING_MISSED, match={"period": "2026-Q1"}, note="second"
    )
    out = apply_acceptances((alert,), (first, second))
    accepted = next(a for a in out if a.accepted)
    assert accepted.note == "first"
    # The second rule never routed anything, so it is reported stale.
    assert len([a for a in out if a.kind is AlertKind.UNUSED_ALERT_ACCEPTANCE]) == 1


def test_alert_order_is_preserved_and_stale_alerts_appended_last() -> None:
    alerts = (_missed("A", "2026-Q1"), _missed("B", "2026-Q1"))
    stale = AcceptedAlertRule(kind=AlertKind.RECURRING_MISSED, match={"category": "Gone"})
    out = apply_acceptances(alerts, (stale,))
    assert [a.payload.get("category") for a in out[:2]] == ["A", "B"]
    assert out[-1].kind is AlertKind.UNUSED_ALERT_ACCEPTANCE
