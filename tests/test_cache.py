from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from home_expenses.cache import (
    ExtractionCache,
    file_sha256,
)
from home_expenses.models import (
    BankStatementFile,
    Invoice,
    InvoicePeriod,
    Transaction,
)


def test_file_sha256_stable(tmp_path: Path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello")
    h1 = file_sha256(p)
    h2 = file_sha256(p)
    assert h1 == h2
    assert len(h1) == 64


def test_invoice_cache_roundtrip(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    inv = Invoice(
        source_path="/tmp/x.pdf",
        content_hash="abc",
        parser="pepeenergy",
        amount=Decimal("11.11"),
        invoice_date=date(2024, 7, 15),
        period=InvoicePeriod(start=date(2024, 5, 1), end=date(2024, 5, 31)),
    )
    cache.put_invoice(inv)
    cache.flush()

    cache2 = ExtractionCache(tmp_path)
    got = cache2.get_invoice("abc", "pepeenergy")
    assert got is not None
    assert got.amount == Decimal("11.11")
    assert got.period.start == date(2024, 5, 1)


def test_statement_cache_roundtrip(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    txn = Transaction(
        date=date(2026, 5, 18),
        description="X",
        amount=Decimal("10.00"),
    )
    stmt = BankStatementFile(
        source_path="/tmp/s.csv",
        content_hash="def",
        date_range=(date(2026, 5, 1), date(2026, 5, 31)),
        transactions=(txn,),
    )
    cache.put_statement(stmt)
    cache.flush()

    cache2 = ExtractionCache(tmp_path)
    got = cache2.get_statement("def")
    assert got is not None
    assert got.transactions[0].amount == Decimal("10.00")


def test_missing_hash_returns_none(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    assert cache.get_invoice("missing", "pepeenergy") is None
    assert cache.get_statement("missing") is None


def test_same_pdf_cached_separately_per_parser(tmp_path: Path) -> None:
    # Aguas y Basuras and Ecociudad parse the same physical PDF — same SHA256
    # but different extracted amounts. The cache must keep them apart.
    cache = ExtractionCache(tmp_path)
    ayto = Invoice(
        source_path="/tmp/AB.pdf",
        content_hash="same-hash",
        parser="aguasYBasuras",
        amount=Decimal("30.00"),
        invoice_date=date(2025, 11, 12),
        period=InvoicePeriod(start=date(2025, 7, 29), end=date(2025, 11, 3)),
    )
    eco = Invoice(
        source_path="/tmp/AB.pdf",
        content_hash="same-hash",
        parser="ecociudad",
        amount=Decimal("87.34"),
        invoice_date=date(2025, 11, 12),
        period=InvoicePeriod(start=date(2025, 7, 29), end=date(2025, 11, 3)),
    )
    cache.put_invoice(ayto)
    cache.put_invoice(eco)
    cache.flush()

    cache2 = ExtractionCache(tmp_path)
    got_ayto = cache2.get_invoice("same-hash", "aguasYBasuras")
    got_eco = cache2.get_invoice("same-hash", "ecociudad")
    assert got_ayto is not None
    assert got_eco is not None
    assert got_ayto.amount == Decimal("30.00")
    assert got_eco.amount == Decimal("87.34")


def test_clear_removes_files(tmp_path: Path) -> None:
    cache = ExtractionCache(tmp_path)
    cache.put_invoice(
        Invoice(
            source_path="/tmp/x.pdf",
            content_hash="abc",
            parser="pepeenergy",
            amount=Decimal("1"),
            invoice_date=date(2024, 1, 1),
            period=InvoicePeriod(start=date(2024, 1, 1), end=date(2024, 1, 31)),
        )
    )
    cache.flush()
    cache.clear()
    assert not (tmp_path / "pdf-extractions.json").exists()
    assert not (tmp_path / "statements.json").exists()


def test_cache_dir_is_created_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "cache"
    cache = ExtractionCache(target)
    cache.put_invoice(
        Invoice(
            source_path="/tmp/x.pdf",
            content_hash="abc",
            parser="pepeenergy",
            amount=Decimal("1"),
            invoice_date=date(2024, 1, 1),
            period=InvoicePeriod(start=date(2024, 1, 1), end=date(2024, 1, 31)),
        )
    )
    cache.flush()
    assert (target / "pdf-extractions.json").exists()
    data = json.loads((target / "pdf-extractions.json").read_text())
    # Stored under a composite key that includes the parser name so two
    # parsers can extract distinct data from the same physical PDF.
    assert "abc|pepeenergy" in data
