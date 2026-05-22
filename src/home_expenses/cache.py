"""Hash-keyed JSON cache for parsed invoices and bank statements."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from home_expenses.models import (
    BankStatementFile,
    Invoice,
    InvoicePeriod,
    Transaction,
)

_INVOICES_FILE = "pdf-extractions.json"
_STATEMENTS_FILE = "statements.json"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _invoice_key(content_hash: str, parser: str) -> str:
    # Composite key so two parsers can extract distinct data from the same
    # physical PDF (e.g. aguasYBasuras and ecociudad share one Zaragoza bill).
    return f"{content_hash}|{parser}"


class ExtractionCache:
    """In-memory + on-disk cache of parsed invoices and statements."""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._invoices: dict[str, dict[str, Any]] = {}
        self._statements: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        inv_file = self._dir / _INVOICES_FILE
        if inv_file.exists():
            self._invoices = json.loads(inv_file.read_text(encoding="utf-8"))
        stmt_file = self._dir / _STATEMENTS_FILE
        if stmt_file.exists():
            self._statements = json.loads(stmt_file.read_text(encoding="utf-8"))

    def get_invoice(self, content_hash: str, parser: str) -> Invoice | None:
        data = self._invoices.get(_invoice_key(content_hash, parser))
        if data is None:
            return None
        raw_inv_date = data.get("invoice_date")
        return Invoice(
            source_path=data["source_path"],
            content_hash=content_hash,
            parser=data["parser"],
            amount=Decimal(data["amount"]),
            invoice_date=date.fromisoformat(raw_inv_date) if raw_inv_date else None,
            period=InvoicePeriod(
                start=date.fromisoformat(data["period_start"]),
                end=date.fromisoformat(data["period_end"]),
            ),
        )

    def put_invoice(self, invoice: Invoice) -> None:
        self._invoices[_invoice_key(invoice.content_hash, invoice.parser)] = {
            "source_path": invoice.source_path,
            "parser": invoice.parser,
            "amount": str(invoice.amount),
            "invoice_date": (
                invoice.invoice_date.isoformat() if invoice.invoice_date is not None else None
            ),
            "period_start": invoice.period.start.isoformat(),
            "period_end": invoice.period.end.isoformat(),
            "extracted_at": datetime.now(UTC).isoformat(),
        }

    def get_statement(self, content_hash: str) -> BankStatementFile | None:
        data = self._statements.get(content_hash)
        if data is None:
            return None
        txns = tuple(
            Transaction(
                date=date.fromisoformat(t["date"]),
                description=t["description"],
                amount=Decimal(t["amount"]),
            )
            for t in data["transactions"]
        )
        return BankStatementFile(
            source_path=data["source_path"],
            content_hash=content_hash,
            date_range=(
                date.fromisoformat(data["date_range"][0]),
                date.fromisoformat(data["date_range"][1]),
            ),
            transactions=txns,
        )

    def put_statement(self, stmt: BankStatementFile) -> None:
        self._statements[stmt.content_hash] = {
            "source_path": stmt.source_path,
            "date_range": [
                stmt.date_range[0].isoformat(),
                stmt.date_range[1].isoformat(),
            ],
            "transactions": [
                {
                    "date": t.date.isoformat(),
                    "description": t.description,
                    "amount": str(t.amount),
                }
                for t in stmt.transactions
            ],
        }

    def flush(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / _INVOICES_FILE).write_text(
            json.dumps(self._invoices, indent=2), encoding="utf-8"
        )
        (self._dir / _STATEMENTS_FILE).write_text(
            json.dumps(self._statements, indent=2), encoding="utf-8"
        )

    def clear(self) -> None:
        for fname in (_INVOICES_FILE, _STATEMENTS_FILE):
            p = self._dir / fname
            if p.exists():
                p.unlink()
        self._invoices = {}
        self._statements = {}
