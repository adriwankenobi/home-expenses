"""Registry of invoice parsers, keyed by config's `invoice_parser` name."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from home_expenses.models import Invoice
from home_expenses.parsers.invoices import pepeenergy

ParseFn = Callable[[Path], Invoice]

_REGISTRY: dict[str, ParseFn] = {
    pepeenergy.PARSER_NAME: pepeenergy.parse,
}


def get_parser(name: str) -> ParseFn:
    if name not in _REGISTRY:
        raise KeyError(f"unknown invoice parser: {name!r}")
    return _REGISTRY[name]


def known_parsers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
