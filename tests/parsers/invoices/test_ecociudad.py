from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.invoices import get_parser, get_spec
from home_expenses.parsers.invoices.ecociudad import (
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
    # Substitute the ecociudad-section "Periodo facturado" line (line 100 — the
    # one under "CUOTA FIJA ECOCIUDAD ZARAGOZA"). The abastecimiento and basuras
    # "Periodo facturado" placeholders elsewhere in the template share the same
    # literal "del DD-MM-YY al DD-MM-YY" placeholder string, so a global replace
    # would also substitute them. The parser must anchor on the ecociudad
    # section regardless, so substituting them all to the same value still
    # exercises the right behavior.
    text = text.replace(
        "Periodo facturado del DD-MM-YY al DD-MM-YY",
        "Periodo facturado del 29-07-25 al 03-11-25",
    )
    text = text.replace(
        "Fecha de emisión DD/MM/YY Calibre (m.m.) XX",
        "Fecha de emisión 12/11/25 Calibre (m.m.) 15",
    )
    # The plain "TOTAL A PAGAR XX,XX" placeholder on page 2 (after
    # TOTAL SANEAMIENTO) is the one we want; substitute it specifically.
    text = text.replace(
        "TOTAL A PAGAR XX,XX Importe € XX,XX",
        "TOTAL A PAGAR 87,34 Importe € 12,00",
    )
    return text


def test_extracts_amount_from_total_a_pagar_after_total_saneamiento() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("87.34")


def test_extracts_invoice_date_from_fecha_de_emision() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.invoice_date == date(2025, 11, 12)


def test_extracts_period_from_ecociudad_periodo_facturado() -> None:
    inv = parse_text(_parseable_template(), source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2025, 7, 29)
    assert inv.period.end == date(2025, 11, 3)


def test_amount_ignores_total_a_pagar_lines_before_total_saneamiento() -> None:
    # The bare "TOTAL A PAGAR 11,11" on page 1, plus the AYUNTAMIENTO and E.Z.
    # variants, must NOT be picked up — only the one after TOTAL SANEAMIENTO.
    text = (
        "TOTAL A PAGAR AYUNTAMIENTO 30,00\n"
        "TOTAL A PAGAR E.Z. 11,22\n"
        "TOTAL A PAGAR 41,22\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "CUOTA FIJA ECOCIUDAD ZARAGOZA\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "TOTAL SANEAMIENTO 9,99\n"
        "TOTAL A PAGAR 87,34 Importe € 12,00\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("87.34")


def test_amount_ignores_ayuntamiento_total_a_pagar_after_saneamiento() -> None:
    # Even after TOTAL SANEAMIENTO, "TOTAL A PAGAR AYUNTAMIENTO" must not match;
    # only the bare "TOTAL A PAGAR" should.
    text = (
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "CUOTA FIJA ECOCIUDAD ZARAGOZA\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "TOTAL SANEAMIENTO 9,99\n"
        "TOTAL A PAGAR AYUNTAMIENTO 30,00\n"
        "TOTAL A PAGAR 87,34 Importe € 12,00\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("87.34")


def test_amount_with_thousands_separator() -> None:
    text = (
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "CUOTA FIJA ECOCIUDAD ZARAGOZA\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "TOTAL SANEAMIENTO 9,99\n"
        "TOTAL A PAGAR 1.234,56 Importe € 12,00\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.amount == Decimal("1234.56")


def test_period_anchors_on_ecociudad_section_not_first_periodo_facturado() -> None:
    # Three "Periodo facturado" lines in the real PDF — abastecimiento, basuras,
    # then ecociudad. Only the one in the ecociudad section should be picked.
    text = (
        "Detalle abastecimiento\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "Recogida R.U.\n"
        "Periodo facturado del 01-02-25 al 28-02-25\n"
        "CUOTA FIJA ECOCIUDAD ZARAGOZA\n"
        "Periodo facturado del 29-07-25 al 03-11-25\n"
        "Fecha de emisión 05/11/25 Calibre (m.m.) 15\n"
        "TOTAL SANEAMIENTO 9,99\n"
        "TOTAL A PAGAR 87,34 Importe € 12,00\n"
    )
    inv = parse_text(text, source_path="/tmp/x.pdf", content_hash="h")
    assert inv.period.start == date(2025, 7, 29)
    assert inv.period.end == date(2025, 11, 3)


def test_missing_total_saneamiento_raises() -> None:
    text = (
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "CUOTA FIJA ECOCIUDAD ZARAGOZA\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "TOTAL A PAGAR 87,34 Importe € 12,00\n"
    )
    with pytest.raises(InvoiceParseError, match="TOTAL SANEAMIENTO"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_total_a_pagar_after_saneamiento_raises() -> None:
    text = (
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "CUOTA FIJA ECOCIUDAD ZARAGOZA\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "TOTAL SANEAMIENTO 9,99\n"
    )
    with pytest.raises(InvoiceParseError, match="TOTAL A PAGAR"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_ecociudad_periodo_facturado_raises() -> None:
    text = (
        "Detalle abastecimiento\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "Fecha de emisión 05/02/25 Calibre (m.m.) 15\n"
        "TOTAL SANEAMIENTO 9,99\n"
        "TOTAL A PAGAR 87,34 Importe € 12,00\n"
    )
    with pytest.raises(InvoiceParseError, match="ECOCIUDAD|Periodo facturado"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_missing_invoice_date_raises() -> None:
    text = (
        "CUOTA FIJA ECOCIUDAD ZARAGOZA\n"
        "Periodo facturado del 01-01-25 al 31-01-25\n"
        "TOTAL SANEAMIENTO 9,99\n"
        "TOTAL A PAGAR 87,34 Importe € 12,00\n"
    )
    with pytest.raises(InvoiceParseError, match="Fecha de emisi"):
        parse_text(text, source_path="/tmp/x.pdf", content_hash="h")


def test_matches_filename_accepts_ab_prefix() -> None:
    # Same source PDF as aguasYBasuras — Zaragoza municipal water/waste bill.
    assert matches_filename(Path("AB25030001.pdf")) is True


def test_matches_filename_rejects_non_ab_prefix() -> None:
    assert matches_filename(Path("ecociudad-2025-03.pdf")) is False
    assert matches_filename(Path("factura.pdf")) is False


def test_registry_resolves_ecociudad() -> None:
    parser = get_parser("ecociudad")
    assert callable(parser)


def test_spec_exposes_parse_and_matches() -> None:
    spec = get_spec("ecociudad")
    assert callable(spec.parse)
    assert callable(spec.matches_filename)
