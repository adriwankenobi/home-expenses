"""Cross-file checks that must hard-fail before any matching."""

from __future__ import annotations

from collections.abc import Iterable

from home_expenses.models import BankStatementFile


class PreflightError(RuntimeError):
    """Raised when an unrecoverable pre-flight check fails."""


def check_statement_overlaps(files: Iterable[BankStatementFile]) -> None:
    files = sorted(files, key=lambda f: f.date_range[0])
    for i, a in enumerate(files):
        for b in files[i + 1 :]:
            if b.date_range[0] > a.date_range[1]:
                break
            overlap_start = max(a.date_range[0], b.date_range[0])
            overlap_end = min(a.date_range[1], b.date_range[1])
            raise PreflightError(
                f"bank-statement date ranges overlap: "
                f"{a.source_path!r} and {b.source_path!r} "
                f"share {overlap_start.isoformat()} → {overlap_end.isoformat()}"
            )
