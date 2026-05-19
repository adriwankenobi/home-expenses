"""Bank statement CSV parser (cp1252-encoded, ; separated)."""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from home_expenses.cache import file_sha256
from home_expenses.models import BankStatementFile, Transaction

_REQUIRED_COLUMNS = ("F. ejecución", "Concepto", "Importe")


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
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y").date()
    except ValueError as e:
        raise BankStatementParseError("invalid date") from e


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
        if amount >= 0:
            continue
        try:
            txn_date = _parse_date(row[idx_date])
        except BankStatementParseError as e:
            raise BankStatementParseError(f"{path}:{row_num}: {e}") from e
        description = row[idx_desc].strip()
        transactions.append(Transaction(date=txn_date, description=description, amount=-amount))

    if not transactions:
        raise BankStatementParseError(f"{path}: no debit rows found after filtering")

    dates = [t.date for t in transactions]
    return BankStatementFile(
        source_path=str(path),
        content_hash=file_sha256(path),
        date_range=(min(dates), max(dates)),
        transactions=tuple(transactions),
    )
