from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.invoices import get_parser, get_spec
from home_expenses.parsers.invoices.pepephone import (
    InvoiceParseError,
    matches_filename,
    parse_text,
)

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "pepephone.txt"


def _load_template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _parseable_template() -> str:
    """Return the template with placeholders substituted so it parses end-to-end."""
    return (
        _load_template()
        .replace("Número de factura: XXXXXXX", "Número de factura: PP-2026-001")
        .replace("Período facturado: Month YYYY", "Período facturado: Enero 2026")
        .replace("Fecha de emisión: DD/MM/YYYY", "Fecha de emisión: 01/02/2026")
        .replace("Total factura X,XX €", "Total factura 0,27 €")
    )


def test_extracts_amount_from_total_factura_line() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("0.27")


def test_extracts_invoice_id_inline_after_label() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_id == "PP-2026-001"


def test_extracts_invoice_date_with_four_digit_year() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_date == date(2026, 2, 1)


def test_extracts_month_year_period() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2026, 1, 1)
    assert inv.period.end == date(2026, 1, 31)


def test_missing_total_factura_raises() -> None:
    text = "Fecha de emisión: 02/05/2026\nNúmero de factura: ABC\nPeríodo facturado: Mayo 2026\n"
    with pytest.raises(InvoiceParseError, match="Total factura"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_period_raises() -> None:
    text = "Fecha de emisión: 02/05/2026\nNúmero de factura: ABC\nTotal factura 0,27 €\n"
    with pytest.raises(InvoiceParseError, match="Período"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_extracts_amount_with_thousands_separator() -> None:
    text = (
        "Fecha de emisión: 02/05/2026\n"
        "Número de factura: ABC\n"
        "Período facturado: Mayo 2026\n"
        "Total factura 1.234,56 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("1234.56")


def test_matches_filename_accepts_pdfs() -> None:
    assert matches_filename(Path("202601.pdf")) is True
    assert matches_filename(Path("factura_pepephone.pdf")) is True


def test_matches_filename_rejects_non_pdfs() -> None:
    assert matches_filename(Path("notes.txt")) is False
    assert matches_filename(Path("contract.docx")) is False


def test_registry_resolves_pepephone() -> None:
    parser = get_parser("pepephone")
    assert callable(parser)


def test_spec_exposes_parse_and_matches() -> None:
    spec = get_spec("pepephone")
    assert callable(spec.parse)
    assert callable(spec.matches_filename)
