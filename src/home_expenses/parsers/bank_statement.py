"""Bank statement CSV parser (cp1252-encoded, ; separated)."""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from home_expenses.cache import file_sha256
from home_expenses.models import BankStatementFile, Transaction

_REQUIRED_COLUMNS = ("F. ejecución", "Concepto", "Importe")

# Always day-first, then month, then year. The bank exports either a padded
# four-digit-year date (18/05/2026) or an unpadded two-digit-year one (1/9/26);
# the two are mutually exclusive, so the try-order never disambiguates anything.
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y")


class BankStatementParseError(ValueError):
    """Raised when a bank statement CSV cannot be parsed."""


def _open_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1252")


def _parse_amount(raw: str) -> Decimal:
    raw = raw.strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation as e:
        raise BankStatementParseError("invalid amount") from e


def _parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise BankStatementParseError("invalid date")


def parse_bank_statement(path: Path) -> BankStatementFile:
    text = _open_text(path)
    reader = csv.reader(text.splitlines(), delimiter=";", quotechar='"')
    try:
        header = next(reader)
    except StopIteration as e:
        raise BankStatementParseError(f"{path}: empty file") from e

    for col in _REQUIRED_COLUMNS:
        if col not in header:
            raise BankStatementParseError(f"{path}: missing required column {col!r}")
    idx_date = header.index("F. ejecución")
    idx_desc = header.index("Concepto")
    idx_amount = header.index("Importe")

    transactions: list[Transaction] = []
    max_idx = max(idx_date, idx_desc, idx_amount)
    for row_num, row in enumerate(reader, start=2):
        if not row or all(c == "" for c in row):
            continue
        if len(row) <= max_idx:
            raise BankStatementParseError(
                f"{path}:{row_num}: row has fewer columns than the header"
            )
        try:
            amount = _parse_amount(row[idx_amount])
        except BankStatementParseError as e:
            raise BankStatementParseError(f"{path}:{row_num}: {e}") from e
        try:
            txn_date = _parse_date(row[idx_date])
        except BankStatementParseError as e:
            raise BankStatementParseError(f"{path}:{row_num}: {e}") from e
        description = row[idx_desc].strip()
        transactions.append(
            Transaction(
                date=txn_date,
                description=description,
                amount=abs(amount),
                is_credit=amount > 0,
            )
        )

    if not transactions:
        raise BankStatementParseError(f"{path}: no rows found")

    dates = [t.date for t in transactions]
    return BankStatementFile(
        source_path=str(path),
        content_hash=file_sha256(path),
        date_range=(min(dates), max(dates)),
        transactions=tuple(transactions),
    )
