"""Ecociudad Zaragoza (saneamiento) invoice PDF parser.

Reads the second emisor on the same Zaragoza municipal water/waste PDF that
[[aguasYBasuras]] reads from — the bill carries two simultaneous charges
(Ayuntamiento and Ecociudad). This parser extracts only the Ecociudad portion.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from home_expenses.cache import file_sha256
from home_expenses.models import Invoice, InvoicePeriod

PARSER_NAME = "ecociudad"

# Section header that introduces the Ecociudad period block on page 2.
_ECOCIUDAD_SECTION_RE = re.compile(r"CUOTA\s+FIJA\s+ECOCIUDAD", re.IGNORECASE)
# Anchor for the Ecociudad amount line — appears after "TOTAL SANEAMIENTO".
_TOTAL_SANEAMIENTO_RE = re.compile(r"TOTAL\s+SANEAMIENTO", re.IGNORECASE)
# Bare "TOTAL A PAGAR <amount>" — the `\s+\d` requirement excludes
# "TOTAL A PAGAR AYUNTAMIENTO" and "TOTAL A PAGAR E.Z." variants.
_TOTAL_A_PAGAR_RE = re.compile(
    r"TOTAL\s+A\s+PAGAR\s+([\d.]*\d+),(\d{2})",
)
_PERIODO_FACTURADO_RE = re.compile(
    r"Periodo\s+facturado\s+del\s+"
    r"(\d{1,2})-(\d{1,2})-(\d{2})\s+al\s+(\d{1,2})-(\d{1,2})-(\d{2})",
    re.IGNORECASE,
)
_FECHA_EMISION_RE = re.compile(
    r"Fecha\s+de\s+emisi[oó]n\s+(\d{1,2})/(\d{1,2})/(\d{2})\b",
    re.IGNORECASE,
)


class InvoiceParseError(ValueError):
    """Raised when an Ecociudad invoice text can't be parsed."""


def matches_filename(path: Path) -> bool:
    """Same source PDF as aguasYBasuras — Zaragoza municipal water/waste bill."""
    return path.name.startswith("AB")


def parse(path: Path) -> Invoice:
    with pdfplumber.open(path) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages[:2]]
    text = "\n".join(pages_text)
    return parse_text(text, source_path=str(path), content_hash=file_sha256(path))


def parse_text(text: str, *, source_path: str, content_hash: str) -> Invoice:
    return Invoice(
        source_path=source_path,
        content_hash=content_hash,
        parser=PARSER_NAME,
        amount=_extract_amount(text),
        invoice_date=_extract_invoice_date(text),
        period=_extract_period(text),
    )


def _extract_amount(text: str) -> Decimal:
    saneamiento = _TOTAL_SANEAMIENTO_RE.search(text)
    if not saneamiento:
        raise InvoiceParseError("'TOTAL SANEAMIENTO' line not found")
    m = _TOTAL_A_PAGAR_RE.search(text, pos=saneamiento.end())
    if not m:
        raise InvoiceParseError("'TOTAL A PAGAR' line after 'TOTAL SANEAMIENTO' not found")
    euros = m.group(1).replace(".", "") + "." + m.group(2)
    return Decimal(euros)


def _extract_invoice_date(text: str) -> date:
    m = _FECHA_EMISION_RE.search(text)
    if not m:
        raise InvoiceParseError("'Fecha de emisión' not found")
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(2000 + yy, mm, dd)


def _extract_period(text: str) -> InvoicePeriod:
    section = _ECOCIUDAD_SECTION_RE.search(text)
    if not section:
        raise InvoiceParseError("'CUOTA FIJA ECOCIUDAD' section not found")
    m = _PERIODO_FACTURADO_RE.search(text, pos=section.end())
    if not m:
        raise InvoiceParseError("'Periodo facturado' line in Ecociudad section not found")
    d1, m1, y1, d2, m2, y2 = (int(g) for g in m.groups())
    return InvoicePeriod(
        start=date(2000 + y1, m1, d1),
        end=date(2000 + y2, m2, d2),
    )
