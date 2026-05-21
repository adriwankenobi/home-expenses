from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.invoices import get_parser, get_spec
from home_expenses.parsers.invoices.ibi import (
    InvoiceParseError,
    matches_filename,
    parse_text,
)

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "ibi.txt"


def _load_template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _parseable_template() -> str:
    """Return the template with placeholders substituted so it parses end-to-end."""
    return (
        _load_template()
        .replace("D-M-YYYY al DD-MM-YYYY", "1-1-2024 al 31-12-2024")
        .replace(
            "Hasta el DD/MM/YY XX-XXX-X X XXXXXXXXXX-XX X-XXX-XX-X-XXX XXX,XX €",
            "Hasta el 15/11/24 12-345-6 7 1234567890-12 3-456-78-9-012 183,02 €",
        )
    )


def test_extracts_amount_from_importe_under_periodo_de_pago_header() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("183.02")


def test_invoice_date_is_none() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_date is None


def test_extracts_period_with_single_digit_components() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2024, 1, 1)
    assert inv.period.end == date(2024, 12, 31)


def test_extracts_period_with_double_digit_components() -> None:
    text = (
        "Período Clave Recaudatoria Nº. de recibo Fecha límite Nº. fijo\n"
        "10-06-2024 al 30-11-2024 XX-XXXX-XX XXXXX-X 15/11/24 XXXXXXXX\n"
        "Período de pago Emisora Mod Referencia Identificación Importe\n"
        "Hasta el 15/11/24 12-345-6 7 1234567890-12 3-456-78-9-012 100,00 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2024, 6, 10)
    assert inv.period.end == date(2024, 11, 30)


def test_year_only_period_expands_to_full_year() -> None:
    text = (
        "Período\n"
        "2024\n"
        "Período de pago Emisora Mod Referencia Identificación Importe\n"
        "Hasta el 15/11/24 12-345-6 7 1234567890-12 3-456-78-9-012 100,00 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2024, 1, 1)
    assert inv.period.end == date(2024, 12, 31)


def test_period_must_be_anchored_to_periodo_header() -> None:
    # A range that's not on the line under the "Período" header
    # must not be picked up.
    text = (
        "Some unrelated text 1-1-2024 al 31-12-2024 here\n"
        "Período de pago Emisora Mod Referencia Identificación Importe\n"
        "Hasta el 15/11/24 12-345-6 7 1234567890-12 3-456-78-9-012 100,00 €\n"
    )
    with pytest.raises(InvoiceParseError, match="period"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_period_raises() -> None:
    text = (
        "Período de pago Emisora Mod Referencia Identificación Importe\n"
        "Hasta el 15/11/24 12-345-6 7 1234567890-12 3-456-78-9-012 100,00 €\n"
    )
    with pytest.raises(InvoiceParseError, match="period"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_importe_raises() -> None:
    text = (
        "Período Clave Recaudatoria Nº. de recibo Fecha límite Nº. fijo\n"
        "1-1-2024 al 31-12-2024 XX-XXXX-XX XXXXX-X 15/11/24 XXXXXXXX\n"
    )
    with pytest.raises(InvoiceParseError, match="Importe"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_extracts_amount_with_thousands_separator() -> None:
    text = (
        "Período Clave Recaudatoria Nº. de recibo Fecha límite Nº. fijo\n"
        "1-1-2024 al 31-12-2024 XX-XXXX-XX XXXXX-X 15/11/24 XXXXXXXX\n"
        "Período de pago Emisora Mod Referencia Identificación Importe\n"
        "Hasta el 15/11/24 12-345-6 7 1234567890-12 3-456-78-9-012 1.234,56 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("1234.56")


def test_importe_not_confused_with_other_money_on_page() -> None:
    # Other amount-like lines (CUOTA A INGRESAR, Cuota líquida, etc.) must
    # NOT be picked up — only the value at the end of the row directly
    # under the "Período de pago" header.
    text = (
        "Período Clave Recaudatoria Nº. de recibo Fecha límite Nº. fijo\n"
        "1-1-2024 al 31-12-2024 XX-XXXX-XX XXXXX-X 15/11/24 XXXXXXXX\n"
        "Período de pago Emisora Mod Referencia Identificación Importe\n"
        "Hasta el 15/11/24 12-345-6 7 1234567890-12 3-456-78-9-012 183,02 €\n"
        "Cuota líquida: 366,05€\n"
        "CUOTA A INGRESAR: 366,05 €\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("183.02")


def test_matches_filename_accepts_pdfs() -> None:
    assert matches_filename(Path("ibi-2024.pdf")) is True


def test_matches_filename_rejects_non_pdfs() -> None:
    assert matches_filename(Path("notes.txt")) is False


def test_registry_resolves_ibi() -> None:
    parser = get_parser("ibi")
    assert callable(parser)


def test_spec_exposes_parse_and_matches() -> None:
    spec = get_spec("ibi")
    assert callable(spec.parse)
    assert callable(spec.matches_filename)
