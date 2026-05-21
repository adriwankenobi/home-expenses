from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.models import Alert, AlertKind, ReportCategory
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
    cats = {
        "electricity": ReportCategory(
            name="electricity",
            recurrence="monthly",
            period_contains_payment=False,
        )
    }
    model = build_report_model(
        items=items,
        alerts=alerts,
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name=cats,
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE")
    assert 'data-year="2026"' in html
    assert "plotly" in html.lower()
    assert "RECIBO PEPE ENERGY" in html
    assert "file:///Users/test/invoices/pepeenergy/f.pdf" in html
    assert "unclassified_expense" in html.lower() or "Unclassified" in html


def test_empty_model_still_renders(tmp_path: Path) -> None:
    model = build_report_model(
        items=[],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={},
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    assert out.exists()


def test_data_block_uses_categories_by_name(tmp_path: Path) -> None:
    items = [
        make_item(
            transaction=make_transaction(date=date(2026, 5, 18)),
            category="water",
        ),
    ]
    cats = {
        "water": ReportCategory(
            name="water",
            recurrence="quarterly",
            period_contains_payment=False,
        ),
    }
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name=cats,
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert "categories_by_name" in html
    assert '"category":"water"' in html or '"category": "water"' in html


def test_period_contains_payment_renders(tmp_path: Path) -> None:
    items = [
        make_item(
            transaction=make_transaction(date=date(2024, 9, 15), amount=Decimal("50.00")),
            category="fee",
        ),
    ]
    cats = {
        "fee": ReportCategory(
            name="fee",
            recurrence="yearly",
            period_contains_payment=True,
        ),
    }
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name=cats,
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert "period_contains_payment" in html
    assert '"period_contains_payment":true' in html or '"period_contains_payment": true' in html


def test_manual_mapping_overrides_appear_in_data_block(tmp_path: Path) -> None:
    items = [
        make_item(
            transaction=make_transaction(
                date=date(2024, 9, 15),
                description="UNIQUE ANNUAL CHARGE",
                amount=Decimal("100.00"),
            ),
            category="sharedQuarterly1",
            display_recurrence="yearly",
            display_period_contains_payment=True,
        ),
    ]
    cats = {
        "sharedQuarterly1": ReportCategory(
            name="sharedQuarterly1",
            recurrence="quarterly",
            period_contains_payment=False,
        ),
    }
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name=cats,
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert '"display_recurrence":"yearly"' in html or '"display_recurrence": "yearly"' in html
    assert (
        '"display_period_contains_payment":true' in html
        or '"display_period_contains_payment": true' in html
    )


def test_items_table_shows_long_dash_when_no_invoice(tmp_path: Path) -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="RECIBO TRIMESTRAL XYZ",
        amount=Decimal("33.33"),
    )
    items = [make_item(transaction=txn, category="water", invoice=None)]
    cats = {
        "water": ReportCategory(
            name="water",
            recurrence="quarterly",
            period_contains_payment=False,
        ),
    }
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name=cats,
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert "&mdash;" in html or "—" in html
    assert '"invoice_id":null' in html or '"invoice_id": null' in html
