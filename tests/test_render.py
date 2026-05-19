from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.models import Alert, AlertKind
from home_expenses.report.model import build_report_model
from home_expenses.report.render import render_report
from tests.factories import make_invoice, make_item, make_transaction


def test_renders_self_contained_html(tmp_path: Path) -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="RECIBO PEPE ENERGY",
        amount=Decimal("11.11"),
    )
    inv = make_invoice(
        source_path="/Users/test/invoices/pepeenergy/f.pdf",
        amount=Decimal("11.11"),
        invoice_date=date(2026, 5, 10),
    )
    items = [make_item(transaction=txn, category="electricity", invoice=inv)]
    alerts = [Alert(kind=AlertKind.UNCLASSIFIED_EXPENSE, message="x")]
    model = build_report_model(
        items=items, alerts=alerts, currency="EUR", generated_at=date(2026, 5, 19)
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    # Self-contained: starts with <!DOCTYPE
    assert html.startswith("<!DOCTYPE")
    # Year filter present
    assert 'data-year="2026"' in html
    # Plotly inline (bundled)
    assert "plotly" in html.lower()
    # Item row present
    assert "RECIBO PEPE ENERGY" in html
    # PDF link present
    assert "file:///Users/test/invoices/pepeenergy/f.pdf" in html
    # Alert section present
    assert "unclassified_expense" in html.lower() or "Unclassified" in html


def test_empty_model_still_renders(tmp_path: Path) -> None:
    model = build_report_model(items=[], alerts=[], currency="EUR", generated_at=date(2026, 5, 19))
    out = tmp_path / "report.html"
    render_report(model, out)
    assert out.exists()
