"""Aguas y Basuras (Zaragoza water + waste) invoice PDF parser. Two pages."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from home_expenses.cache import file_sha256
from home_expenses.models import Invoice, InvoicePeriod

PARSER_NAME = "aguasYBasuras"


def matches_filename(path: Path) -> bool:
    """Return True if the file name follows Aguas y Basuras' invoice convention."""
    return path.name.startswith("AB")


# Amount sits at the end of the line anchored on "TOTAL A PAGAR AYUNTAMIENTO".
# Other "TOTAL A PAGAR..." rows on the page (E.Z., bare total) must NOT match.
_TOTAL_AYTO_RE = re.compile(
    r"TOTAL\s+A\s+PAGAR\s+AYUNTAMIENTO\s+([\d.]*\d+),(\d{2})",
)
# Period anchors: "Lectura anterior (m3) <reading> DD-MM-YY" and
# "Última lectura (m3) <reading> DD-MM-YY". Accent on Última is optional —
# pdfplumber occasionally drops it.
_LECTURA_ANTERIOR_RE = re.compile(
    r"Lectura\s+anterior\s*\(m3\)\s+\S+\s+(\d{1,2})-(\d{1,2})-(\d{2})",
    re.IGNORECASE,
)
_ULTIMA_LECTURA_RE = re.compile(
    r"[ÚU]ltima\s+lectura\s*\(m3\)\s+\S+\s+(\d{1,2})-(\d{1,2})-(\d{2})",
    re.IGNORECASE,
)
_FECHA_EMISION_RE = re.compile(
    r"Fecha\s+de\s+emisi[oó]n\s+(\d{1,2})/(\d{1,2})/(\d{2})\b",
    re.IGNORECASE,
)


class InvoiceParseError(ValueError):
    """Raised when an Aguas y Basuras invoice text can't be parsed."""


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
    m = _TOTAL_AYTO_RE.search(text)
    if not m:
        raise InvoiceParseError("'TOTAL A PAGAR AYUNTAMIENTO' line not found")
    euros = m.group(1).replace(".", "") + "." + m.group(2)
    return Decimal(euros)


def _extract_invoice_date(text: str) -> date:
    m = _FECHA_EMISION_RE.search(text)
    if not m:
        raise InvoiceParseError("'Fecha de emisión' not found")
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(2000 + yy, mm, dd)


def _extract_period(text: str) -> InvoicePeriod:
    start_m = _LECTURA_ANTERIOR_RE.search(text)
    if not start_m:
        raise InvoiceParseError("'Lectura anterior' line not found")
    end_m = _ULTIMA_LECTURA_RE.search(text)
    if not end_m:
        raise InvoiceParseError("'Última lectura' line not found")
    d1, m1, y1 = (int(g) for g in start_m.groups())
    d2, m2, y2 = (int(g) for g in end_m.groups())
    return InvoicePeriod(
        start=date(2000 + y1, m1, d1),
        end=date(2000 + y2, m2, d2),
    )
