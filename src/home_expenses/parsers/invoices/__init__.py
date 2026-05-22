"""Registry of invoice parsers, keyed by config's `invoice_parser` name."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from home_expenses.models import Invoice
from home_expenses.parsers.invoices import (
    aguasYBasuras,
    ecociudad,
    ibi,
    pepeenergy,
    pepephone,
    ullastresAguaYGas,
)

ParseFn = Callable[[Path], Invoice]
MatchesFn = Callable[[Path], bool]


@dataclass(frozen=True)
class ParserSpec:
    parse: ParseFn
    matches_filename: MatchesFn


_REGISTRY: dict[str, ParserSpec] = {
    aguasYBasuras.PARSER_NAME: ParserSpec(
        parse=aguasYBasuras.parse,
        matches_filename=aguasYBasuras.matches_filename,
    ),
    ecociudad.PARSER_NAME: ParserSpec(
        parse=ecociudad.parse,
        matches_filename=ecociudad.matches_filename,
    ),
    ibi.PARSER_NAME: ParserSpec(
        parse=ibi.parse,
        matches_filename=ibi.matches_filename,
    ),
    pepeenergy.PARSER_NAME: ParserSpec(
        parse=pepeenergy.parse,
        matches_filename=pepeenergy.matches_filename,
    ),
    pepephone.PARSER_NAME: ParserSpec(
        parse=pepephone.parse,
        matches_filename=pepephone.matches_filename,
    ),
    ullastresAguaYGas.PARSER_NAME: ParserSpec(
        parse=ullastresAguaYGas.parse,
        matches_filename=ullastresAguaYGas.matches_filename,
    ),
}


def get_parser(name: str) -> ParseFn:
    if name not in _REGISTRY:
        raise KeyError(f"unknown invoice parser: {name!r}")
    return _REGISTRY[name].parse


def get_spec(name: str) -> ParserSpec:
    if name not in _REGISTRY:
        raise KeyError(f"unknown invoice parser: {name!r}")
    return _REGISTRY[name]


def known_parsers() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
