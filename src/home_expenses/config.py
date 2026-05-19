"""Load and validate config.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Recurrence = Literal["monthly", "bimonthly", "quarterly", "yearly", "none"]
_VALID_RECURRENCES: frozenset[str] = frozenset(
    ("monthly", "bimonthly", "quarterly", "yearly", "none")
)


class ConfigError(ValueError):
    """Raised when config.json is structurally invalid."""


def _require_dict(value: Any, label: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: {label} must be an object")
    return value


@dataclass(frozen=True)
class Category:
    name: str
    recurrence: Recurrence
    invoice_folder: Path | None = None
    invoice_parser: str | None = None
    patterns: tuple[str, ...] = ()
    match_window_days: int | None = None


@dataclass(frozen=True)
class BankStatementsConfig:
    dir: Path
    glob: str


@dataclass(frozen=True)
class Config:
    currency: str
    bank_statements: BankStatementsConfig
    match_window_days_default: int
    cache_dir: Path
    categories: dict[str, Category] = field(default_factory=dict)
    manual_mappings: dict[str, str] = field(default_factory=dict)


def load_config(path: Path) -> Config:
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: not valid JSON: {e}") from e

    for required in (
        "currency",
        "bank_statements",
        "match_window_days_default",
        "categories",
        "manual_mappings",
    ):
        if required not in raw:
            raise ConfigError(f"{path}: missing required field '{required}'")

    bs_raw: dict[str, Any] = _require_dict(raw["bank_statements"], "bank_statements", path)
    if "dir" not in bs_raw or "glob" not in bs_raw:
        raise ConfigError(f"{path}: bank_statements.dir and .glob are required")
    bs_dir = Path(bs_raw["dir"])
    if not bs_dir.is_dir():
        raise ConfigError(f"{path}: bank_statements.dir does not exist: {bs_dir}")

    cache_dir_raw = raw.get("cache_dir")
    cache_dir = Path(cache_dir_raw) if cache_dir_raw else path.parent / "cache"

    categories: dict[str, Category] = {}
    pattern_owners: dict[str, str] = {}
    for name, c in _require_dict(raw["categories"], "categories", path).items():
        _require_dict(c, f"category '{name}'", path)
        if "recurrence" not in c:
            raise ConfigError(f"{path}: category '{name}' missing 'recurrence'")
        if c["recurrence"] not in _VALID_RECURRENCES:
            raise ConfigError(
                f"{path}: category '{name}' has invalid recurrence '{c['recurrence']}'"
            )
        invoice_folder_raw = c.get("invoice_folder")
        invoice_folder: Path | None = None
        if invoice_folder_raw is not None:
            invoice_folder = Path(invoice_folder_raw)
            if not invoice_folder.is_dir():
                raise ConfigError(
                    f"{path}: category '{name}' invoice_folder does not exist: {invoice_folder}"
                )
            if "invoice_parser" not in c:
                raise ConfigError(
                    f"{path}: category '{name}' has invoice_folder but no invoice_parser"
                )
        patterns = tuple(c.get("patterns", ()))
        for p in patterns:
            if p in pattern_owners:
                raise ConfigError(
                    f"{path}: duplicate pattern '{p}' in categories "
                    f"'{pattern_owners[p]}' and '{name}'"
                )
            pattern_owners[p] = name
        categories[name] = Category(
            name=name,
            recurrence=c["recurrence"],
            invoice_folder=invoice_folder,
            invoice_parser=c.get("invoice_parser"),
            patterns=patterns,
            match_window_days=c.get("match_window_days"),
        )

    manual_mappings: dict[str, str] = dict(
        _require_dict(raw["manual_mappings"], "manual_mappings", path)
    )
    for desc in manual_mappings:
        if desc in pattern_owners:
            raise ConfigError(
                f"{path}: manual_mapping key '{desc}' collides with pattern "
                f"in category '{pattern_owners[desc]}'"
            )

    return Config(
        currency=raw["currency"],
        bank_statements=BankStatementsConfig(dir=bs_dir, glob=bs_raw["glob"]),
        match_window_days_default=int(raw["match_window_days_default"]),
        cache_dir=cache_dir,
        categories=categories,
        manual_mappings=manual_mappings,
    )
