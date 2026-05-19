"""End-to-end orchestration for `home-expenses report`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from home_expenses.cache import ExtractionCache, file_sha256
from home_expenses.config import Config
from home_expenses.matcher import match
from home_expenses.models import Alert, BankStatementFile, Invoice
from home_expenses.parsers.bank_statement import parse_bank_statement
from home_expenses.parsers.invoices import get_spec
from home_expenses.parsers.invoices.pepeenergy import InvoiceParseError
from home_expenses.preflight import check_statement_overlaps
from home_expenses.recurrence import check_recurrence
from home_expenses.report.model import build_report_model
from home_expenses.report.render import render_report


@dataclass(frozen=True)
class RunSummary:
    statements_loaded: int
    statements_from_cache: int
    invoices_loaded: int
    invoices_from_cache: int
    skipped_files: tuple[str, ...]
    items: int
    alerts: int
    output_path: Path


def run_report(config: Config, output_path: Path, today: date) -> RunSummary:
    cache = ExtractionCache(config.cache_dir)

    statements, cached_stmt_count = _load_statements(config, cache)
    check_statement_overlaps(statements)

    invoices_by_cat, cached_inv_count, skipped = _load_invoices(config, cache)

    transactions = tuple(t for s in statements for t in s.transactions)
    result = match(transactions, invoices_by_cat, config)

    full_range = _union_range(statements)
    recurrence_alerts: list[Alert] = (
        list(
            check_recurrence(
                items=result.items,
                categories=config.categories,
                statement_range=full_range,
                today=today,
            )
        )
        if full_range is not None
        else []
    )

    all_alerts = tuple(result.alerts) + tuple(recurrence_alerts)
    model = build_report_model(
        items=result.items,
        alerts=all_alerts,
        currency=config.currency,
        generated_at=today,
    )
    render_report(model, output_path)
    cache.flush()

    total_invoices = sum(len(v) for v in invoices_by_cat.values())
    return RunSummary(
        statements_loaded=len(statements),
        statements_from_cache=cached_stmt_count,
        invoices_loaded=total_invoices,
        invoices_from_cache=cached_inv_count,
        skipped_files=tuple(skipped),
        items=len(model.items),
        alerts=len(model.alerts),
        output_path=output_path,
    )


def _load_statements(config: Config, cache: ExtractionCache) -> tuple[list[BankStatementFile], int]:
    files = sorted(config.bank_statements.dir.glob(config.bank_statements.glob))
    loaded: list[BankStatementFile] = []
    from_cache = 0
    for f in files:
        h = file_sha256(f)
        hit = cache.get_statement(h)
        if hit is not None:
            loaded.append(hit)
            from_cache += 1
        else:
            parsed = parse_bank_statement(f)
            cache.put_statement(parsed)
            loaded.append(parsed)
    return loaded, from_cache


def _load_invoices(
    config: Config, cache: ExtractionCache
) -> tuple[dict[str, list[Invoice]], int, list[str]]:
    by_cat: dict[str, list[Invoice]] = {}
    from_cache = 0
    skipped: list[str] = []
    for name, cat in config.categories.items():
        if cat.invoice_folder is None or cat.invoice_parser is None:
            continue
        spec = get_spec(cat.invoice_parser)
        by_cat[name] = []
        for f in sorted(cat.invoice_folder.glob("*.pdf")):
            if not spec.matches_filename(f):
                skipped.append(str(f))
                continue
            h = file_sha256(f)
            hit = cache.get_invoice(h)
            if hit is not None:
                by_cat[name].append(hit)
                from_cache += 1
            else:
                try:
                    inv = spec.parse(f)
                except InvoiceParseError as e:
                    raise InvoiceParseError(f"{f}: {e}") from e
                cache.put_invoice(inv)
                by_cat[name].append(inv)
    return by_cat, from_cache, skipped


def _union_range(
    statements: Iterable[BankStatementFile],
) -> tuple[date, date] | None:
    statements = list(statements)
    if not statements:
        return None
    start = min(s.date_range[0] for s in statements)
    end = max(s.date_range[1] for s in statements)
    return (start, end)
