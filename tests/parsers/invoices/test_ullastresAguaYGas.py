from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.invoices import get_parser, get_spec
from home_expenses.parsers.invoices.ullastresAguaYGas import (
    InvoiceParseError,
    matches_filename,
    parse_text,
)

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "ullastresAguaYGas.txt"


def _load_template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _parseable_template() -> str:
    """Return the template with placeholders substituted so it parses end-to-end."""
    text = _load_template()
    text = text.replace(
        "PERIODO DE LECTURA: DD/MM/YY - DD/MM/YY",
        "PERIODO DE LECTURA: 01/04/25 - 30/04/25",
    )
    text = text.replace(
        "FECHA EMISIÓN: DD/MM/YY",
        "FECHA EMISIÓN: 05/05/25",
    )
    text = text.replace("TOTAL XX,XX €", "TOTAL 42,17 €")
    return text


def test_extracts_amount_from_total_line() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("42.17")


def test_extracts_invoice_date_from_fecha_emision() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_date == date(2025, 5, 5)


def test_extracts_period_from_periodo_de_lectura() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2025, 4, 1)
    assert inv.period.end == date(2025, 4, 30)


def test_amount_with_thousands_separator() -> None:
    text = (
        "PERIODO DE LECTURA: 01/04/25 - 30/04/25 DÍAS: 30 FECHA EMISIÓN: 05/05/25\n"
        "TOTAL 1.234,56 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("1234.56")


def test_amount_anchors_on_total_word_not_intermediate_lines() -> None:
    # The detail rows above the TOTAL line also contain € amounts; only the
    # bare "TOTAL <amount> €" line should be picked up.
    text = (
        "PERIODO DE LECTURA: 01/04/25 - 30/04/25 DÍAS: 30 FECHA EMISIÓN: 05/05/25\n"
        "004 Caliente - Sin Cons 0 11,11 € 22,22 € 33,33 €\n"
        "Cuota Fija - 9,99 € 9,99 €\n"
        "TOTAL 42,17 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("42.17")


def test_accepts_fecha_emision_without_accent() -> None:
    # pdfplumber sometimes drops accents; accept either form defensively.
    text = (
        "PERIODO DE LECTURA: 01/04/25 - 30/04/25 DÍAS: 30 FECHA EMISION: 05/05/25\n"
        "TOTAL 42,17 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_date == date(2025, 5, 5)


def test_missing_total_raises() -> None:
    text = (
        "PERIODO DE LECTURA: 01/04/25 - 30/04/25 DÍAS: 30 FECHA EMISIÓN: 05/05/25\n"
        "Cuota Fija - 9,99 € 9,99 €\n"
    )
    with pytest.raises(InvoiceParseError, match="TOTAL"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_periodo_de_lectura_raises() -> None:
    text = (
        "FECHA EMISIÓN: 05/05/25\n"
        "TOTAL 42,17 €\n"
    )
    with pytest.raises(InvoiceParseError, match="PERIODO DE LECTURA"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_fecha_emision_raises() -> None:
    text = (
        "PERIODO DE LECTURA: 01/04/25 - 30/04/25 DÍAS: 30\n"
        "TOTAL 42,17 €\n"
    )
    with pytest.raises(InvoiceParseError, match="FECHA EMISI"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_matches_filename_accepts_pdf() -> None:
    assert matches_filename(Path("ullastres-2025-04.pdf")) is True
    assert matches_filename(Path("anything.PDF")) is True


def test_matches_filename_rejects_non_pdf() -> None:
    assert matches_filename(Path("ullastres-2025-04.txt")) is False
    assert matches_filename(Path("invoice.docx")) is False


def test_registry_resolves_ullastresAguaYGas() -> None:
    parser = get_parser("ullastresAguaYGas")
    assert callable(parser)


def test_spec_exposes_parse_and_matches() -> None:
    spec = get_spec("ullastresAguaYGas")
    assert callable(spec.parse)
    assert callable(spec.matches_filename)
