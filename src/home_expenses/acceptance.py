"""Apply `accepted_alerts` rules to a run's alerts.

An accepted alert is one the user has already looked at and signed off —
a bank that stopped charging a fee, a community charge known to be
uncollected. It still appears in the report (in green rather than red) so
the fact stays visible, but it no longer counts as an open alert.

Rules that match nothing are reported as `UNUSED_ALERT_ACCEPTANCE`, the
same way an amount-specific manual_mapping that routed nothing is
reported. Without that, a rule left behind after the underlying problem
is fixed would silently keep greening whatever it happens to match next.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from home_expenses.config import AcceptedAlertRule
from home_expenses.models import Alert, AlertKind


def _matches(alert: Alert, rule: AcceptedAlertRule) -> bool:
    if alert.kind is not rule.kind:
        return False
    for key, expected in rule.match.items():
        if key not in alert.payload:
            return False
        if str(alert.payload[key]) != expected:
            return False
    return True


def apply_acceptances(
    alerts: Iterable[Alert],
    rules: Sequence[AcceptedAlertRule],
) -> tuple[Alert, ...]:
    """Mark alerts matched by `rules` as accepted; flag rules that matched none.

    Alert order is preserved. Any `UNUSED_ALERT_ACCEPTANCE` alerts are
    appended after the original ones.
    """
    out: list[Alert] = []
    used: set[int] = set()
    for alert in alerts:
        for idx, rule in enumerate(rules):
            if _matches(alert, rule):
                used.add(idx)
                out.append(replace(alert, accepted=True, note=rule.note))
                break
        else:
            out.append(alert)

    for idx, rule in enumerate(rules):
        if idx in used:
            continue
        match_str = ", ".join(f"{k}={v!r}" for k, v in rule.match.items()) or "(any)"
        out.append(
            Alert(
                kind=AlertKind.UNUSED_ALERT_ACCEPTANCE,
                message=(
                    f"accepted_alerts rule for '{rule.kind.value}' [{match_str}] "
                    f"matched no alert in this run"
                ),
                payload={"kind": rule.kind.value, "match": dict(rule.match)},
            )
        )
    return tuple(out)


__all__ = ["apply_acceptances"]
