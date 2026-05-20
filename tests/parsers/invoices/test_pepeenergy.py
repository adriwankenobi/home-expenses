from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.invoices import get_parser, get_spec
from home_expenses.parsers.invoices.pepeenergy import (
    InvoiceParseError,
    matches_filename,
    parse_text,
)

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "pepeenergy.txt"


def _load_template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _parseable_template() -> str:
    """Return the template with placeholders substituted so it parses end-to-end."""
    return (
        _load_template()
        .replace("DD/MM/YY", "15/07/24")
        .replace("month YYYY", "mayo 2024")
        .replace("X,XX €", "1,11 €")
    )


def test_extracts_amount_above_total_a_pagar_label() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("1.11")


def test_extracts_invoice_id_from_line_after_label() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_id == "XXXXXXXXXXXX"


def test_extracts_invoice_date_from_fecha_emision() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_date == date(2024, 7, 15)


def test_extracts_month_year_period() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2024, 5, 1)
    assert inv.period.end == date(2024, 5, 31)


def test_extracts_day_range_period() -> None:
    text = (
        "Factura de la luz de Juan Ejemplo\n"
        "16 al 30 de abril de 2024 Calle Falsa\n"
        "Fecha emisión: 02/05/24\n"
        "Número de factura\n"
        "ABC123 Resumen\n"
        "5,00 €\n"
        "Total a pagar\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2024, 4, 16)
    assert inv.period.end == date(2024, 4, 30)


def test_missing_total_a_pagar_raises() -> None:
    text = "Fecha emisión: 02/05/24\nNúmero de factura\nABC\n"
    with pytest.raises(InvoiceParseError, match="Total a pagar"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_period_raises() -> None:
    text = "Fecha emisión: 02/05/24\nNúmero de factura\nABC Resumen\n5,00 €\nTotal a pagar\n"
    with pytest.raises(InvoiceParseError, match="period"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_registry_resolves_pepeenergy() -> None:
    parser = get_parser("pepeenergy")
    assert callable(parser)


def test_registry_unknown_parser_raises() -> None:
    with pytest.raises(KeyError):
        get_parser("acme")


def test_extracts_amount_with_thousands_separator() -> None:
    text = (
        "Factura de la luz de Juan Ejemplo\n"
        "mayo 2024 Calle Falsa\n"
        "Fecha emisión: 02/05/24\n"
        "Número de factura\n"
        "ABC123 Resumen\n"
        "1.234,56 €\n"
        "Total a pagar\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("1234.56")


def test_unrecognized_period_format_raises() -> None:
    text = (
        "Factura de la luz de Juan Ejemplo\n"
        "garbled period text\n"
        "Fecha emisión: 02/05/24\n"
        "Número de factura\n"
        "ABC Resumen\n"
        "1,00 €\n"
        "Total a pagar\n"
    )
    with pytest.raises(InvoiceParseError, match="unrecognized period"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_matches_filename_accepts_e_prefix() -> None:
    assert matches_filename(Path("E24PP0000258154.pdf")) is True


def test_matches_filename_rejects_non_e_prefix() -> None:
    assert matches_filename(Path("contrato_luz_512356.pdf")) is False
    assert matches_filename(Path("factura.pdf")) is False


def test_spec_exposes_parse_and_matches() -> None:
    spec = get_spec("pepeenergy")
    assert callable(spec.parse)
    assert callable(spec.matches_filename)
    assert spec.matches_filename(Path("Eanything.pdf")) is True
