from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.models import Alert, AlertKind, ReportCategory
from home_expenses.report.model import build_report_model
from home_expenses.report.render import _serialize_model, render_report
from tests.factories import make_invoice, make_item, make_transaction


def test_renders_self_contained_html(tmp_path: Path) -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="PEPE ENERGY INVOICE",
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
    assert "PEPE ENERGY INVOICE" in html
    assert "file:///Users/test/invoices/pepeenergy/f.pdf" in html
    assert "unclassified_expense" in html.lower() or "Unclassified" in html


def test_dates_display_as_ddmmyyyy_but_json_stays_iso(tmp_path: Path) -> None:
    # Every user-facing date renders DD/MM/YYYY, but the JSON data block the
    # in-page JS consumes (for sorting, time axes, month math, year filtering)
    # MUST stay ISO YYYY-MM-DD. This test pins both halves of that contract.
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="PEPE ENERGY INVOICE",
        amount=Decimal("11.11"),
    )
    inv = make_invoice(
        source_path="/Users/test/invoices/pepeenergy/f.pdf",
        amount=Decimal("11.11"),
        invoice_date=date(2026, 5, 10),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    items = [make_item(transaction=txn, category="electricity", invoice=inv)]
    cats = {
        "electricity": ReportCategory(
            name="electricity", recurrence="monthly", period_contains_payment=False
        )
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

    # Server-rendered items table: DD/MM/YYYY.
    assert "18/05/2026" in html  # payment date
    assert "10/05/2026" in html  # invoice date
    # The period (01/04 -> 30/04) is a full calendar month, so the merged
    # Period column collapses it to the month name rather than two dates.
    assert "April 2026" in html
    # Header timestamp date portion: DD/MM/YYYY (not 2026-05-19).
    assert "19/05/2026" in html

    # JSON data block keeps ISO so JS date math / time axes keep working.
    payload = json.loads(_serialize_model(model))
    it = payload["items"][0]
    assert it["date"] == "2026-05-18"
    assert it["invoice_date"] == "2026-05-10"
    assert it["period_start"] == "2026-04-01"
    assert it["period_end"] == "2026-04-30"
    assert payload["generated_at"] == "2026-05-19"


def test_js_has_ddmmyyyy_formatter_and_wires_it(tmp_path: Path) -> None:
    # A single JS helper converts ISO → DD/MM/YYYY at display time, and the
    # dynamic table + timeline tooltip + Plotly date axis all route through it.
    model = build_report_model(
        items=[
            make_item(
                transaction=make_transaction(date=date(2026, 5, 18)),
                category="electricity",
            )
        ],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={
            "electricity": ReportCategory(
                name="electricity", recurrence="monthly", period_contains_payment=False
            )
        },
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert "function formatDate" in html
    # JS-rebuilt items table uses the formatter for the payment-date cell.
    assert "formatDate(it.date)" in html
    # The merged Period column + timeline tooltip route through formatPeriod,
    # which itself falls back to formatDate for explicit ranges.
    assert "function formatPeriod" in html
    assert "formatPeriod(it.period_start, it.period_end)" in html
    # Plotly date axis ticks render DD/MM/YYYY.
    assert "tickformat: '%d/%m/%Y'" in html


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


def test_items_sorted_by_txn_date_then_invoice_date_then_period_start() -> None:
    # All five items share the same transaction date except E (one day earlier),
    # forcing the tie-break chain to fully exercise: by transaction.date desc,
    # then by invoice.invoice_date desc (None last), then by period.start desc.
    txn_same = make_transaction(date=date(2026, 5, 18), amount=Decimal("10.00"))
    txn_earlier = make_transaction(date=date(2026, 5, 17), amount=Decimal("10.00"))

    inv_later = make_invoice(
        invoice_date=date(2026, 5, 15),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        content_hash="hLater",
        source_path="/tmp/later.pdf",
    )
    inv_mid_newer_period = make_invoice(
        invoice_date=date(2026, 5, 10),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        content_hash="hMidNew",
        source_path="/tmp/mid_new.pdf",
    )
    inv_mid_older_period = make_invoice(
        invoice_date=date(2026, 5, 10),
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        content_hash="hMidOld",
        source_path="/tmp/mid_old.pdf",
    )

    item_d_later_inv = make_item(transaction=txn_same, category="c", invoice=inv_later)
    item_b_newer_period = make_item(
        transaction=txn_same, category="c", invoice=inv_mid_newer_period
    )
    item_c_older_period = make_item(
        transaction=txn_same, category="c", invoice=inv_mid_older_period
    )
    item_a_no_invoice = make_item(transaction=txn_same, category="c", invoice=None)
    item_e_earlier_txn = make_item(transaction=txn_earlier, category="c", invoice=inv_later)

    # Pass in an arbitrary order to confirm the sort actually runs.
    items = [
        item_e_earlier_txn,
        item_a_no_invoice,
        item_c_older_period,
        item_b_newer_period,
        item_d_later_inv,
    ]
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={
            "c": ReportCategory(name="c", recurrence="monthly", period_contains_payment=False),
        },
    )
    payload = json.loads(_serialize_model(model))
    paths_in_order = [it["invoice_path"] for it in payload["items"]]
    assert paths_in_order == [
        "/tmp/later.pdf",      # txn 5/18, invoice 5/15
        "/tmp/mid_new.pdf",    # txn 5/18, invoice 5/10, period 4/1
        "/tmp/mid_old.pdf",    # txn 5/18, invoice 5/10, period 3/1
        None,                  # txn 5/18, no invoice (last in tie group)
        "/tmp/later.pdf",      # txn 5/17 (earlier txn, sorts last overall)
    ]


def test_items_table_shows_long_dash_when_no_invoice(tmp_path: Path) -> None:
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="QUARTERLY INVOICE XYZ",
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
    assert '"invoice_path":null' in html or '"invoice_path": null' in html


def test_items_section_has_legend_container(tmp_path: Path) -> None:
    # The items table is preceded by an empty <div id="items-legend"> that
    # the report script populates with one clickable pill per category.
    txn = make_transaction(
        date=date(2026, 5, 18),
        description="ANY EXPENSE",
        amount=Decimal("10.00"),
    )
    items = [make_item(transaction=txn, category="electricity", invoice=None)]
    cats = {
        "electricity": ReportCategory(
            name="electricity",
            recurrence="monthly",
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
    assert '<div id="items-legend"' in html


def test_report_defines_unified_filter_controls(tmp_path: Path) -> None:
    # The report script exposes the shared filter-control functions that every
    # legend gesture funnels through.
    model = build_report_model(
        items=[
            make_item(
                transaction=make_transaction(date=date(2026, 5, 18)),
                category="electricity",
            )
        ],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={
            "electricity": ReportCategory(
                name="electricity", recurrence="monthly", period_contains_payment=False
            )
        },
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert "function toggleCategory" in html
    assert "function isolateCategory" in html
    assert "function scheduleToggle" in html
    assert "function cancelToggle" in html


def test_bar_charts_intercept_legend_for_unified_filter(tmp_path: Path) -> None:
    model = build_report_model(
        items=[
            make_item(
                transaction=make_transaction(date=date(2026, 5, 18)),
                category="electricity",
            )
        ],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={
            "electricity": ReportCategory(
                name="electricity", recurrence="monthly", period_contains_payment=False
            )
        },
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    # Hidden categories are greyed (legendonly) rather than dropped, and the
    # monthly chart routes legend gestures through the shared helpers. The
    # ternary is unique to renderMonthlyChart's bar traces, so it guards the
    # specific wiring this test is named for.
    assert "hiddenCategories.has(cat) ? 'legendonly' : true" in html
    assert "plotly_legendclick" in html
    assert "legendCatFromEvent" in html


def test_donut_uses_hiddenlabels_for_unified_filter(tmp_path: Path) -> None:
    model = build_report_model(
        items=[
            make_item(
                transaction=make_transaction(date=date(2026, 5, 18)),
                category="electricity",
            )
        ],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={
            "electricity": ReportCategory(
                name="electricity", recurrence="monthly", period_contains_payment=False
            )
        },
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    # The bare word "hiddenlabels" also appears inside the bundled Plotly.js,
    # so assert on the exact wiring unique to renderDonut's layout.
    assert "hiddenlabels: Array.from(hiddenCategories)" in html


def test_timeline_uses_unified_state(tmp_path: Path) -> None:
    # The timeline now shares hiddenCategories; the old per-chart set is gone.
    model = build_report_model(
        items=[
            make_item(
                transaction=make_transaction(date=date(2026, 5, 18)),
                category="electricity",
            )
        ],
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 5, 19),
        categories_by_name={
            "electricity": ReportCategory(
                name="electricity", recurrence="monthly", period_contains_payment=False
            )
        },
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert "hiddenTimelineCats" not in html


def test_period_edge_days_renders_in_data_block(tmp_path: Path) -> None:
    items = [
        make_item(
            transaction=make_transaction(date=date(2026, 4, 30), amount=Decimal("60.00")),
            category="fee",
        ),
    ]
    cats = {
        "fee": ReportCategory(
            name="fee",
            recurrence="monthly",
            period_contains_payment=True,
            period_edge_days=2,
        ),
    }
    model = build_report_model(
        items=items,
        alerts=[],
        currency="EUR",
        generated_at=date(2026, 8, 20),
        categories_by_name=cats,
    )
    out = tmp_path / "report.html"
    render_report(model, out)
    html = out.read_text(encoding="utf-8")
    assert '"period_edge_days":2' in html or '"period_edge_days": 2' in html
