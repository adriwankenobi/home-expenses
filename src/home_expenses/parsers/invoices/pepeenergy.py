"""PepeEnergy invoice PDF parser. Operates on extracted text."""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from home_expenses.cache import file_sha256
from home_expenses.models import Invoice, InvoicePeriod

PARSER_NAME = "pepeenergy"


def matches_filename(path: Path) -> bool:
    """Return True if the file name follows PepeEnergy's invoice convention."""
    return path.name.startswith("E")


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

_AMOUNT_LINE_RE = re.compile(r"^([\d.]*\d+),(\d{2})\s*€\s*$")
_FECHA_EMISION_RE = re.compile(r"Fecha emisión:\s*(\d{2})/(\d{2})/(\d{2})")
_PERIOD_MONTH_YEAR_RE = re.compile(rf"^({_MONTH_NAMES})\s+(\d{{4}})\b", re.IGNORECASE)
_PERIOD_RANGE_RE = re.compile(
    rf"^(\d{{1,2}})\s+al\s+(\d{{1,2}})\s+de\s+({_MONTH_NAMES})\s+de\s+(\d{{4}})",
    re.IGNORECASE,
)


class InvoiceParseError(ValueError):
    """Raised when a PepeEnergy invoice text can't be parsed."""


def parse(path: Path) -> Invoice:
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    return parse_text(text, source_path=str(path), content_hash=file_sha256(path))


def parse_text(text: str, *, source_path: str, content_hash: str) -> Invoice:
    lines = text.splitlines()
    amount = _extract_amount(lines)
    invoice_date = _extract_invoice_date(text)
    period = _extract_period(lines)
    return Invoice(
        source_path=source_path,
        content_hash=content_hash,
        parser=PARSER_NAME,
        amount=amount,
        invoice_date=invoice_date,
        period=period,
    )


def _extract_amount(lines: list[str]) -> Decimal:
    for i, line in enumerate(lines):
        if line.strip() == "Total a pagar":
            if i == 0:
                raise InvoiceParseError("no line above 'Total a pagar'")
            prev = lines[i - 1].strip()
            m = _AMOUNT_LINE_RE.match(prev)
            if not m:
                raise InvoiceParseError("line above 'Total a pagar' is not an amount")
            euros = m.group(1).replace(".", "") + "." + m.group(2)
            return Decimal(euros)
    raise InvoiceParseError("'Total a pagar' label not found")


def _extract_invoice_date(text: str) -> date:
    m = _FECHA_EMISION_RE.search(text)
    if not m:
        raise InvoiceParseError("Fecha emisión not found")
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(2000 + yy, mm, dd)


def _extract_period(lines: list[str]) -> InvoicePeriod:
    for i, line in enumerate(lines):
        if line.startswith("Factura de la luz de"):
            if i + 1 >= len(lines):
                raise InvoiceParseError("no line after 'Factura de la luz de'")
            nxt = lines[i + 1].strip()
            m = _PERIOD_RANGE_RE.match(nxt)
            if m:
                d1, d2 = int(m.group(1)), int(m.group(2))
                month = _SPANISH_MONTHS[m.group(3).lower()]
                year = int(m.group(4))
                return InvoicePeriod(start=date(year, month, d1), end=date(year, month, d2))
            m = _PERIOD_MONTH_YEAR_RE.match(nxt)
            if m:
                month = _SPANISH_MONTHS[m.group(1).lower()]
                year = int(m.group(2))
                last_day = calendar.monthrange(year, month)[1]
                return InvoicePeriod(start=date(year, month, 1), end=date(year, month, last_day))
            raise InvoiceParseError("unrecognized period format on line after label")
    raise InvoiceParseError("invoice period not found")
