"""Pepephone invoice PDF parser. Operates on extracted text."""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from home_expenses.cache import file_sha256
from home_expenses.models import Invoice, InvoicePeriod

PARSER_NAME = "pepephone"


def matches_filename(path: Path) -> bool:
    """Accept every PDF in the folder. Tighten later if non-invoice files appear."""
    return path.suffix.lower() == ".pdf"


_SPANISH_MONTHS: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_MONTH_NAMES = "|".join(_SPANISH_MONTHS)

_TOTAL_FACTURA_RE = re.compile(r"^Total factura\s+([\d.]*\d+),(\d{2})\s*€\s*$")
# Date label: accept optional colon, any whitespace (including line breaks)
# between the label and the date, accept either / or - as the separator,
# accept the accent as either á/ó or plain a/o defensively.
_FECHA_EMISION_RE = re.compile(
    r"Fecha de emisi[oó]n\s*:?\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
    re.IGNORECASE,
)
_NUMERO_FACTURA_RE = re.compile(r"Número de factura\s*:?\s*(\S+)")
_PERIODO_RE = re.compile(
    # Defensive against pdfplumber quirks: accept "Período"/"Periodo" with
    # optional whitespace between letters, any whitespace before/after the
    # colon, and an optional colon.
    rf"Per[ií\s]*odo\s+facturad[oa]\s*:?\s*({_MONTH_NAMES})\s+(\d{{4}})",
    re.IGNORECASE,
)


class InvoiceParseError(ValueError):
    """Raised when a Pepephone invoice text can't be parsed."""


def parse(path: Path) -> Invoice:
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    return parse_text(text, source_path=str(path), content_hash=file_sha256(path))


def parse_text(text: str, *, source_path: str, content_hash: str) -> Invoice:
    lines = text.splitlines()
    amount = _extract_amount(lines)
    invoice_date = _extract_invoice_date(text)
    invoice_id = _extract_invoice_id(text)
    period = _extract_period(text)
    return Invoice(
        source_path=source_path,
        content_hash=content_hash,
        parser=PARSER_NAME,
        amount=amount,
        invoice_id=invoice_id,
        invoice_date=invoice_date,
        period=period,
    )


def _extract_amount(lines: list[str]) -> Decimal:
    # The authoritative total lives on the "Total factura" line in the
    # fiscal-breakdown section; the value at the top of the page next to
    # "TOTAL A PAGAR:" is a summary that can be truncated/rounded.
    for line in lines:
        m = _TOTAL_FACTURA_RE.match(line.strip())
        if m:
            euros = m.group(1).replace(".", "") + "." + m.group(2)
            return Decimal(euros)
    raise InvoiceParseError("'Total factura' line not found")


def _extract_invoice_date(text: str) -> date:
    m = _FECHA_EMISION_RE.search(text)
    if not m:
        raise InvoiceParseError("Fecha de emisión not found")
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(yyyy, mm, dd)


def _extract_invoice_id(text: str) -> str:
    m = _NUMERO_FACTURA_RE.search(text)
    if not m:
        raise InvoiceParseError("'Número de factura:' not found")
    return m.group(1)


def _extract_period(text: str) -> InvoicePeriod:
    m = _PERIODO_RE.search(text)
    if not m:
        raise InvoiceParseError("'Período facturado:' not found")
    month = _SPANISH_MONTHS[m.group(1).lower()]
    year = int(m.group(2))
    last_day = calendar.monthrange(year, month)[1]
    return InvoicePeriod(start=date(year, month, 1), end=date(year, month, last_day))
