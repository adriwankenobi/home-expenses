"""Entry points for the home-expenses CLI."""

from __future__ import annotations

import sys
import webbrowser
from datetime import date
from pathlib import Path

import click

from home_expenses.config import ConfigError, load_config
from home_expenses.parsers.bank_statement import BankStatementParseError
from home_expenses.parsers.invoices.pepeenergy import InvoiceParseError
from home_expenses.preflight import PreflightError
from home_expenses.runner import run_report


@click.group()
def cli() -> None:
    """home-expenses: reconcile bank statements with invoice PDFs."""


@cli.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=Path("config.json"),
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=Path("report.html"),
)
@click.option("--exit-on-alerts", is_flag=True, default=False)
@click.option(
    "--no-open",
    "no_open",
    is_flag=True,
    default=False,
    help="Skip opening the report in the default browser after generation.",
)
def report(
    config_path: Path,
    output_path: Path,
    exit_on_alerts: bool,
    no_open: bool,
) -> None:
    """Build report.html from bank statements and invoice PDFs."""
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    try:
        summary = run_report(cfg, output_path, today=date.today())
    except (PreflightError, BankStatementParseError, InvoiceParseError) as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    click.echo(
        f"Loaded {summary.statements_loaded} statements "
        f"({summary.statements_from_cache} cached, "
        f"{summary.statements_loaded - summary.statements_from_cache} parsed)."
    )
    click.echo(
        f"Extracted {summary.invoices_loaded} PDF invoices "
        f"({summary.invoices_from_cache} cached, "
        f"{summary.invoices_loaded - summary.invoices_from_cache} new)."
    )
    if summary.skipped_files:
        click.echo(
            f"Skipped {len(summary.skipped_files)} file(s) not matching the parser's "
            f"filename convention:",
            err=True,
        )
        for path in summary.skipped_files:
            click.echo(f"  - {path}", err=True)
    click.echo(f"Matched {summary.items} items. {summary.alerts} alerts.")
    click.echo(f"→ {summary.output_path}")

    if not no_open:
        webbrowser.open(summary.output_path.resolve().as_uri())

    if exit_on_alerts and summary.alerts > 0:
        sys.exit(3)


@cli.group()
def cache() -> None:
    """Cache management."""


@cache.command("clear")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=Path("config.json"),
)
def cache_clear(config_path: Path) -> None:
    """Wipe the extraction cache."""
    from home_expenses.cache import ExtractionCache

    cfg = load_config(config_path)
    ExtractionCache(cfg.cache_dir).clear()
    click.echo(f"Cache cleared at {cfg.cache_dir}.")


if __name__ == "__main__":
    cli()
