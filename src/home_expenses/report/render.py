"""Render the ReportModel as a single self-contained HTML file."""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import plotly  # type: ignore[import-untyped]
from jinja2 import Environment, FileSystemLoader, select_autoescape

from home_expenses.models import Alert, Item, ReportModel, Transaction

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# English month names, indexed 1..12. Hardcoded rather than using
# ``date.strftime('%B')`` because the latter is locale-dependent (the user's
# system locale is Spanish) and the report UI is English throughout.
_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def format_period(start: date | None, end: date | None) -> str:
    """Format an invoice period span for the merged "Period" column.

    Strict calendar alignment:
    - Full calendar year (01/01 -> 31/12, same year) -> ``"2025"``.
    - Full calendar month (1st -> last day, same month) -> ``"January 2025"``.
    - Single day (start == end) -> ``"13/03/2025"``.
    - Anything else -> ``"13/03/2025 to 23/05/2025"`` (DD/MM/YYYY).
    - Missing either bound -> ``"—"``.

    The JS ``formatPeriod`` in the report template mirrors this logic; keep
    the two in sync.
    """
    if start is None or end is None:
        return "—"
    if (
        start.month == 1
        and start.day == 1
        and end.month == 12
        and end.day == 31
        and start.year == end.year
    ):
        return str(start.year)
    if (
        start.year == end.year
        and start.month == end.month
        and start.day == 1
        and end.day == calendar.monthrange(end.year, end.month)[1]
    ):
        return f"{_MONTH_NAMES[start.month]} {start.year}"
    if start == end:
        return start.strftime("%d/%m/%Y")
    return f"{start.strftime('%d/%m/%Y')} to {end.strftime('%d/%m/%Y')}"


_YEAR_IN_TEXT_RE = re.compile(r"\b(19|20)\d{2}\b")
# Payload keys that carry a date. `period` is a label ("2024-11", "2025-Q2",
# "2026"); the others are ISO dates.
_DATE_KEYS = ("date", "invoice_date")
_SPAN_KEYS = ("period_start", "period_end")


def _years_from_fields(payload: Mapping[str, Any]) -> set[int]:
    years: set[int] = set()
    for key in _DATE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            years.add(int(value[:4]))
    label = payload.get("period")
    if isinstance(label, str) and len(label) >= 4 and label[:4].isdigit():
        years.add(int(label[:4]))
    bounds = [
        int(v[:4])
        for k in _SPAN_KEYS
        if isinstance(v := payload.get(k), str) and len(v) >= 4 and v[:4].isdigit()
    ]
    if bounds:
        # An invoice period can straddle a year boundary, so the alert belongs
        # to every year it touches, not just the one it started in.
        years.update(range(min(bounds), max(bounds) + 1))
    return years


def alert_years(alert: Alert) -> tuple[int, ...]:
    """Years an alert belongs to, for the report's year-tab filter.

    Resolved in order of how trustworthy the source is:

    1. Date-bearing payload fields.
    2. The acceptance rule embedded in an `unused_alert_acceptance` payload —
       a stale rule inherits the year of whatever it was written to accept.
    3. A four-digit year written in the user's `note`. Free text, so this is
       a last resort and only consulted when nothing structured was found.

    An empty result means the alert is not tied to any year (a config-level
    statement), and the report shows it under every year tab.
    """
    years = _years_from_fields(alert.payload)
    if not years:
        nested = alert.payload.get("match")
        if isinstance(nested, Mapping):
            years = _years_from_fields(nested)
    if not years and alert.note:
        years = {int(m.group(0)) for m in _YEAR_IN_TEXT_RE.finditer(alert.note)}
    return tuple(sorted(years))


def render_report(model: ReportModel, output_path: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["format_period"] = format_period
    env.filters["alert_years"] = alert_years
    template = env.get_template("report.html.j2")
    sorted_items = sorted(model.items, key=_item_sort_key, reverse=True)
    html = template.render(
        model=model,
        sorted_items=sorted_items,
        data_json=_serialize_model(model),
        plotly_js=plotly.offline.get_plotlyjs(),
    )
    output_path.write_text(html, encoding="utf-8")


def _item_sort_key(item: Item) -> tuple[date, date, date]:
    """Sort key: transaction.date, then invoice_date, then period.start.

    With ``reverse=True`` applied at the call site, items with no invoice
    (or no invoice_date) get ``date.min`` as their tie-break value, which
    flips to "sorts last within the txn-date group" under reverse order.
    """
    inv = item.invoice
    inv_date = inv.invoice_date if inv is not None and inv.invoice_date is not None else date.min
    period_start = inv.period.start if inv is not None else date.min
    return (item.transaction.date, inv_date, period_start)


def _signed_amount(txn: Transaction) -> Decimal:
    """Amount as it should read in the report: negative for credits.

    `Transaction.amount` is a sign-free magnitude so the matcher can compare
    amounts directly. The report wants the real direction, and the in-page JS
    simply sums `amount`, so emitting a negative here makes every total,
    chart and KPI subtract refunds without any JS change.
    """
    return -txn.amount if txn.is_credit else txn.amount


def _serialize_model(model: ReportModel) -> str:
    """Serialize the model into a JSON blob for in-page JS to consume.

    Items are sorted by transaction date in descending order, with ties
    broken by invoice_date then period.start (both desc, missing values
    sort last). This keeps the JSON's index order aligned with the
    server-rendered table and Plotly customdata references.
    """
    sorted_items = sorted(model.items, key=_item_sort_key, reverse=True)
    payload = {
        "generated_at": model.generated_at.isoformat(),
        "currency": model.currency,
        "years": list(model.years),
        "categories_by_name": {
            name: {
                "recurrence": rc.recurrence,
                "period_contains_payment": rc.period_contains_payment,
                "period_edge_days": rc.period_edge_days,
            }
            for name, rc in model.categories_by_name.items()
        },
        "items": [
            {
                "date": it.transaction.date.isoformat(),
                "description": it.transaction.description,
                "amount": str(_signed_amount(it.transaction)),
                "category": it.category,
                "display_recurrence": it.display_recurrence,
                "display_period_contains_payment": it.display_period_contains_payment,
                "invoice_path": (it.invoice.source_path if it.invoice is not None else None),
                "invoice_date": (
                    it.invoice.invoice_date.isoformat()
                    if it.invoice is not None and it.invoice.invoice_date is not None
                    else None
                ),
                "period_start": (
                    it.invoice.period.start.isoformat() if it.invoice is not None else None
                ),
                "period_end": (
                    it.invoice.period.end.isoformat() if it.invoice is not None else None
                ),
            }
            for it in sorted_items
        ],
        "alerts": [
            {"kind": a.kind.value, "message": a.message, "payload": a.payload} for a in model.alerts
        ],
    }
    return json.dumps(payload, default=_json_default)


def _json_default(obj: object) -> object:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"cannot serialize {type(obj)}")
