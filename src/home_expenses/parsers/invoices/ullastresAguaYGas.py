"""Ullastres (hot water & gas consumption) invoice PDF parser."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from home_expenses.cache import file_sha256
from home_expenses.models import Invoice, InvoicePeriod

PARSER_NAME = "ullastresAguaYGas"


def matches_filename(path: Path) -> bool:
    """Accept every PDF in the folder. Tighten later if non-invoice files appear."""
    return path.suffix.lower() == ".pdf"


# Period and emission date share a single header line in the source PDF:
# "... PERIODO DE LECTURA: DD/MM/YY - DD/MM/YY DÍAS: XX FECHA EMISIÓN: DD/MM/YY".
_PERIODO_LECTURA_RE = re.compile(
    r"PERIODO\s+DE\s+LECTURA:\s*"
    r"(\d{1,2})/(\d{1,2})/(\d{2})\s*-\s*(\d{1,2})/(\d{1,2})/(\d{2})",
    re.IGNORECASE,
)
_FECHA_EMISION_RE = re.compile(
    r"FECHA\s+EMISI[ÓO]N:\s*(\d{1,2})/(\d{1,2})/(\d{2})",
    re.IGNORECASE,
)
# Amount anchored on a bare "TOTAL <amount> €" line — the detail rows above
# contain € amounts but never start with the word "TOTAL".
_TOTAL_RE = re.compile(
    r"\bTOTAL\s+([\d.]*\d+),(\d{2})\s*€",
)


class InvoiceParseError(ValueError):
    """Raised when an Ullastres invoice text can't be parsed."""


def parse(path: Path) -> Invoice:
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[0].extract_text() or ""
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
    m = _TOTAL_RE.search(text)
    if not m:
        raise InvoiceParseError("'TOTAL <amount> €' line not found")
    euros = m.group(1).replace(".", "") + "." + m.group(2)
    return Decimal(euros)


def _extract_invoice_date(text: str) -> date:
    m = _FECHA_EMISION_RE.search(text)
    if not m:
        raise InvoiceParseError("'FECHA EMISIÓN' not found")
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(2000 + yy, mm, dd)


def _extract_period(text: str) -> InvoicePeriod:
    m = _PERIODO_LECTURA_RE.search(text)
    if not m:
        raise InvoiceParseError("'PERIODO DE LECTURA' line not found")
    d1, m1, y1, d2, m2, y2 = (int(g) for g in m.groups())
    return InvoicePeriod(
        start=date(2000 + y1, m1, d1),
        end=date(2000 + y2, m2, d2),
    )
