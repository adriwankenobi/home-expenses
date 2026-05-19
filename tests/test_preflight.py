from __future__ import annotations

from datetime import date

import pytest

from home_expenses.models import BankStatementFile
from home_expenses.preflight import (
    PreflightError,
    check_statement_overlaps,
)


def _stmt(start: date, end: date, path: str) -> BankStatementFile:
    return BankStatementFile(
        source_path=path,
        content_hash=path,
        date_range=(start, end),
        transactions=(),
    )


def test_no_overlap_passes() -> None:
    files = [
        _stmt(date(2026, 1, 1), date(2026, 1, 31), "a.csv"),
        _stmt(date(2026, 2, 1), date(2026, 2, 28), "b.csv"),
    ]
    # Should not raise.
    check_statement_overlaps(files)


def test_overlap_raises_with_filenames_and_range() -> None:
    files = [
        _stmt(date(2026, 2, 15), date(2026, 3, 15), "a.csv"),
        _stmt(date(2026, 3, 1), date(2026, 4, 1), "b.csv"),
    ]
    with pytest.raises(PreflightError) as e:
        check_statement_overlaps(files)
    msg = str(e.value)
    assert "a.csv" in msg
    assert "b.csv" in msg
    assert "2026-03-01" in msg
    assert "2026-03-15" in msg


def test_adjacent_ranges_are_not_overlapping() -> None:
    # End of one == start of next is a single shared day — treat as overlap.
    files = [
        _stmt(date(2026, 1, 1), date(2026, 1, 31), "a.csv"),
        _stmt(date(2026, 1, 31), date(2026, 2, 28), "b.csv"),
    ]
    with pytest.raises(PreflightError):
        check_statement_overlaps(files)
