from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.invoices import get_parser, get_spec
from home_expenses.parsers.invoices.aguasYBasuras import (
    InvoiceParseError,
    matches_filename,
    parse_text,
)

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "aguasBasurasYEcociudad.txt"


def _load_template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _parseable_template() -> str:
    """Return the template with placeholders substituted so it parses end-to-end."""
    text = _load_template()
    # Substitute the specific data-bearing lines first so they don't collide
    # with the broad global replacements below.
    text = text.replace(
        "TOTAL A PAGAR AYUNTAMIENTO XX,XX",
        "TOTAL A PAGAR AYUNTAMIENTO 42,17",
    )
    text = text.replace(
        "Lectura anterior (m3) X DD-MM-YY",
        "Lectura anterior (m3) 123 05-03-25",
    )
    text = text.replace(
        "Última lectura (m3) X DD-MM-YY",
        "Última lectura (m3) 145 04-05-25",
    )
    text = text.replace(
        "Fecha de emisión DD/MM/YY Calibre (m.m.) XX",
        "Fecha de emisión 12/05/25 Calibre (m.m.) 15",
    )
    return text


def test_extracts_amount_from_total_a_pagar_ayuntamiento() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("42.17")


def test_extracts_invoice_date_from_fecha_de_emision() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_date == date(2025, 5, 12)


def test_extracts_period_from_lectura_lines() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2025, 3, 5)
    assert inv.period.end == date(2025, 5, 4)


def test_amount_anchors_on_ayuntamiento_not_plain_total() -> None:
    # "TOTAL A PAGAR" (without AYUNTAMIENTO) and "TOTAL A PAGAR E.Z."
    # are different totals that must NOT be picked up.
    text = (
        "Lectura anterior (m3) 100 01-01-25\n"
        "Última lectura (m3) 110 31-01-25\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "TOTAL RECIBO AYUNTAMIENTO 99,99\n"
        "TOTAL A PAGAR AYUNTAMIENTO 42,17\n"
        "TOTAL A PAGAR E.Z. 11,22\n"
        "TOTAL A PAGAR 53,39\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("42.17")


def test_amount_with_thousands_separator() -> None:
    text = (
        "Lectura anterior (m3) 100 01-01-25\n"
        "Última lectura (m3) 110 31-01-25\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "TOTAL A PAGAR AYUNTAMIENTO 1.234,56\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("1234.56")


def test_missing_total_a_pagar_ayuntamiento_raises() -> None:
    text = (
        "Lectura anterior (m3) 100 01-01-25\n"
        "Última lectura (m3) 110 31-01-25\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "TOTAL A PAGAR E.Z. 11,22\n"
    )
    with pytest.raises(InvoiceParseError, match="TOTAL A PAGAR AYUNTAMIENTO"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_period_start_raises() -> None:
    text = (
        "Última lectura (m3) 110 31-01-25\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "TOTAL A PAGAR AYUNTAMIENTO 42,17\n"
    )
    with pytest.raises(InvoiceParseError, match="Lectura anterior"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_period_end_raises() -> None:
    text = (
        "Lectura anterior (m3) 100 01-01-25\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "TOTAL A PAGAR AYUNTAMIENTO 42,17\n"
    )
    with pytest.raises(InvoiceParseError, match="Última lectura|Ultima lectura"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_invoice_date_raises() -> None:
    text = (
        "Lectura anterior (m3) 100 01-01-25\n"
        "Última lectura (m3) 110 31-01-25\n"
        "TOTAL A PAGAR AYUNTAMIENTO 42,17\n"
    )
    with pytest.raises(InvoiceParseError, match="Fecha de emisi"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_accepts_ultima_lectura_without_accent() -> None:
    # pdfplumber sometimes drops accents; accept either form defensively.
    text = (
        "Lectura anterior (m3) 100 01-01-25\n"
        "Ultima lectura (m3) 110 31-01-25\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "TOTAL A PAGAR AYUNTAMIENTO 42,17\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.end == date(2025, 1, 31)


def test_matches_filename_accepts_ab_prefix() -> None:
    assert matches_filename(Path("AB25030001.pdf")) is True


def test_matches_filename_rejects_non_ab_prefix() -> None:
    assert matches_filename(Path("aguas-2025-03.pdf")) is False
    assert matches_filename(Path("factura.pdf")) is False


def test_registry_resolves_aguasYBasuras() -> None:
    parser = get_parser("aguasYBasuras")
    assert callable(parser)


def test_spec_exposes_parse_and_matches() -> None:
    spec = get_spec("aguasYBasuras")
    assert callable(spec.parse)
    assert callable(spec.matches_filename)
