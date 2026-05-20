"""Render the ReportModel as a single self-contained HTML file."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import plotly  # type: ignore[import-untyped]
from jinja2 import Environment, FileSystemLoader, select_autoescape

from home_expenses.models import ReportModel

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_report(model: ReportModel, output_path: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(
        model=model,
        data_json=_serialize_model(model),
        plotly_js=plotly.offline.get_plotlyjs(),
    )
    output_path.write_text(html, encoding="utf-8")


def _serialize_model(model: ReportModel) -> str:
    """Serialize the model into a JSON blob for in-page JS to consume.

    Items are sorted by transaction date in descending order so that the
    JSON's index order matches the server-rendered table and downstream
    Plotly customdata references stay consistent.
    """
    sorted_items = sorted(model.items, key=lambda it: it.transaction.date, reverse=True)
    payload = {
        "generated_at": model.generated_at.isoformat(),
        "currency": model.currency,
        "years": list(model.years),
        "categories": dict(model.categories),
        "items": [
            {
                "date": it.transaction.date.isoformat(),
                "description": it.transaction.description,
                "amount": str(it.transaction.amount),
                "category": it.category,
                "invoice_path": (it.invoice.source_path if it.invoice is not None else None),
                "invoice_id": (it.invoice.invoice_id if it.invoice is not None else None),
                "invoice_date": (
                    it.invoice.invoice_date.isoformat() if it.invoice is not None else None
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
