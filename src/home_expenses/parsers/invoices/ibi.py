"""IBI (Spanish property tax) invoice PDF parser. Operates on extracted text."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from home_expenses.cache import file_sha256
from home_expenses.models import Invoice, InvoicePeriod

PARSER_NAME = "ibi"


def matches_filename(path: Path) -> bool:
    """Accept every PDF in the folder. Tighten later if non-invoice files appear."""
    return path.suffix.lower() == ".pdf"


# Labels we anchor on. Each field is laid out as a header line followed by
# its value on the next line, so we find the label and read the line below.
# "Período" appears twice on the page (the period header at the top and
# "Período de pago" further down on the payment slip). The first match wins,
# which is always the top header. "Importe" appears only on the payment-slip
# header, so anchoring on it is unambiguous.
_PERIODO = re.compile(r"\bPer[ií]odo\b", re.IGNORECASE)
_IMPORTE = re.compile(r"\bImporte\b", re.IGNORECASE)
# Period values: either an explicit range "D-M-YYYY al DD-MM-YYYY" or just
# a 4-digit year (which we expand to Jan 1 – Dec 31).
_RANGE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})\s+al\s+(\d{1,2})-(\d{1,2})-(\d{4})")
_YEAR = re.compile(r"\b(\d{4})\b")
# Importe value sits at the end of the row below the header.
_MONEY_AT_EOL = re.compile(r"([\d.]*\d+),(\d{2})\s*€\s*$")


class InvoiceParseError(ValueError):
    """Raised when an IBI invoice text can't be parsed."""


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
        invoice_date=None,
        period=_extract_period(text),
    )


def _line_after(text: str, label: re.Pattern[str]) -> str | None:
    """Return the line directly below the first line matching `label`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if label.search(line) and i + 1 < len(lines):
            return lines[i + 1]
    return None


def _extract_amount(text: str) -> Decimal:
    data = _line_after(text, _IMPORTE)
    if data is None:
        raise InvoiceParseError("'Importe' header not found")
    m = _MONEY_AT_EOL.search(data.rstrip())
    if not m:
        raise InvoiceParseError("no amount at end of line below 'Importe' header")
    euros = m.group(1).replace(".", "") + "." + m.group(2)
    return Decimal(euros)


def _extract_period(text: str) -> InvoicePeriod:
    data = _line_after(text, _PERIODO)
    if data is None:
        raise InvoiceParseError("'Período' header not found")
    rm = _RANGE.search(data)
    if rm:
        d1, m1, y1, d2, m2, y2 = (int(g) for g in rm.groups())
        return InvoicePeriod(start=date(y1, m1, d1), end=date(y2, m2, d2))
    ym = _YEAR.search(data)
    if ym:
        year = int(ym.group(1))
        return InvoicePeriod(start=date(year, 1, 1), end=date(year, 12, 31))
    raise InvoiceParseError("no period value below 'Período' header")
