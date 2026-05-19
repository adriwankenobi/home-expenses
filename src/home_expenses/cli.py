"""Entry points for the home-expenses CLI."""

from __future__ import annotations

import click


@click.group()
def cli() -> None:
    """home-expenses: reconcile bank statements with invoice PDFs."""


@cli.command()
def report() -> None:
    """Build report.html."""
    click.echo("report: not yet implemented")


@cli.group()
def cache() -> None:
    """Cache management."""


@cache.command("clear")
def cache_clear() -> None:
    """Wipe the extraction cache."""
    click.echo("cache clear: not yet implemented")


if __name__ == "__main__":
    cli()
