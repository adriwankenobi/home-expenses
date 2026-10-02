from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from home_expenses.parsers.bank_statement import (
    BankStatementParseError,
    parse_bank_statement,
)

FIXTURE = Path(__file__).resolve().parents[1] / "templates" / "bank_statement.csv"


def test_parses_cp1252_encoded_file() -> None:
    result = parse_bank_statement(FIXTURE)
    assert result.source_path == str(FIXTURE)
    # All 3 rows are kept; sign filtering is delegated to the matcher.
    assert len(result.transactions) == 3


def test_credit_rows_are_included() -> None:
    result = parse_bank_statement(FIXTURE)
    descriptions = [t.description for t in result.transactions]
    assert "PAYROLL CREDIT" in descriptions


def test_amounts_are_positive_decimals() -> None:
    result = parse_bank_statement(FIXTURE)
    # All amounts stored as absolute value regardless of sign in source.
    pepe = next(t for t in result.transactions if t.description == "PEPE ENERGY INVOICE")
    assert pepe.amount == Decimal("11.11")
    assert pepe.amount > 0
    abono = next(t for t in result.transactions if t.description == "PAYROLL CREDIT")
    assert abono.amount == Decimal("1500.00")
    assert abono.amount > 0


def test_dates_parsed_as_date_objects() -> None:
    result = parse_bank_statement(FIXTURE)
    pepe = next(t for t in result.transactions if t.description == "PEPE ENERGY INVOICE")
    assert pepe.date == date(2026, 5, 18)


def test_date_range_is_min_max_of_all_rows() -> None:
    result = parse_bank_statement(FIXTURE)
    # Now includes the credit row's date.
    assert result.date_range == (date(2026, 5, 2), date(2026, 5, 18))


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


SHORT_DATE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "templates" / "bank_statement_short_dates.csv"
)


def test_parses_unpadded_day_month_two_digit_year() -> None:
    result = parse_bank_statement(SHORT_DATE_FIXTURE)
    pepe = next(t for t in result.transactions if t.description == "PEPE ENERGY INVOICE")
    assert pepe.date == date(2026, 9, 1)


def test_two_digit_year_with_two_digit_month() -> None:
    result = parse_bank_statement(SHORT_DATE_FIXTURE)
    fee = next(t for t in result.transactions if t.description == "ACCOUNT MAINTENANCE FEE")
    assert fee.date == date(2026, 10, 1)


def test_ambiguous_short_date_is_day_first() -> None:
    # "3/4/26" must be 3 April, never 4 March.
    result = parse_bank_statement(SHORT_DATE_FIXTURE)
    payroll = next(t for t in result.transactions if t.description == "PAYROLL CREDIT")
    assert payroll.date == date(2026, 4, 3)


def test_both_date_formats_coexist_in_one_file() -> None:
    result = parse_bank_statement(SHORT_DATE_FIXTURE)
    hoa = next(t for t in result.transactions if t.description == "HOA FEE")
    assert hoa.date == date(2026, 5, 18)


def test_short_date_file_date_range() -> None:
    result = parse_bank_statement(SHORT_DATE_FIXTURE)
    assert result.date_range == (date(2026, 4, 3), date(2026, 10, 1))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1/9/26", date(2026, 9, 1)),
        ("01/09/26", date(2026, 9, 1)),
        ("1/9/2026", date(2026, 9, 1)),
        ("01/09/2026", date(2026, 9, 1)),
        ("31/12/26", date(2026, 12, 31)),
    ],
)
def test_accepted_date_spellings(tmp_path: Path, raw: str, expected: date) -> None:
    csv_path = tmp_path / "s.csv"
    csv_path.write_text(
        '"";"F. ejecución";"F. valor";"Concepto";"Importe";"Saldo"\n'
        f'"";"{raw}";"{raw}";"X";"-1,00";"0"\n',
        encoding="cp1252",
    )
    assert parse_bank_statement(csv_path).transactions[0].date == expected


@pytest.mark.parametrize("raw", ["2026-05-18", "18.05.2026", "13/13/26", "", "5/26"])
def test_rejected_date_spellings(tmp_path: Path, raw: str) -> None:
    csv_path = tmp_path / "s.csv"
    csv_path.write_text(
        '"";"F. ejecución";"F. valor";"Concepto";"Importe";"Saldo"\n'
        f'"";"{raw}";"{raw}";"X";"-1,00";"0"\n',
        encoding="cp1252",
    )
    with pytest.raises(BankStatementParseError, match="date"):
        parse_bank_statement(csv_path)


def test_outgoing_rows_are_not_credits() -> None:
    result = parse_bank_statement(FIXTURE)
    pepe = next(t for t in result.transactions if t.description == "PEPE ENERGY INVOICE")
    assert pepe.is_credit is False


def test_incoming_rows_are_flagged_as_credits() -> None:
    result = parse_bank_statement(FIXTURE)
    payroll = next(t for t in result.transactions if t.description == "PAYROLL CREDIT")
    assert payroll.is_credit is True
    # Amount stays a positive magnitude; the sign lives in is_credit.
    assert payroll.amount == Decimal("1500.00")


def test_zero_amount_row_is_not_a_credit(tmp_path: Path) -> None:
    csv_path = tmp_path / "s.csv"
    csv_path.write_text(
        '"";"F. ejecución";"F. valor";"Concepto";"Importe";"Saldo"\n'
        '"";"18/05/2026";"18/05/2026";"SETTLEMENT";"0,00";"0"\n',
        encoding="cp1252",
    )
    assert parse_bank_statement(csv_path).transactions[0].is_credit is False
