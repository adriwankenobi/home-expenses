from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.bank_statement import (
    BankStatementParseError,
    parse_bank_statement,
)

FIXTURE = Path(__file__).resolve().parents[2] / "templates" / "bank_sample.csv"


def test_parses_cp1252_encoded_file() -> None:
    result = parse_bank_statement(FIXTURE)
    assert result.source_path == str(FIXTURE)
    # 2 debit rows kept; the credit row is skipped
    assert len(result.transactions) == 2


def test_credit_rows_are_skipped() -> None:
    result = parse_bank_statement(FIXTURE)
    descriptions = [t.description for t in result.transactions]
    assert "ABONO NOMINA" not in descriptions


def test_amounts_are_positive_decimals() -> None:
    result = parse_bank_statement(FIXTURE)
    pepe = next(t for t in result.transactions if t.description == "RECIBO PEPE ENERGY")
    assert pepe.amount == Decimal("11.11")
    assert pepe.amount > 0


def test_dates_parsed_as_date_objects() -> None:
    result = parse_bank_statement(FIXTURE)
    pepe = next(t for t in result.transactions if t.description == "RECIBO PEPE ENERGY")
    assert pepe.date == date(2026, 5, 18)


def test_date_range_is_min_max_of_kept_rows() -> None:
    result = parse_bank_statement(FIXTURE)
    assert result.date_range == (date(2026, 5, 15), date(2026, 5, 18))


def test_missing_required_column_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text('"";"F. valor";"Concepto";"Importe";"Saldo"\n', encoding="cp1252")
    with pytest.raises(BankStatementParseError, match="F\\. ejecución"):
        parse_bank_statement(bad)


def test_invalid_date_in_row_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        '"";"F. ejecución";"F. valor";"Concepto";"Importe";"Saldo"\n'
        '"";"NOT_A_DATE";"18/05/2026";"X";"-1,00";"0"\n',
        encoding="cp1252",
    )
    with pytest.raises(BankStatementParseError, match="date"):
        parse_bank_statement(bad)


def test_invalid_amount_in_row_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        '"";"F. ejecución";"F. valor";"Concepto";"Importe";"Saldo"\n'
        '"";"18/05/2026";"18/05/2026";"X";"N/A";"0"\n',
        encoding="cp1252",
    )
    with pytest.raises(BankStatementParseError, match="invalid amount"):
        parse_bank_statement(bad)


def test_short_row_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        '"";"F. ejecución";"F. valor";"Concepto";"Importe";"Saldo"\n"";"18/05/2026"\n',
        encoding="cp1252",
    )
    with pytest.raises(BankStatementParseError, match="fewer columns"):
        parse_bank_statement(bad)
