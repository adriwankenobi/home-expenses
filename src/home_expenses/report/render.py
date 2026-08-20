"""Render the ReportModel as a single self-contained HTML file."""

from __future__ import annotations

import calendar
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import plotly  # type: ignore[import-untyped]
from jinja2 import Environment, FileSystemLoader, select_autoescape

from home_expenses.models import Item, ReportModel

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


def render_report(model: ReportModel, output_path: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    env.filters["format_period"] = format_period
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
                "amount": str(it.transaction.amount),
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
